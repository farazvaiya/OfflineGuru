#!/usr/bin/env python3
"""Deterministic diagnostics for the segmented (non-streaming) capture path.

Two defects are measured here without needing a microphone:

1. ``remainder`` — audio.Segmenter.feed() consumes only whole 320-sample frames
   and throws the leftover samples away instead of carrying them to the next call.
   The measurement feeds the same 60 s of audio twice: once in frame-aligned
   chunks (what eval scripts do) and once in 1000-sample chunks (what a short
   pipe read produces), and compares how much audio actually reached a segment.

2. ``overlap`` — after a force-cut at max_segment_s the next segment re-seeds its
   timestamp from the rolling pre-roll, so consecutive segments claim overlapping
   audio. Measured as negative gaps between one segment's end_s and the next
   segment's start_s.

    .venv/bin/python engineering_harness/diag_segments.py --wav data/demo/lecture.wav
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import evidence  # noqa: E402
from guru.audio import SAMPLE_RATE, Segmenter  # noqa: E402


def feed(audio: np.ndarray, chunk: int) -> tuple[Segmenter, list]:
    seg = Segmenter(min_segment_s=0.8, max_segment_s=12, silence_ms=600)
    out = []
    for i in range(0, len(audio), chunk):
        out.extend(seg.feed(audio[i:i + chunk]))
    tail = seg.flush()
    if tail:
        out.append(tail)
    return seg, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default="data/demo/lecture.wav")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--label", default="baseline")
    args = ap.parse_args()

    import soundfile as sf

    audio, sr = sf.read(str(evidence.REPO / args.wav), dtype="float32")
    if sr != SAMPLE_RATE:
        raise SystemExit(f"expected 16 kHz, got {sr}")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio[: int(args.seconds * SAMPLE_RATE)]

    # 1) remainder loss
    _, aligned = feed(audio, 1600)      # 5 x 320: exact
    _, ragged = feed(audio, 1000)       # 3 x 320 + 40 dropped per call
    aligned_s = sum(s.duration_s for s in aligned)
    ragged_s = sum(s.duration_s for s in ragged)
    # The clock is derived from frames consumed, so lost samples also drift time.
    ragged_clock = 0.0
    seg = Segmenter()
    for i in range(0, len(audio), 1000):
        seg.feed(audio[i:i + 1000])
    ragged_clock = seg._total_frames * 0.02  # exposed for the diagnostic only
    input_s = len(audio) / SAMPLE_RATE

    # 2) segment overlap
    overlaps = []
    for a, b in zip(aligned, aligned[1:]):
        gap = b.start_s - a.end_s
        if gap < -0.001:
            overlaps.append({"prev_end": round(a.end_s, 2), "next_start": round(b.start_s, 2),
                             "overlap_s": round(-gap, 3)})

    payload = {
        "wav": args.wav,
        "input_s": round(input_s, 2),
        "aligned": {
            "chunk_samples": 1600,
            "segments": len(aligned),
            "covered_s": round(aligned_s, 2),
            "clock_s": round(aligned[-1].end_s if aligned else 0.0, 2),
            "loss_pct": round(100 * (1 - aligned_s / max(1e-9, sum(
                min(12.0, s.duration_s) for s in aligned))), 2),
        },
        "ragged": {
            "chunk_samples": 1000,
            "segments": len(ragged),
            "covered_s": round(ragged_s, 2),
            "clock_s": round(ragged_clock, 2),
            "expected_clock_s": round(input_s, 2),
            "clock_drift_pct": round(100 * (1 - ragged_clock / max(1e-9, input_s)), 2),
            "covered_vs_aligned_pct": round(100 * (ragged_s / max(1e-9, aligned_s) - 1), 2),
        },
        "overlap": {
            "pairs": len(overlaps),
            "instances": overlaps[:20],
            "total_overlap_s": round(sum(o["overlap_s"] for o in overlaps), 3),
        },
        "interpretation": (
            "ragged.chunk_samples=1000 is 3 full 320-sample frames plus a 40-sample tail. "
            "feed() drops that tail on every call, so audio and the timestamp clock both "
            "advance slower than wall time. overlap measures the pre-roll re-seed after a "
            "max_segment_s force-cut."
        ),
    }
    path = evidence.write(f"segment_alignment_{args.label}", payload, command=" ".join(sys.argv))
    import json

    print(json.dumps(payload, indent=2))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
