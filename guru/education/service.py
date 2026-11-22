"""Education workflows: summary drafts, quiz drafts, grading and feedback.

All generation happens off the event loop (worker thread) and never blocks the
live translation pipeline.  Drafts are stored with ``status='draft'``; students
only ever see ``approved`` materials unless the teacher explicitly previews them.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from ..config import Settings
from ..storage import Storage
from .documents import search_chunks
from .grading import grade, study_recommendations
from .quiz import build_quiz, public_quiz
from .summary import build_summary
from .transcript_utils import key_terms

log = logging.getLogger(__name__)


class EducationService:
    def __init__(self, settings: Settings, storage: Storage, translator: Any) -> None:
        self.settings = settings
        self.storage = storage
        self.translator = translator
        self._lock = threading.Lock()
        self._last_generated_count: dict[str, int] = {}
        self._last_attempt: dict[str, float] = {}
        self.last_error: str | None = None

    # -- generation ------------------------------------------------------
    def _passages(self, session_id: str, k: int = 6) -> list[dict[str, Any]]:
        """Retrieve the uploaded pages most relevant to what is being taught now.

        The query is the current key terms of the transcript, so a quiz generated
        after a lecture cites the slides that actually match it.  Deterministic:
        the same material and transcript always yield the same citations.
        """
        chunks = self.storage.document_chunks(session_id)
        if not chunks:
            return []
        segments = self.storage.list_segments(session_id)
        query = " ".join(key_terms(segments, limit=12))
        hits = search_chunks(query, chunks, k=k) if query else []
        if hits:
            return hits
        # No usable transcript query yet (a quiz prepared from the deck before
        # class, or nothing matched): use the opening pages, still with their real
        # page numbers, rather than silently grounding the draft on nothing.
        return [
            {"document": c["document"], "document_id": c["document_id"], "page": c["page"],
             "chunk_id": c["id"], "score": 0.0, "text": c["text"]}
            for c in chunks[:k]
        ]

    def search_materials(self, session_id: str, query: str, k: int = 5) -> list[dict[str, Any]]:
        return search_chunks(query, self.storage.document_chunks(session_id), k=k)

    def maybe_refresh(self, session_id: str) -> bool:
        """Generate a draft summary when enough new segments have arrived."""
        cfg = self.settings.education
        count = self.storage.segment_count(session_id)
        previous = self._last_generated_count.get(session_id, 0)
        if count - previous < cfg.auto_refresh_every_n_finals:
            return False
        # Avoid hot-looping when generation fails or there is nothing new.
        if time.time() - self._last_attempt.get(session_id, 0) < 20:
            return False
        self._last_attempt[session_id] = time.time()
        try:
            self.generate_summary(session_id, auto=True)
            return True
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("auto summary failed")
            self.last_error = str(exc)
            return False

    def generate_summary(self, session_id: str, auto: bool = False,
                         use_documents: bool = True) -> dict[str, Any]:
        segments = self.storage.list_segments(session_id)
        session = self.storage.get_session(session_id) or {}
        passages = self._passages(session_id) if use_documents else []
        with self._lock:
            summary = build_summary(
                segments,
                self.translator,
                max_sentences=self.settings.education.summary_max_sentences,
                session_title=session.get("title", "Class lecture"),
                passages=passages,
            )
        # Auto-drafts keep the previous approval state if the teacher already
        # approved an earlier summary; manual edits are preserved by the API.
        existing = self.storage.get_material(session_id, "summary")
        status = "approved" if existing and existing["status"] == "approved" and auto else "draft"
        self.storage.upsert_material(session_id, "summary", summary, status=status)
        self._last_generated_count[session_id] = len(segments)
        return summary

    def generate_quiz(self, session_id: str, count: int | None = None,
                      include_short: bool = True,
                      use_documents: bool = True) -> dict[str, Any]:
        segments = self.storage.list_segments(session_id)
        passages = self._passages(session_id) if use_documents else []
        with self._lock:
            quiz = build_quiz(
                segments,
                self.translator,
                count=count or self.settings.education.quiz_default_count,
                include_short_answer=include_short,
                min_evidence_chars=self.settings.education.min_evidence_chars,
                passages=passages,
            )
        self.storage.upsert_material(session_id, "quiz", quiz, status="draft", topic="quiz")
        return quiz

    # -- grading ---------------------------------------------------------
    def submit_answer(self, session_id: str, student_id: str, question_id: str,
                      answer: str) -> dict[str, Any]:
        quiz = self.storage.get_material(session_id, "quiz")
        if not quiz or quiz["status"] != "approved":
            raise PermissionError("Quiz is not open for students yet.")
        content = quiz["content"]
        question = next((q for q in content.get("questions", []) if q["id"] == question_id), None)
        if question is None:
            raise KeyError("Unknown question")
        if len(answer) > 2000:
            raise ValueError("Answer too long")

        result = grade(question, answer)
        self.storage.save_answer(
            session_id=session_id,
            quiz_id=content["quiz_id"],
            student_id=student_id,
            question_id=question_id,
            answer=answer[:2000],
            is_correct=result.is_correct,
            score=result.score,
            feedback=result.feedback_en,
            needs_review=result.needs_review,
        )
        if result.is_correct is not None:
            self.storage.bump_topic(session_id, student_id, question.get("topic", "general"), result.is_correct)

        payload = result.to_dict()
        payload.update(
            {
                "question_id": question_id,
                "correct_option_id": question.get("correct_option_id"),
                "answer_text_en": question.get("answer_text_en", ""),
                "answer_text_bn": question.get("answer_text_bn", ""),
                "explanation_bn": question.get("explanation_bn", ""),
                "evidence": question.get("evidence", {}),
                "topic": question.get("topic", ""),
            }
        )
        return payload

    def student_quiz(self, session_id: str) -> dict[str, Any] | None:
        quiz = self.storage.get_material(session_id, "quiz")
        if not quiz or quiz["status"] != "approved":
            return None
        return public_quiz(quiz["content"])

    def student_feedback(self, session_id: str, student_id: str) -> dict[str, Any]:
        answers = self.storage.list_answers(session_id, student_id)
        stats = self.storage.topic_stats(session_id, student_id)
        return {
            "answers": [
                {
                    "question_id": a["question_id"],
                    "is_correct": a["is_correct"],
                    "score": a["score"],
                    "needs_review": bool(a["needs_review"]),
                }
                for a in answers
            ],
            "topics": stats,
            "recommendations": study_recommendations(stats),
            "note": "Topic hints appear only after at least 3 answers on that topic.",
        }
