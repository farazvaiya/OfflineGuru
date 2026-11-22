"""Text utilities shared by the educational modules.

These functions operate on stored transcript segments and are intentionally
deterministic: every summary sentence and quiz item can be traced back to a
segment id and timestamp.  Nothing here invents content.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "so", "because",
    "as", "of", "at", "by", "for", "with", "about", "against", "between", "into",
    "through", "during", "before", "after", "above", "below", "to", "from", "up",
    "down", "in", "out", "on", "off", "over", "under", "again", "further", "once",
    "here", "there", "when", "where", "why", "how", "all", "any", "both", "each",
    "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only",
    "own", "same", "too", "very", "can", "will", "just", "should", "now", "is",
    "are", "was", "were", "be", "been", "being", "have", "has", "had", "do",
    "does", "did", "this", "that", "these", "those", "i", "you", "he", "she",
    "it", "we", "they", "them", "his", "her", "its", "our", "their", "my",
    "your", "what", "which", "who", "whom", "also", "like", "okay", "right",
    "well", "let", "lets", "us", "see", "look", "think", "know", "want", "going",
    "go", "get", "got", "make", "made", "say", "said", "one", "two", "three",
    "today", "class", "students", "student", "teacher", "everybody", "everyone",
    "something", "anything", "thing", "things", "much", "many", "really", "quite",
    # Classroom filler and definition verbs that are not useful key terms or
    # quiz distractors.
    "called", "known", "defined", "means", "refer", "refers", "using", "used",
    "makes", "making", "inside", "around", "probably", "actually", "basically",
    "example", "examples", "different", "important", "answer", "question",
}

DEFINITION_PATTERNS = [
    re.compile(r"^(?P<term>[A-Za-z][A-Za-z0-9 \-]{2,40}?)\s+(?:is|are)\s+(?:called|known as|defined as)\s+(?P<def>.+)$", re.IGNORECASE),
    re.compile(r"^(?P<term>[A-Za-z][A-Za-z0-9 \-]{2,40}?)\s+(?:means|refers to|is defined as)\s+(?P<def>.+)$", re.IGNORECASE),
    re.compile(r"^(?P<def>.+?)\s+is\s+(?:called|known as)\s+(?P<term>[A-Za-z][A-Za-z0-9 \-]{2,40})\s*\.?$", re.IGNORECASE),
    re.compile(r"^(?P<term>[A-Z][A-Za-z0-9 \-]{2,40}?)\s+(?:is|are)\s+(?P<def>(?:a|an|the)\s.+)$"),
]

NUMERIC_PATTERN = re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|percent|degrees?|km|kg|cm|mm|m|s|seconds?|minutes?|hours?|years?|litres?|liters?|grams?)?\b")


@dataclass
class Evidence:
    segment_id: str
    seq: int
    start_s: float
    quote_en: str
    # Present only for evidence taken from an uploaded document.
    source: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "seq": self.seq,
            "start_s": round(self.start_s, 2),
            "quote_en": self.quote_en,
            "source": self.source,
        }


@dataclass
class Sentence:
    text: str
    segment_id: str
    seq: int
    start_s: float

    @property
    def evidence(self) -> Evidence:
        return Evidence(self.segment_id, self.seq, self.start_s, self.text)


def to_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z\u0980-\u09FF][A-Za-z\u0980-\u09FF'\-]*", text)


def content_words(text: str) -> list[str]:
    return [w.lower() for w in to_words(text) if w.lower() not in STOPWORDS and len(w) > 2]


def split_sentences(segments: Sequence[dict[str, Any]]) -> list[Sentence]:
    """Flatten transcript segments into sentences, preserving evidence links."""
    out: list[Sentence] = []
    for seg in segments:
        text = (seg.get("en") or "").strip()
        if not text:
            continue
        parts = re.split(r"(?<=[.!?])\s+", text)
        for part in parts:
            part = part.strip()
            if len(part) < 8:
                continue
            out.append(
                Sentence(
                    text=part,
                    segment_id=seg["id"],
                    seq=int(seg["seq"]),
                    start_s=float(seg.get("start_s", 0.0)),
                )
            )
    return out


def term_frequencies(sentences: Sequence[Sentence]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in sentences:
        for w in content_words(s.text):
            counts[w] = counts.get(w, 0) + 1
    return counts


def key_terms(segments: Sequence[dict[str, Any]], limit: int = 8) -> list[str]:
    """Rank salient terms by frequency, definition position and capitalisation."""
    sentences = split_sentences(segments)
    tf = term_frequencies(sentences)
    if not tf:
        return []
    scored: dict[str, float] = {}
    for term, count in tf.items():
        scored[term] = count * (1.0 + math.log(1 + count))
    # Terms introduced by an explicit definition outrank incidental mentions.
    for s in sentences:
        d = parse_definition(s.text)
        if not d:
            continue
        term, _ = d
        for w in content_words(term):
            scored[w] = scored.get(w, 0) + 3.0
    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))
    return [t for t, _ in ranked[:limit]]


def parse_definition(text: str) -> tuple[str, str] | None:
    """Return (term, definition) if the sentence looks like a definition."""
    clean = text.strip().rstrip(".")
    for pattern in DEFINITION_PATTERNS:
        m = pattern.match(clean)
        if m:
            term = m.group("term").strip(" ,.")
            definition = m.group("def").strip(" ,.")
            if 2 <= len(term) <= 60 and len(definition) >= 8:
                return term, definition
    return None


def extract_numbers(text: str) -> list[str]:
    return [m.group(0).strip() for m in NUMERIC_PATTERN.finditer(text)]


def sentence_score(sentence: Sentence, tf: dict[str, int], total: int) -> float:
    words = content_words(sentence.text)
    if not words:
        return 0.0
    uniq = set(words)
    weight = sum(tf.get(w, 0) for w in uniq) / max(1, len(uniq))
    norm = math.log(1 + total)
    length_penalty = 0.0 if len(words) >= 5 else 0.5 * (5 - len(words))
    definition_bonus = 1.5 if parse_definition(sentence.text) else 0.0
    numeric_bonus = 0.4 if extract_numbers(sentence.text) else 0.0
    return (weight / norm) + definition_bonus + numeric_bonus - length_penalty


def select_summary_sentences(segments: Sequence[dict[str, Any]], max_sentences: int = 7) -> list[Sentence]:
    """Pick high-information, non-redundant sentences in chronological order."""
    sentences = split_sentences(segments)
    if not sentences:
        return []
    tf = term_frequencies(sentences)
    total = sum(tf.values()) or 1
    scored = [(sentence_score(s, tf, total), i, s) for i, s in enumerate(sentences)]
    scored.sort(key=lambda t: -t[0])

    chosen: list[tuple[int, Sentence]] = []
    seen_tokens: set[str] = set()
    for score, idx, s in scored:
        if len(chosen) >= max_sentences:
            break
        tokens = set(content_words(s.text))
        if not tokens:
            continue
        overlap = len(tokens & seen_tokens) / max(1, len(tokens))
        if overlap > 0.75 and chosen:  # near-duplicate
            continue
        chosen.append((idx, s))
        seen_tokens |= tokens
    chosen.sort(key=lambda t: t[0])
    return [s for _, s in chosen]


def format_timestamp(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def contrasting_terms(term: str, pool: Iterable[str], k: int = 3) -> list[str]:
    """Pick plausible distractors that are not the answer and not substrings of it."""
    low = term.lower()
    out: list[str] = []
    for cand in pool:
        c = cand.lower()
        if c == low or c in low or low in c:
            continue
        if c not in [o.lower() for o in out]:
            out.append(cand)
        if len(out) >= k:
            break
    return out
