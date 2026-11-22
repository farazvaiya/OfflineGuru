from __future__ import annotations

import pytest

from guru.education.service import EducationService
from guru.storage import Storage

from .conftest import FakeTranslator

LECTURE = [
    {"id": "s1", "seq": 1, "start_s": 0.0,
     "en": "Photosynthesis is the process by which green plants make food using sunlight."},
    {"id": "s2", "seq": 2, "start_s": 6.0,
     "en": "Chlorophyll is a green pigment that captures light energy inside the leaf."},
    {"id": "s3", "seq": 3, "start_s": 12.0,
     "en": "Plants need sunlight, water and carbon dioxide for photosynthesis."},
]


@pytest.fixture
def service(settings, tmp_path):
    storage = Storage(tmp_path / "edu.db")
    svc = EducationService(settings, storage, FakeTranslator())
    return svc, storage


def test_submit_answer_requires_approved_quiz(service):
    svc, storage = service
    sid = storage.create_session("t", "tok", "EDU001")
    student = storage.add_student(sid, "A")
    svc.storage.upsert_material(sid, "quiz", {"quiz_id": "q", "questions": []}, status="draft")
    with pytest.raises(PermissionError):
        svc.submit_answer(sid, student["id"], "q1", "answer")


def test_submit_answer_unknown_question_and_too_long(service):
    svc, storage = service
    sid = storage.create_session("t", "tok", "EDU002")
    student = storage.add_student(sid, "A")
    quiz = {
        "quiz_id": "qz",
        "questions": [{
            "id": "q1", "type": "mcq", "topic": "t",
            "question_en": "Q?", "question_bn": "প্রশ্ন?",
            "options": [{"id": "A", "en": "a", "bn": "ক"}, {"id": "B", "en": "b", "bn": "খ"}],
            "correct_option_id": "A", "answer_text_en": "a", "answer_text_bn": "ক",
            "rubric_terms": [], "explanation_en": "e", "explanation_bn": "ব্যাখ্যা",
            "evidence": {"segment_id": "s1", "seq": 1, "start_s": 0.0, "quote_en": "q"},
            "source_segment_id": "s1",
        }],
        "insufficient_evidence": False, "generated_by": "test", "source_segment_ids": ["s1"],
    }
    storage.upsert_material(sid, "quiz", quiz, status="approved")
    with pytest.raises(KeyError):
        svc.submit_answer(sid, student["id"], "ghost", "answer")
    with pytest.raises(ValueError):
        svc.submit_answer(sid, student["id"], "q1", "x" * 2001)


def test_maybe_refresh_respects_threshold_and_throttle(service):
    svc, storage = service
    sid = storage.create_session("t", "tok", "EDU003")
    # Below the auto-refresh threshold (default 8): nothing should happen.
    for i in range(3):
        storage.add_segment(sid, start_s=float(i), end_s=float(i + 1), en=LECTURE[i]["en"])
    assert svc.maybe_refresh(sid) is False
    assert storage.get_material(sid, "summary") is None

    # Cross the threshold: a draft summary is generated once…
    for i in range(3, 9):
        storage.add_segment(sid, start_s=float(i), end_s=float(i + 1),
                            en="Stomata are tiny pores on the leaf surface.")
    assert svc.maybe_refresh(sid) is True
    assert storage.get_material(sid, "summary")["status"] == "draft"

    # …and the 20 s throttle prevents regenerating immediately.
    assert svc.maybe_refresh(sid) is False


def test_generate_summary_and_quiz_persist_drafts(service):
    svc, storage = service
    sid = storage.create_session("t", "tok", "EDU004")
    for seg in LECTURE:
        storage.add_segment(sid, start_s=seg["start_s"], end_s=seg["start_s"] + 5, en=seg["en"])
    summary = svc.generate_summary(sid)
    assert summary["insufficient_content"] is False
    assert storage.get_material(sid, "summary")["status"] == "draft"

    quiz = svc.generate_quiz(sid, count=3)
    assert quiz["questions"]
    assert storage.get_material(sid, "quiz")["status"] == "draft"

    # Auto-refresh after approval must preserve the approval.
    storage.set_material_status(sid, "summary", "approved")
    svc.generate_summary(sid, auto=True)
    assert storage.get_material(sid, "summary")["status"] == "approved"


def test_student_quiz_and_feedback_scoped(service):
    svc, storage = service
    sid = storage.create_session("t", "tok", "EDU005")
    a = storage.add_student(sid, "A")
    b = storage.add_student(sid, "B")
    assert svc.student_quiz(sid) is None  # draft quizzes are invisible

    quiz = {
        "quiz_id": "qz2",
        "questions": [{
            "id": "q1", "type": "mcq", "topic": "photo",
            "question_en": "Q?", "question_bn": "প্রশ্ন?",
            "options": [{"id": "A", "en": "a", "bn": "ক"}, {"id": "B", "en": "b", "bn": "খ"}],
            "correct_option_id": "A", "answer_text_en": "a", "answer_text_bn": "ক",
            "rubric_terms": [], "explanation_en": "e", "explanation_bn": "ব্যাখ্যা",
            "evidence": {"segment_id": "s1", "seq": 1, "start_s": 0.0, "quote_en": "q"},
            "source_segment_id": "s1",
        }],
        "insufficient_evidence": False, "generated_by": "test", "source_segment_ids": ["s1"],
    }
    storage.upsert_material(sid, "quiz", quiz, status="approved")
    assert svc.student_quiz(sid)["questions"][0]["id"] == "q1"

    result = svc.submit_answer(sid, a["id"], "q1", "A")
    assert result["is_correct"] is True
    fb_a = svc.student_feedback(sid, a["id"])
    fb_b = svc.student_feedback(sid, b["id"])
    assert len(fb_a["answers"]) == 1
    assert fb_b["answers"] == []  # B submitted nothing and sees nothing of A's
