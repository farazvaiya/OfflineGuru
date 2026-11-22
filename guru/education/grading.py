"""Automatic grading and explanatory feedback.

Design rules (from the project constraints):
* Objective questions are graded deterministically.
* Short answers are scored against the rubric terms of the reference answer.
* Borderline short answers are flagged ``needs_review`` instead of being marked
  wrong — automated grading of free text is unreliable.
* Feedback never comments on a student's ability, only on this answer and what to
  review next.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..translate import normalize_digits
from .transcript_utils import content_words, format_timestamp


@dataclass
class Grade:
    score: float
    is_correct: bool | None
    needs_review: bool
    feedback_bn: str
    feedback_en: str
    matched_terms: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "is_correct": self.is_correct,
            "needs_review": self.needs_review,
            "feedback_bn": self.feedback_bn,
            "feedback_en": self.feedback_en,
            "matched_terms": self.matched_terms,
        }


_STRIP = re.compile(r"[^\w\u0980-\u09FF%]+")


_ZW = re.compile(r"[\u200b-\u200d\ufeff]")

# Bengali inflectional endings, longest first. Stripping them is what lets
# "ক্লোরোফিলের" match a rubric entry written as "ক্লোরোফিল": without it the two
# never meet and a correct answer is needlessly sent to the teacher. The three-
# character floor keeps short real words from being truncated into noise.
_BN_SUFFIXES = ("গুলো", "গুলোর", "গুলি", "দের", "য়ের", "এর", "ের", "তে", "টি", "টা", "রা", "কে", "য়")


def _stem_bn(token: str) -> str:
    for suffix in _BN_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def _stem_tokens(text: str) -> list[str]:
    return [_stem_bn(t) for t in text.split() if t]


def _norm(text: str) -> str:
    """Lower-case, fold Bengali digits, drop punctuation, collapse whitespace."""
    folded = _STRIP.sub(" ", normalize_digits(_ZW.sub("", text)).lower())
    return re.sub(r"\s+", " ", folded).strip()


def grade_mcq(question: dict[str, Any], submitted_option: str) -> Grade:
    correct_id = question.get("correct_option_id")
    ok = submitted_option.strip().upper() == str(correct_id).upper()
    answer_text = question.get("answer_text_bn") or question.get("answer_text_en", "")
    ts = format_timestamp(question.get("evidence", {}).get("start_s", 0))
    if ok:
        fb_bn = f"সঠিক উত্তর! {answer_text}। ব্যাখ্যা: {question.get('explanation_bn', '')}"
        fb_en = f"Correct. The answer is {question.get('answer_text_en', '')}."
    else:
        fb_bn = (
            f"সঠিক উত্তর: {answer_text}। {question.get('explanation_bn', '')} "
            f"লেকচারের {ts} সময়ে আবার শুনুন।"
        )
        fb_en = (
            f"Not quite. The correct answer is {question.get('answer_text_en', '')}. "
            f"Review the lecture around {ts}."
        )
    return Grade(
        score=1.0 if ok else 0.0,
        is_correct=ok,
        needs_review=False,
        feedback_bn=fb_bn,
        feedback_en=fb_en,
        matched_terms=[],
    )


def rubric_pairs(question: dict[str, Any]) -> list[tuple[str, str]]:
    """Pair each English rubric term with its Bangla counterpart (either may be empty)."""
    en = [str(t).strip() for t in question.get("rubric_terms") or []]
    bn = [str(t).strip() for t in question.get("rubric_terms_bn") or []]
    pairs: list[tuple[str, str]] = []
    for i in range(max(len(en), len(bn))):
        e = en[i] if i < len(en) else ""
        b = bn[i] if i < len(bn) else ""
        if e or b:
            pairs.append((e, b))
    return pairs


def grade_short(question: dict[str, Any], answer: str) -> Grade:
    """Rubric overlap grading with an explicit uncertainty band.

    This is literal string matching, not semantic understanding: an answer hits
    a rubric entry when it contains that entry, in English or in Bangla. Because
    a miss means "we cannot tell", the middle band is flagged for the teacher
    instead of being scored right or wrong.
    """
    text = _norm(answer)
    words = content_words(text)
    answer_stems = set(_stem_tokens(text))
    pairs = rubric_pairs(question)
    ts = format_timestamp(question.get("evidence", {}).get("start_s", 0))

    if not words:
        return Grade(
            score=0.0,
            is_correct=None,
            needs_review=True,
            feedback_bn="উত্তরটি খালি ছিল। অনুগ্রহ করে নিজের ভাষায় উত্তর লিখুন।",
            feedback_en="No answer was submitted.",
            matched_terms=[],
        )

    matched: list[str] = []
    missed_en: list[str] = []
    missed_bn: list[str] = []
    def _hits(term: str) -> bool:
        """Exact phrase, or every word present after inflection folding.

        The second branch is what tolerates word order and vowel-ending changes;
        it still requires *all* words of a rubric term, so an answer that merely
        shares one word does not pass.
        """
        if not term:
            return False
        if term in text:
            return True
        parts = _stem_tokens(term)
        return bool(parts) and all(p in answer_stems for p in parts)

    for en_term, bn_term in pairs:
        en_hit = _hits(_norm(en_term))
        bn_hit = _hits(_norm(bn_term))
        if en_hit or bn_hit:
            # Report the language the answer actually used, so a teacher reading
            # the file sees the term that matched, not its translation.
            matched.append(en_term if en_hit else (bn_term or en_term))
        else:
            if en_term:
                missed_en.append(en_term)
            if bn_term:
                missed_bn.append(bn_term)
    ratio = len(matched) / max(1, len(pairs))
    # Very short answers cannot be judged reliably even if a keyword matched.
    too_short = len(words) < 3
    # A Bangla answer to a quiz generated before Bangla rubrics existed cannot be
    # judged by string matching at all: ask the teacher instead of marking it wrong.
    unjudgeable = bool(_BANGLA.search(answer)) and not any(b for _e, b in pairs)

    if too_short or (unjudgeable and not matched):
        # Too little evidence to judge either way: never mark this wrong.
        is_correct, needs_review = None, True
    elif ratio >= 0.7:
        is_correct, needs_review = True, False
    elif ratio < 0.35:
        is_correct, needs_review = False, False
    else:
        is_correct, needs_review = None, True

    if is_correct:
        fb_bn = "ভালো উত্তর! মূল ধারণাগুলো ধরতে পেরেছেন।"
        fb_en = "Good answer — you covered the key ideas."
    elif needs_review:
        fb_bn = (
            "উত্তরটি আংশিক। শিক্ষক এটি দেখে নেবেন। "
            f"লেকচারের {ts} সময়ের অংশটি আবার দেখুন।"
        )
        fb_en = "Partly correct — your teacher will review this answer."
    else:
        fb_bn = f"এখনো নয়। লেকচারের {ts} সময়ে বিষয়টি আবার শুনুন, তারপর আবার চেষ্টা করুন।"
        fb_en = f"Not yet. Review the lecture around {ts} and try again."

    if not is_correct:
        if missed_bn or missed_en:
            fb_bn += f" যে বিষয়গুলো যোগ করতে পারেন: {', '.join((missed_bn or missed_en)[:3])}।"
        if missed_en:
            fb_en += f" Ideas to include: {', '.join(missed_en[:3])}."

    return Grade(
        score=round(ratio, 2),
        is_correct=is_correct,
        needs_review=needs_review,
        feedback_bn=fb_bn,
        feedback_en=fb_en,
        matched_terms=matched,
    )


_BANGLA = re.compile(r"[\u0980-\u09FF]")


def grade(question: dict[str, Any], answer: str) -> Grade:
    if question.get("type") == "mcq":
        return grade_mcq(question, answer)
    return grade_short(question, answer)


def study_recommendations(topic_stats: list[dict[str, Any]], min_attempts: int = 3) -> list[dict[str, Any]]:
    """Topic-level hints, only when there is enough evidence.

    With fewer than ``min_attempts`` answers on a topic we say nothing rather
    than drawing a conclusion from one mistake.
    """
    out: list[dict[str, Any]] = []
    for row in topic_stats:
        attempts = int(row.get("attempts", 0))
        if attempts < min_attempts:
            continue
        correct = int(row.get("correct", 0))
        accuracy = correct / attempts
        if accuracy < 0.5:
            out.append(
                {
                    "topic": row["topic"],
                    "attempts": attempts,
                    "accuracy": round(accuracy, 2),
                    "suggestion_bn": f"«{row['topic']}» বিষয়ে আরও অনুশীলন করুন।",
                    "suggestion_en": f"More practice recommended on “{row['topic']}”.",
                }
            )
    return out
