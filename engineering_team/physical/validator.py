"""Evidence-based validation for physical test sessions (Phase 4).

Validators compare MEASURED evidence (telemetry DB rows, app metric tables,
or human-supplied reference files) against configurable thresholds.

Outcomes are exactly: PASS, FAIL, INCONCLUSIVE, NOT_EXECUTED.
- PASS only with the required evidence present.
- INCONCLUSIVE when evidence exists but is incomplete/ambiguous.
- NOT_EXECUTED when the check could not run (no hardware, no reference,
  no session). Missing evidence is never PASS and never zero.

Reference metrics (WER/CER/chrF) are regression signals against engineer-
written references; they do not determine semantic quality (prompt rule).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .telemetry import TelemetryDB

# -- reference metric implementations (self-contained, no deps) -------------


def _words(text: str) -> list[str]:
    return re.findall(r"[^\s]+", text.lower())


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate (Levenshtein over words), 0.0..1.0+. Empty ref+hyp=0."""
    ref, hyp = _words(reference), _words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, rc in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, hc in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc))
        prev = cur
    return prev[len(hyp)] / len(ref)


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate."""
    return wer(" ".join(reference.lower()), " ".join(hypothesis.lower()))


def _chrf(hyp: str, ref: str, max_n: int = 6, beta: float = 2.0) -> float:
    """chrF++-style: character n-gram F-score averaged over orders 1..max_n.

    Simplified self-contained implementation (no external deps). Sufficient as
    a regression signal; not a substitute for human judgement.
    """

    def ngrams(s: str, n: int) -> list[str]:
        s = " " + s.strip() + " "
        return [s[i : i + n] for i in range(len(s) - n + 1)]

    if not hyp.strip() or not ref.strip():
        return 0.0
    from collections import Counter
    prec_total = rec_total = 0.0
    for n in range(1, max_n + 1):
        hr = ngrams(hyp, n)
        rf = ngrams(ref, n)
        ch, cr = Counter(hr), Counter(rf)
        overlap = sum((ch & cr).values())
        if not hr or not rf:
            continue
        prec_total += overlap / len(hr)
        rec_total += overlap / len(rf)
    if prec_total == 0 and rec_total == 0:
        return 0.0
    p, r = prec_total / max_n, rec_total / max_n
    if p + r == 0:
        return 0.0
    return (1 + beta**2) * (p * r) / ((beta**2 * p) + r)


# -- validators --------------------------------------------------------------


class ValidationError(Exception):
    pass


class Validator:
    """Runs named checks against a TelemetryDB session + optional references."""

    def __init__(self, db: TelemetryDB, thresholds: dict[str, Any] | None = None) -> None:
        self.db = db
        self.thresholds = thresholds or {}

    # helpers ---------------------------------------------------------------
    def _stat(self, session_id: str | None, key: str) -> Any:
        """Latest ASR-collector stat from telemetry events, or None."""
        rows = self.db.events(session_id=session_id, collector="asr", limit=1)
        if not rows:
            return None
        try:
            payload = json.loads(rows[0]["payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            return None
        return payload.get(key)

    def _require_session(self, session_id: str | None) -> None:
        if not session_id:
            raise ValidationError("session_id required")

    # checks ----------------------------------------------------------------
    def check_asr_wer(self, session_id: str | None, reference: str,
                      hypothesis: str, max_wer: float | None = None) -> dict[str, Any]:
        """WER against a reference transcript. INCONCLUSIVE if either side empty."""
        self._require_session(session_id)
        if not reference.strip():
            return self._record(session_id, "ASR_WER", "INCONCLUSIVE",
                                {"reference_words": 0}, {},
                                evidence="reference transcript empty",
                                notes="WER needs a non-empty reference")
        measured = {"wer": round(wer(reference, hypothesis), 4),
                    "reference_words": len(_words(reference)),
                    "hypothesis_words": len(_words(hypothesis))}
        lim = max_wer if max_wer is not None else self.thresholds.get("max_wer")
        if lim is None:
            outcome = "INCONCLUSIVE"
            notes = ["no max_wer threshold configured"]
        else:
            outcome = "PASS" if measured["wer"] <= lim else "FAIL"
            notes = []
        return self._record(session_id, "ASR_WER", outcome, measured,
                            {"max_wer": lim}, evidence="wer() computed from supplied strings",
                            notes=notes)

    def check_cer(self, session_id: str | None, reference: str, hypothesis: str,
                  max_cer: float | None = None) -> dict[str, Any]:
        self._require_session(session_id)
        if not reference.strip():
            return self._record(session_id, "ASR_CER", "INCONCLUSIVE",
                                {"reference_chars": 0}, {},
                                evidence="reference empty", notes=["CER needs a reference"])
        measured = {"cer": round(cer(reference, hypothesis), 4)}
        lim = max_cer if max_cer is not None else self.thresholds.get("max_cer")
        outcome = "INCONCLUSIVE" if lim is None else ("PASS" if measured["cer"] <= lim else "FAIL")
        return self._record(session_id, "ASR_CER", outcome, measured, {"max_cer": lim},
                            evidence="cer() computed from supplied strings")

    def check_translation_chrf(self, session_id: str | None, reference: str,
                               hypothesis: str, min_chrf: float | None = None) -> dict[str, Any]:
        self._require_session(session_id)
        measured = {"chrf": round(_chrf(hypothesis, reference), 4)}
        lim = min_chrf if min_chrf is not None else self.thresholds.get("min_chrf")
        if not reference.strip() or not hypothesis.strip():
            return self._record(session_id, "TRANS_CHRF", "INCONCLUSIVE", measured,
                                {"min_chrf": lim}, evidence="empty input",
                                notes=["chrF needs non-empty reference and hypothesis"])
        outcome = "INCONCLUSIVE" if lim is None else ("PASS" if measured["chrf"] >= lim else "FAIL")
        return self._record(session_id, "TRANS_CHRF", outcome, measured, {"min_chrf": lim},
                            evidence="_chrf() computed from supplied strings",
                            notes=["regression signal only; not a semantic quality claim"])

    def check_asr_queue_drops(self, session_id: str | None, max_drops: int = 0) -> dict[str, Any]:
        self._require_session(session_id)
        v = self._stat(session_id, "asr_queue_drops")
        if v is None:
            return self._record(session_id, "ASR_QUEUE_DROPS", "INCONCLUSIVE",
                                {"asr_queue_drops": None}, {"max_drops": max_drops},
                                evidence="no ASR telemetry recorded for session",
                                notes=["collector never saw the app; cannot judge"])
        outcome = "PASS" if int(v) <= max_drops else "FAIL"
        return self._record(session_id, "ASR_QUEUE_DROPS", outcome,
                            {"asr_queue_drops": v}, {"max_drops": max_drops},
                            evidence="telemetry_events asr payload")

    def check_final_tail(self, session_id: str | None) -> dict[str, Any]:
        """Final-tail decode coverage: the app does not expose it (GAP G-5)."""
        self._require_session(session_id)
        return self._record(
            session_id, "FINAL_TAIL_DECODE", "NOT_EXECUTED",
            {"final_tail_decode_count": None}, {},
            evidence="app exposes no final-tail decode counter (GAP G-5)",
            notes=["cannot verify without a production counter; recommend (not implement) one"],
        )

    def check_audible_playback(self, session_id: str | None,
                               human_feedback: str | None = None) -> dict[str, Any]:
        """Audible playback can never be PASS from server telemetry alone (rule 3.4)."""
        self._require_session(session_id)
        if human_feedback and human_feedback.strip():
            return self._record(
                session_id, "AUDIBLE_PLAYBACK", "INCONCLUSIVE",
                {"human_feedback": human_feedback[:500]}, {},
                evidence="human_feedback row",
                notes=["human report supports but does not prove audible playback"],
            )
        return self._record(
            session_id, "AUDIBLE_PLAYBACK", "NOT_EXECUTED", {}, {},
            evidence="no student ack and no human feedback supplied",
            notes=["server delivery != audible playback (rule 3.4)"],
        )

    def check_duration(self, session_id: str | None, expected_s: float,
                       tolerance_pct: float = 10.0) -> dict[str, Any]:
        """Expected vs captured audio duration. Needs a measured captured_s."""
        self._require_session(session_id)
        v = self._stat(session_id, "captured_audio_s")
        if v is None:
            return self._record(session_id, "AUDIO_DURATION", "NOT_EXECUTED",
                                {"expected_s": expected_s, "captured_s": None},
                                {"tolerance_pct": tolerance_pct},
                                evidence="captured audio duration not exposed by app (GAP G-2)",
                                notes=["missing, not zero"])
        delta = abs(float(v) - expected_s) / max(expected_s, 1e-9) * 100
        outcome = "PASS" if delta <= tolerance_pct else "FAIL"
        return self._record(session_id, "AUDIO_DURATION", outcome,
                            {"expected_s": expected_s, "captured_s": v, "delta_pct": round(delta, 2)},
                            {"tolerance_pct": tolerance_pct},
                            evidence="telemetry captured_audio_s")

    # internals -------------------------------------------------------------
    def _record(self, session_id: str | None, check_id: str, outcome: str,
                measured: Any, threshold: Any, evidence: str, notes: list[str] | None = None) -> dict[str, Any]:
        self.db.record_validation(session_id, check_id, outcome, measured, threshold,
                                  evidence, "; ".join(notes or []))
        return {"check_id": check_id, "outcome": outcome, "measured": measured,
                "threshold": threshold, "evidence": evidence, "notes": notes or []}

    def run_all(self, session_id: str | None, **kwargs: Any) -> list[dict[str, Any]]:
        """Run the checks whose inputs are available; missing inputs -> NOT_EXECUTED/INCONCLUSIVE."""
        out = []
        out.append(self.check_final_tail(session_id))
        out.append(self.check_audible_playback(session_id, kwargs.get("human_feedback")))
        if kwargs.get("reference_transcript") is not None and kwargs.get("hypothesis_transcript") is not None:
            out.append(self.check_asr_wer(session_id, kwargs["reference_transcript"],
                                          kwargs["hypothesis_transcript"], kwargs.get("max_wer")))
        if kwargs.get("reference_translation") is not None and kwargs.get("hypothesis_translation") is not None:
            out.append(self.check_translation_chrf(session_id, kwargs["reference_translation"],
                                                   kwargs["hypothesis_translation"], kwargs.get("min_chrf")))
        out.append(self.check_asr_queue_drops(session_id, kwargs.get("max_drops", 0)))
        if kwargs.get("expected_duration_s") is not None:
            out.append(self.check_duration(session_id, kwargs["expected_duration_s"]))
        return out
