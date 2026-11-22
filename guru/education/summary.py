"""Offline-Bangla lecture summarisation.

Strategy: extract high-information sentences from the *stored transcript*, then
translate exactly those sentences with the local translation model.  This is
grounded by construction — the Bangla summary cannot contain facts that were not
said in class.

If a local LLM is configured (``education.llm_provider = "ollama"``) it is used to
choose/refine *English* sentences only; the Bangla text is still produced by the
translation model, and any LLM failure falls back to the extractive path.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Protocol

from ..translate import Translation
from .transcript_utils import (
    key_terms,
    select_summary_sentences,
    split_sentences,
    term_frequencies,
)

log = logging.getLogger(__name__)


class TranslatorLike(Protocol):
    def translate_batch(self, texts: list[str], beam_size: int | None = None) -> list[Translation]: ...


def _translate(translator: TranslatorLike, texts: list[str]) -> list[str]:
    if not texts:
        return []
    try:
        results = translator.translate_batch(texts, beam_size=4)
    except Exception:  # pragma: no cover - defensive
        log.exception("summary translation failed")
        return [""] * len(texts)
    return [r.text if r.text else r.source for r in results]


def _document_points(passages: Sequence[dict[str, Any]], limit: int = 2) -> list[dict[str, Any]]:
    """Best-supported sentence per cited page (never a sentence that is not there)."""
    points: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for passage in passages:
        document = str(passage.get("document") or "")
        page = int(passage.get("page") or 0)
        text = " ".join(str(passage.get("text") or "").split())
        if not document or page < 1 or len(text) < 40:
            continue
        key = (document, page)
        if key in seen:
            continue
        seen.add(key)
        points.append({
            "text": text,
            "page": page,
            "source": {"document": document, "page": page},
        })
        if len(points) >= limit:
            break
    return points


def build_summary(
    segments: Sequence[dict[str, Any]],
    translator: TranslatorLike,
    max_sentences: int = 7,
    session_title: str = "Class lecture",
    passages: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a bilingual, evidence-linked summary of transcript and slides.

    Every sentence is copied from the transcript or from an uploaded document and
    carries its origin; nothing is paraphrased into a claim the sources do not
    contain.  Only the Bangla rendering is produced by the translation model.
    """
    usable = [s for s in segments if (s.get("en") or "").strip()]
    if not usable and not passages:
        return {
            "title": session_title,
            "summary_bn": [],
            "summary_en": [],
            "key_terms": [],
            "topics": [],
            "sources": [],
            "evidence_segment_ids": [],
            "generated_by": "extractive-v1",
            "insufficient_content": True,
            "note": "No finalized transcript segments yet.",
        }

    sentences = select_summary_sentences(usable, max_sentences=max_sentences)
    en_texts = [s.text for s in sentences]
    bn_texts = _translate(translator, en_texts)

    summary_en = [
        {"text": s.text, "segment_id": s.segment_id, "start_s": round(s.start_s, 2)}
        for s in sentences
    ]
    summary_bn = [
        {
            "text": bn,
            "segment_id": s.segment_id,
            "start_s": round(s.start_s, 2),
            "source_en": s.text,
        }
        for s, bn in zip(sentences, bn_texts)
    ]

    # Slide material is appended with its page, after the lecture sentences.
    doc_points = _document_points(passages or ())
    for point in doc_points:
        bn = _translate(translator, [point["text"]])[0]
        summary_en.append({
            "text": point["text"], "segment_id": None, "start_s": 0.0,
            "page": point["page"], "source": point["source"],
        })
        summary_bn.append({
            "text": bn, "segment_id": None, "start_s": 0.0,
            "page": point["page"], "source": point["source"],
            "source_en": point["text"],
        })

    terms = key_terms(usable, limit=8)
    term_bn = _translate(translator, terms)
    key_terms_payload = [
        {"en": t, "bn": b or "", "evidence": _term_evidence(t, usable)}
        for t, b in zip(terms, term_bn)
    ]

    return {
        "title": session_title,
        "summary_bn": summary_bn,
        "summary_en": summary_en,
        "key_terms": key_terms_payload,
        "topics": topics_from_segments(usable),
        "sources": [p["source"] for p in doc_points],
        "evidence_segment_ids": sorted({s.segment_id for s in sentences}),
        "generated_by": "extractive-v1",
        "insufficient_content": False,
    }


def topics_from_segments(segments: Sequence[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    """Very light-touch topic segmentation: group sentences by dominant term.

    Deliberately simple and explainable rather than a hidden clustering model;
    the UI presents them as "sections", and the teacher can edit the summary.
    """
    sentences = split_sentences(segments)
    if not sentences:
        return []
    tf = term_frequencies(sentences)
    ranked = sorted(tf.items(), key=lambda kv: -kv[1])[: limit * 2]
    topics: list[dict[str, Any]] = []
    used: set[str] = set()
    for term, _count in ranked:
        if term in used or len(topics) >= limit:
            continue
        first = next((s for s in sentences if term in s.text.lower()), None)
        if first is None:
            continue
        used.add(term)
        topics.append(
            {
                "label_en": term,
                "label_bn": "",
                "start_s": round(first.start_s, 2),
                "segment_id": first.segment_id,
            }
        )
    return topics


def _term_evidence(term: str, segments: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    for seg in segments:
        text = (seg.get("en") or "")
        if term.lower() in text.lower():
            return {
                "segment_id": seg["id"],
                "start_s": round(float(seg.get("start_s", 0.0)), 2),
                "quote_en": text,
            }
    return None
