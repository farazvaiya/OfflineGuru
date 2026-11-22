from __future__ import annotations

from guru.education.grading import grade, grade_mcq, grade_short, study_recommendations
from guru.education.quiz import build_quiz, public_quiz
from guru.education.summary import build_summary
from guru.education.transcript_utils import (
    format_timestamp,
    key_terms,
    parse_definition,
    select_summary_sentences,
)

from .conftest import FakeTranslator

SEGMENTS = [
    {"id": "s1", "seq": 1, "start_s": 0.0,
     "en": "Photosynthesis is the process by which green plants make food using sunlight."},
    {"id": "s2", "seq": 2, "start_s": 6.0,
     "en": "Chlorophyll is a green pigment that captures light energy inside the leaf."},
    {"id": "s3", "seq": 3, "start_s": 12.0,
     "en": "The leaves are called the food factory of the plant."},
    {"id": "s4", "seq": 4, "start_s": 18.0,
     "en": "Plants need sunlight, water and carbon dioxide for photosynthesis."},
    {"id": "s5", "seq": 5, "start_s": 24.0,
     "en": "Water boils at 100 degrees Celsius at sea level."},
    {"id": "s6", "seq": 6, "start_s": 30.0,
     "en": "Photosynthesis produces oxygen as a byproduct of the reaction."},
]


def test_parse_definition_patterns():
    assert parse_definition("Photosynthesis is the process by which plants make food")[0] == "Photosynthesis"
    term, definition = parse_definition("The leaves are called the food factory of the plant")
    assert term.lower().startswith("the leaves")
    assert "food factory" in definition
    assert parse_definition("Plants need sunlight and water") is None


def test_key_terms_are_contentful():
    terms = key_terms(SEGMENTS)
    assert terms
    assert "photosynthesis" in terms
    assert all(t not in {"the", "and", "is"} for t in terms)


def test_summary_is_grounded_and_bilingual():
    tr = FakeTranslator()
    summary = build_summary(SEGMENTS, tr, max_sentences=4, session_title="Biology")
    assert summary["insufficient_content"] is False
    assert 1 <= len(summary["summary_bn"]) <= 4
    assert len(summary["summary_en"]) == len(summary["summary_bn"])
    assert all(item["text"].startswith("বাংলা: ") for item in summary["summary_bn"])
    ids = {s["id"] for s in SEGMENTS}
    assert set(summary["evidence_segment_ids"]) <= ids
    assert summary["key_terms"]
    # Chronological order is preserved for readability.
    starts = [item["start_s"] for item in summary["summary_en"]]
    assert starts == sorted(starts)


def test_summary_without_transcript_is_honest():
    summary = build_summary([], FakeTranslator())
    assert summary["insufficient_content"] is True
    assert summary["summary_bn"] == []


def test_summary_sentence_selection_is_deterministic():
    a = [s.text for s in select_summary_sentences(SEGMENTS, 3)]
    b = [s.text for s in select_summary_sentences(SEGMENTS, 3)]
    assert a == b


def test_quiz_questions_have_evidence_and_keys():
    tr = FakeTranslator()
    quiz = build_quiz(SEGMENTS, tr, count=5)
    assert quiz["questions"], "expected at least one question"
    ids = {s["id"] for s in SEGMENTS}
    for q in quiz["questions"]:
        assert q["evidence"]["segment_id"] in ids
        assert q["question_en"]
        assert q["question_bn"]
        assert q["source_segment_id"] in ids
        if q["type"] == "mcq":
            assert q["correct_option_id"] in [o["id"] for o in q["options"]]
            assert len(q["options"]) >= 3


def test_public_quiz_hides_answer_keys():
    quiz = build_quiz(SEGMENTS, FakeTranslator(), count=3)
    public = public_quiz(quiz)
    assert public["questions"]
    for q in public["questions"]:
        assert "correct_option_id" not in q
        assert "answer_text_en" not in q
        assert "rubric_terms" not in q
    revealed = public_quiz(quiz, reveal=True)
    assert any("correct_option_id" in q for q in revealed["questions"])


def test_quiz_without_content_reports_insufficient_evidence():
    quiz = build_quiz([], FakeTranslator(), count=5)
    assert quiz["questions"] == []
    assert quiz["insufficient_evidence"] is True


def test_mcq_grading():
    q = {"type": "mcq", "correct_option_id": "B", "answer_text_en": "chlorophyll",
         "answer_text_bn": "ক্লোরোফিল", "explanation_bn": "ব্যাখ্যা", "evidence": {"start_s": 65}}
    ok = grade_mcq(q, "b")
    assert ok.is_correct is True and ok.score == 1.0
    bad = grade_mcq(q, "A")
    assert bad.is_correct is False
    assert "1:05" in bad.feedback_en


def test_short_answer_grading_bands():
    q = {"type": "short", "rubric_terms": ["process", "green", "plants", "sunlight"],
         "answer_text_en": "process by which green plants use sunlight",
         "answer_text_bn": "সালোকসংশ্লেষণ", "evidence": {"start_s": 0}}
    good = grade_short(q, "It is the process where green plants use sunlight to make food.")
    assert good.is_correct is True and good.score >= 0.7

    partial = grade_short(q, "Plants use sunlight and water.")
    assert partial.is_correct is None and partial.needs_review is True

    too_short = grade_short(q, "plants")
    assert too_short.needs_review is True and too_short.is_correct is None

    empty = grade_short(q, "   ")
    assert empty.needs_review is True and empty.score == 0.0


def test_feedback_never_judges_ability():
    q = {"type": "short", "rubric_terms": ["a", "b"], "answer_text_en": "x", "evidence": {"start_s": 0}}
    g = grade(q, "something unrelated")
    blob = (g.feedback_bn + g.feedback_en).lower()
    for word in ("stupid", "weak student", "learning disability", "intelligence"):
        assert word not in blob


def test_study_recommendations_need_enough_evidence():
    stats = [
        {"topic": "photosynthesis", "attempts": 2, "correct": 0},
        {"topic": "respiration", "attempts": 4, "correct": 1},
        {"topic": "osmosis", "attempts": 4, "correct": 4},
    ]
    recs = study_recommendations(stats)
    assert [r["topic"] for r in recs] == ["respiration"]


def test_format_timestamp():
    assert format_timestamp(65.4) == "1:05"
    assert format_timestamp(0) == "0:00"


BILINGUAL_RUBRIC = {
    "type": "short",
    "rubric_terms": ["process", "green", "plants", "sunlight"],
    "rubric_terms_bn": ["প্রক্রিয়া", "সবুজ", "উদ্ভিদ", "সূর্যালোক"],
    "answer_text_en": "the process by which green plants make food using sunlight",
    "answer_text_bn": "যে প্রক্রিয়ায় সবুজ উদ্ভিদ সূর্যালোকে খাদ্য তৈরি করে",
    "evidence": {"start_s": 12},
}


def test_bangla_answer_matches_the_bangla_rubric():
    full = grade_short(BILINGUAL_RUBRIC,
                       "এই প্রক্রিয়ায় সবুজ উদ্ভিদ সূর্যালোক ব্যবহার করে খাদ্য বানায়।")
    assert full.is_correct is True and full.score >= 0.7

    partial = grade_short(BILINGUAL_RUBRIC, "সবুজ উদ্ভিদ খাদ্য তৈরি করে।")
    assert partial.is_correct is None and partial.needs_review is True


def test_bangla_answer_ignores_punctuation_and_digits():
    plain = grade_short(BILINGUAL_RUBRIC, "প্রক্রিয়া সবুজ উদ্ভিদ সূর্যালোক")
    noisy = grade_short(BILINGUAL_RUBRIC, "  প্রক্রিয়া!!! সবুজ, উদ্ভিদ?? সূর্যালোক।  ")
    assert plain.is_correct is True and noisy.is_correct is True
    assert plain.score == noisy.score == 1.0


def test_english_answer_ignores_punctuation_and_capitalisation():
    plain = grade_short(BILINGUAL_RUBRIC, "process green plants sunlight")
    noisy = grade_short(BILINGUAL_RUBRIC, "PROCESS, green — plants (sunlight)!")
    assert plain.is_correct is True and noisy.is_correct is True
    assert plain.score == noisy.score


def test_equivalent_bangla_wording_scores_the_same_band():
    first = grade_short(BILINGUAL_RUBRIC, "সূর্যালোক, সবুজ উদ্ভিদ, প্রক্রিয়া — তিনটাই দরকার")
    second = grade_short(BILINGUAL_RUBRIC, "প্রক্রিয়ায় সবুজ উদ্ভিদ আর সূর্যালোক লাগে")
    assert first.is_correct is True and second.is_correct is True
    assert first.score == second.score == 1.0


def test_bangla_answer_to_english_only_rubric_is_flagged_not_failed():
    """String matching cannot judge it, so the teacher decides instead of a zero."""
    q = {"type": "short", "rubric_terms": ["process", "green"],
         "answer_text_en": "x", "evidence": {"start_s": 0}}
    g = grade_short(q, "সবুজ উদ্ভিদ সূর্যালোকে খাদ্য তৈরি করে।")
    assert g.is_correct is None and g.needs_review is True


def test_bangla_feedback_hints_use_bangla_terms():
    g = grade_short(BILINGUAL_RUBRIC, "সবুজ উদ্ভিদ খাদ্য তৈরি করে না।")
    assert "সূর্যালোক" in g.feedback_bn or "প্রক্রিয়া" in g.feedback_bn
