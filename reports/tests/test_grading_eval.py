"""Grading evaluation: what the rubric matcher can and cannot judge.

This is a small, explicit dataset rather than a vibe check. Each case states the
behaviour we require. Cases that the matcher still cannot handle (synonyms) are
asserted as ``needs_review`` — never as a silent pass — because the teacher, not
the string matcher, decides those.
"""

from __future__ import annotations

import pytest

from guru.education.grading import grade, grade_mcq

QUESTION = {
    "type": "short",
    "rubric_terms": ["chlorophyll", "light energy"],
    "rubric_terms_bn": ["ক্লোরোফিল", "আলোর শক্তি"],
    "evidence": {"start_s": 12.0},
}


def g(answer: str):
    return grade(QUESTION, answer)


@pytest.mark.parametrize(
    "answer",
    [
        "Chlorophyll captures light energy.",
        "light energy is captured by chlorophyll",
        "ক্লোরোফিল আলোর শক্তি ধারণ করে",
    ],
)
def test_correct_answers_in_both_languages(answer):
    result = g(answer)
    assert result.is_correct is True
    assert result.needs_review is False


def test_word_order_does_not_break_a_correct_answer():
    # Rubric term "light energy" appears reversed in the answer.
    result = g("Chlorophyll absorbs energy from light in the leaf")
    assert result.is_correct is True


def test_bangla_inflection_matches_the_base_rubric_term():
    # "ক্লোরোফিলের" (genitive) must match the rubric entry "ক্লোরোফিল".
    result = g("ক্লোরোফিলের কাজ হলো আলোর শক্তি ধরা")
    assert "ক্লোরোফিল" in result.matched_terms
    assert result.is_correct is True


def test_empty_answer_is_not_marked_wrong():
    result = g("   ")
    assert result.is_correct is None
    assert result.needs_review is True


def test_too_short_answer_goes_to_review_not_failure():
    result = g("ক্লোরোফিল")
    assert result.is_correct is None
    assert result.needs_review is True


def test_unrelated_answer_is_marked_wrong():
    result = g("The stomach digests food with acid and enzymes in the body")
    assert result.is_correct is False
    assert result.needs_review is False


def test_synonym_paraphrase_is_marked_wrong_and_never_approved():
    # Known limitation, pinned here so no one claims it is solved: the matcher
    # has no word-sense knowledge, so a pure paraphrase shares no term with the
    # rubric and is scored wrong. It is never silently approved, and the teacher
    # can override it in the console (teacher_override).
    result = g("the green pigment in leaves traps sunlight")
    assert result.is_correct is False
    assert result.score == 0.0


def test_partial_answer_is_flagged_for_review():
    result = g("Chlorophyll is important for the plant and it is green in colour")
    assert result.is_correct is None
    assert result.needs_review is True


def test_mcq_is_exact():
    question = {"type": "mcq", "correct_option_id": "B", "answer_text_en": "water"}
    assert grade_mcq(question, "b").is_correct is True
    assert grade_mcq(question, "A").is_correct is False
