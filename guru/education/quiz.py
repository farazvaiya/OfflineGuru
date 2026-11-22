"""Grounded quiz generation from the stored lecture transcript.

Every question is produced from a specific transcript sentence and carries that
sentence as evidence.  If the transcript does not contain enough support for a
question type, fewer questions are generated — the generator never invents
content to hit a requested count.

Question stems are bilingual templates; only the lecture content (definitions,
options, quotes) is machine-translated.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Sequence
from typing import Any, Protocol

from ..translate import Translation
from .transcript_utils import (
    STOPWORDS,
    Sentence,
    content_words,
    contrasting_terms,
    extract_numbers,
    key_terms,
    parse_definition,
    split_sentences,
)

log = logging.getLogger(__name__)


class TranslatorLike(Protocol):
    def translate_batch(self, texts: list[str], beam_size: int | None = None) -> list[Translation]: ...


def _translate_map(translator: TranslatorLike, texts: list[str]) -> dict[str, str]:
    """Translate a unique set of strings once, tolerating failures."""
    uniq = [t for t in dict.fromkeys(texts) if t]
    if not uniq:
        return {}
    try:
        results = translator.translate_batch(uniq, beam_size=4)
    except Exception:  # pragma: no cover - defensive
        log.exception("quiz translation failed")
        return {t: "" for t in uniq}
    return {src: (res.text or "") for src, res in zip(uniq, results)}


def _blank(text: str, term: str) -> str:
    return re.sub(re.escape(term), "____", text, count=1, flags=re.IGNORECASE)


def _mcq_options(correct: str, distractors: list[str]) -> list[str]:
    opts = [correct] + distractors[:3]
    # Stable, deterministic order (alphabetical) so regenerating is reproducible.
    return sorted(dict.fromkeys(opts), key=lambda s: s.lower())


ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)


def clean_term(term: str) -> str:
    """Normalise a definition term into something usable in a question."""
    t = re.sub(r"\s+", " ", term).strip(" .,;:")
    t = ARTICLE_RE.sub("", t).strip()
    if not (3 <= len(t) <= 40):
        return ""
    words = t.split()
    if len(words) > 4:
        return ""
    if all(w.lower() in STOPWORDS for w in words):
        return ""
    return t


def _term_pool(segments: Sequence[dict[str, Any]], sentences: Sequence[Sentence],
               defined: Sequence[str]) -> list[str]:
    """Candidate distractors: defined terms first, then salient single words."""
    pool = [t for t in dict.fromkeys(defined) if t]
    for candidate in key_terms(segments, limit=20):
        cleaned = clean_term(candidate)
        if cleaned and len(cleaned.split()) == 1:
            pool.append(cleaned)
    for s in sentences:
        for word in _capitalised(s.text):
            cleaned = clean_term(word)
            if cleaned and len(cleaned.split()) == 1:
                pool.append(cleaned)
    return list(dict.fromkeys(pool))


def build_quiz(
    segments: Sequence[dict[str, Any]],
    translator: TranslatorLike,
    count: int = 5,
    include_short_answer: bool = True,
    min_evidence_chars: int = 25,
    passages: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a quiz from the transcript, optionally mixed with slide material.

    ``passages`` are retrieval hits from uploaded documents; any question built
    from one carries ``evidence.source = {document, page}``.  Questions are only
    produced where the stored text supports them, so a requested count is never
    padded with invented facts.
    """
    usable = [s for s in segments if len((s.get("en") or "").strip()) >= min_evidence_chars]
    sentences: list[Sentence] = split_sentences(usable)

    # First pass: collect definitional statements. Their terms make far better
    # distractors than arbitrary words from the transcript (which produced
    # nonsense options such as "called" in early testing).
    definitions: list[tuple[Sentence, str, str]] = []
    for s in sentences:
        parsed = parse_definition(s.text)
        if not parsed:
            continue
        term = clean_term(parsed[0])
        definition = parsed[1].strip()
        if term and len(definition) >= 12:
            definitions.append((s, term, definition))
    defined_terms = [t for _, t, _ in definitions]
    pool = _term_pool(usable, sentences, defined_terms)

    # Slide terms also make legitimate distractors, and are needed as a pool when
    # there is no transcript at all (e.g. a quiz prepared from the deck alone).
    # A retrieval chunk can hold several sentences; each is considered on its
    # own, otherwise a text file with three definitions yields only one term.
    slide_sentences = [(p, s) for p in (passages or ()) for s in _passage_sentences(p)]
    slide_pool: list[str] = []
    for _passage, sentence in slide_sentences:
        parsed = parse_definition(sentence)
        term = clean_term(parsed[0]) if parsed else ""
        if term:
            slide_pool.append(term)
    pool = list(dict.fromkeys(pool + slide_pool))

    document_candidates: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    used_terms: set[str] = set()
    for passage, sentence in slide_sentences:
        candidate = _passage_question(passage, sentence, pool, min_evidence_chars)
        if candidate and candidate["topic"] not in used_terms:
            document_candidates.append(candidate)
            used_terms.add(candidate["topic"])

    # 1) Definition-based MCQ (term recall) and 2) short answer (explanation).
    for s, term, definition in definitions:
        if term.lower() in used_terms:
            continue
        distractors = contrasting_terms(term, pool, k=3)
        if len(distractors) >= 2:
            candidates.append(_definition_mcq(s, term, definition, distractors))
            used_terms.add(term.lower())
        if include_short_answer and 1 <= len(term.split()) <= 3:
            candidates.append(_definition_short(s, term, definition))
            used_terms.add(term.lower())

    # 3) Numeric cloze (checks listening for quantities).
    all_numbers = [n for s in sentences for n in extract_numbers(s.text)]
    for s in sentences:
        nums = extract_numbers(s.text)
        if len(nums) != 1:
            continue
        answer = nums[0]
        others = [n for n in dict.fromkeys(all_numbers) if n != answer][:3]
        if len(others) < 2:
            continue
        blanked = s.text.replace(answer, "____", 1)
        if "____" not in blanked:
            continue
        candidates.append(_numeric_mcq(s, blanked, answer, others))

    if document_candidates:
        mixed: list[dict[str, Any]] = []
        for i in range(max(len(candidates), len(document_candidates))):
            if i < len(candidates):
                mixed.append(candidates[i])
            if i < len(document_candidates):
                mixed.append(document_candidates[i])
        candidates = mixed

    if not candidates:
        return {
            "quiz_id": uuid.uuid4().hex[:8],
            "questions": [],
            "insufficient_evidence": True,
            "note": "Neither the transcript nor the uploaded materials contain enough "
                    "structured facts for a quiz yet.",
            "source_segment_ids": [],
            "source_documents": [],
            "generated_by": "template-v1",
        }

    # Deterministic mix: alternate definition MCQ / short answer / numeric.
    selected: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    by_type: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        by_type.setdefault(c["type"], []).append(c)
    order = ["mcq", "short", "mcq", "mcq", "short"]
    idx = 0
    while len(selected) < count and idx < count * 6:
        want = order[idx % len(order)]
        pool = by_type.get(want) or [c for cs in by_type.values() for c in cs]
        if not pool:
            break
        cand = pool.pop(0)
        key = (cand["source_segment_id"], cand["type"], cand["question_en"][:60])
        if key not in seen_keys:
            seen_keys.add(key)
            selected.append(cand)
        idx += 1
    if not selected:
        selected = candidates[:count]

    # One translation pass for everything the templates do not already cover.
    strings: list[str] = []
    for q in selected:
        strings.append(q["question_en"])
        strings.extend(o["en"] for o in q.get("options", []))
        strings.append(q["answer_text_en"])
        strings.extend(q.get("rubric_terms", []))
        strings.append(q["evidence"]["quote_en"])
        if q.get("blank_sentence_en"):
            strings.append(q["blank_sentence_en"])
    tmap = _translate_map(translator, strings)

    for i, q in enumerate(selected, start=1):
        q["id"] = f"q{i}"
        q.pop("blank_sentence_en", None)
        q["question_bn"] = tmap.get(q["question_en"], "") or q["question_en"]
        for opt in q.get("options", []):
            opt["bn"] = tmap.get(opt["en"], "")
        q["answer_text_bn"] = tmap.get(q["answer_text_en"], "")
        q["rubric_terms"] = q.get("rubric_terms", [])
        q["rubric_terms_bn"] = [tmap.get(t, "") for t in q["rubric_terms"]]
        quote_bn = tmap.get(q["evidence"]["quote_en"], "")
        q["explanation_en"] = f"The lecture says: “{q['evidence']['quote_en']}”"
        q["explanation_bn"] = (
            f"শিক্ষকের বক্তব্য: “{quote_bn}”" if quote_bn else q["explanation_en"]
        )

    return {
        "quiz_id": uuid.uuid4().hex[:8],
        "questions": selected,
        "insufficient_evidence": len(selected) < count,
        "requested_count": count,
        "generated_by": "template-v1",
        "source_segment_ids": sorted(
            {q["source_segment_id"] for q in selected if q.get("source_segment_id")}
        ),
        "source_documents": _source_documents(selected),
    }


def _source_documents(selected: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Document pages actually cited by the selected questions."""
    citations: list[dict[str, Any]] = []
    for q in selected:
        source = q.get("evidence", {}).get("source")
        if source and source not in citations:
            citations.append(source)
    return citations


def _passage_sentences(passage: dict[str, Any]) -> list[str]:
    """Sentences of a retrieved chunk, so each can be cited on its own page."""
    text = " ".join(str(passage.get("text") or "").split())
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) >= 20]


def _passage_question(passage: dict[str, Any], text: str, pool: list[str],
                      min_chars: int) -> dict[str, Any] | None:
    """One definitional MCQ from a retrieved sentence, with document and page."""
    document = str(passage.get("document") or "")
    page = int(passage.get("page") or 0)
    if len(text) < min_chars or not document or page < 1:
        return None
    parsed = parse_definition(text)
    if not parsed:
        return None
    term = clean_term(parsed[0])
    if not term:
        return None
    distractors = contrasting_terms(term, pool, k=3)
    if len(distractors) < 2:
        return None
    blanked = _blank(text, term)
    if "____" not in blanked:
        return None
    options = _mcq_options(term, distractors)
    return {
        "type": "mcq",
        "topic": term.lower(),
        "question_en": f"According to “{document}” page {page}: {blanked}",
        "blank_sentence_en": blanked,
        "options": [{"id": chr(65 + i), "en": o} for i, o in enumerate(options)],
        "correct_option_id": chr(65 + options.index(term)),
        "answer_text_en": term,
        "rubric_terms": [],
        "evidence": {
            "segment_id": None,
            "seq": 0,
            "start_s": None,
            "quote_en": text,
            "source": {"document": document, "page": page},
        },
        "source_segment_id": None,
        "source_document": document,
        "source_page": page,
        "difficulty": "recall",
    }


def _capitalised(text: str) -> list[str]:
    return [w for w in re.findall(r"\b[A-Z][a-z]{3,}\b", text)]


def _definition_mcq(s: Sentence, term: str, definition: str, distractors: list[str]) -> dict[str, Any]:
    # ``definition`` is kept for the caller's evidence link; the option set is
    # built from the term and other defined terms.
    blanked = _blank(s.text, term)
    options = _mcq_options(term, distractors)
    return {
        "type": "mcq",
        "topic": term.lower(),
        "question_en": f"Which term completes this statement from the lecture? “{blanked}”",
        "blank_sentence_en": blanked,
        "options": [{"id": chr(65 + i), "en": o} for i, o in enumerate(options)],
        "correct_option_id": chr(65 + options.index(term)),
        "answer_text_en": term,
        "rubric_terms": [],
        "evidence": s.evidence.to_dict(),
        "source_segment_id": s.segment_id,
        "difficulty": "recall",
    }


def _definition_short(s: Sentence, term: str, definition: str) -> dict[str, Any]:
    rubric = [w for w in dict.fromkeys(content_words(definition))][:6]
    return {
        "type": "short",
        "topic": term.lower(),
        "question_en": f"Explain in your own words: what does “{term}” mean in this lecture?",
        "options": [],
        "correct_option_id": None,
        "answer_text_en": definition,
        "rubric_terms": rubric,
        "evidence": s.evidence.to_dict(),
        "source_segment_id": s.segment_id,
        "difficulty": "understanding",
    }


def _numeric_mcq(s: Sentence, blanked: str, answer: str, others: list[str]) -> dict[str, Any]:
    options = _mcq_options(answer, others)
    return {
        "type": "mcq",
        "topic": "quantity",
        "question_en": f"According to the lecture, fill in the blank: “{blanked}”",
        "blank_sentence_en": blanked,
        "options": [{"id": chr(65 + i), "en": o} for i, o in enumerate(options)],
        "correct_option_id": chr(65 + options.index(answer)),
        "answer_text_en": answer,
        "rubric_terms": [],
        "evidence": s.evidence.to_dict(),
        "source_segment_id": s.segment_id,
        "difficulty": "recall",
    }


def public_quiz(quiz: dict[str, Any], reveal: bool = False) -> dict[str, Any]:
    """Strip answer keys before a quiz is shown to students.

    ``reveal`` is used only when returning feedback for an already-graded answer.
    """
    questions = []
    for q in quiz.get("questions", []):
        out = {
            "id": q["id"],
            "type": q["type"],
            "topic": q.get("topic", ""),
            "question_en": q["question_en"],
            "question_bn": q.get("question_bn", ""),
            "options": [{"id": o["id"], "en": o["en"], "bn": o.get("bn", "")} for o in q.get("options", [])],
            "difficulty": q.get("difficulty", "recall"),
            "evidence": {
                "start_s": q.get("evidence", {}).get("start_s", 0),
                "source": q.get("evidence", {}).get("source"),
            },
        }
        if reveal:
            out["correct_option_id"] = q.get("correct_option_id")
            out["answer_text_en"] = q.get("answer_text_en")
            out["answer_text_bn"] = q.get("answer_text_bn", "")
            out["explanation_bn"] = q.get("explanation_bn", "")
        questions.append(out)
    return {
        "quiz_id": quiz.get("quiz_id"),
        "questions": questions,
        "insufficient_evidence": quiz.get("insufficient_evidence", False),
    }
