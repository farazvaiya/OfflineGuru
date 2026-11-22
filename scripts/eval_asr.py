#!/usr/bin/env python3
"""Measure Whisper WER, per-segment latency and memory on a known lecture.

Segments are produced by the same VAD segmenter used live, so the latency column
is a realistic streaming number rather than a whole-file batch number.

    .venv/bin/python scripts/eval_asr.py --sizes tiny,base,small
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import psutil
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from guru.asr import WhisperAsr
from guru.audio import SAMPLE_RATE, Segmenter
from guru.config import load_settings


def normalise(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return [w for w in text.split() if w]


def wer(reference: list[str], hypothesis: list[str]) -> float:
    """Levenshtein word error rate."""
    if not reference:
        return 0.0
    prev = list(range(len(hypothesis) + 1))
    for i, r in enumerate(reference, 1):
        cur = [i]
        for j, h in enumerate(hypothesis, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(reference)


def segments_of(wav: str) -> list[np.ndarray]:
    audio, sr = sf.read(wav, dtype="float32")
    if sr != SAMPLE_RATE:
        raise SystemExit(f"expected 16 kHz audio, got {sr}")
    seg = Segmenter(min_segment_s=0.8, max_segment_s=12, silence_ms=600)
    out: list[np.ndarray] = []
    for i in range(0, len(audio), 1600):
        out.extend(s.audio for s in seg.feed(audio[i : i + 1600]))
    tail = seg.flush()
    if tail:
        out.append(tail.audio)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default="data/demo/lecture.wav")
    ap.add_argument("--text", default="data/demo/lecture.txt")
    ap.add_argument("--sizes", default="tiny,base,small")
    ap.add_argument("--out", default="data/eval/asr.json")
    args = ap.parse_args()

    wav = ROOT / args.wav
    text_file = ROOT / args.text
    reference = normalise(text_file.read_text())
    chunks = segments_of(str(wav))
    total_audio = sum(len(c) for c in chunks) / SAMPLE_RATE
    print(f"{len(chunks)} segments, {total_audio:.1f}s speech from {wav.name}")
    print(f"reference: {len(reference)} words")

    results = {}
    for size in [s.strip() for s in args.sizes.split(",") if s.strip()]:
        settings = load_settings()
        settings.asr.model_dir = f"models/whisper-{size}"
        if not (ROOT / settings.asr.model_dir).is_dir():
            print(f"skip {size}: model missing")
            continue
        proc = psutil.Process(os.getpid())
        asr = WhisperAsr(settings)
        t0 = time.perf_counter()
        asr.load()
        load_s = time.perf_counter() - t0
        rss_after_load = proc.memory_info().rss / 1e6
        texts, latencies, rtf = [], [], []
        for c in chunks:
            r = asr.transcribe(c)
            texts.append(r.text)
            latencies.append(r.latency_ms)
            rtf.append(r.latency_ms / 1000 / max(0.1, len(c) / SAMPLE_RATE))
        hyp = normalise(" ".join(texts))
        w = wer(reference, hyp)
        lat = sorted(latencies)
        results[size] = {
            "wer": round(w, 3),
            "segments": len(chunks),
            "speech_s": round(total_audio, 1),
            "load_s": round(load_s, 2),
            "rss_mb_after_load": round(rss_after_load),
            "asr_ms_mean": round(sum(latencies) / len(latencies)),
            "asr_ms_p50": round(lat[len(lat) // 2]),
            "asr_ms_p95": round(lat[int(len(lat) * 0.95)]),
            "rtf_mean": round(sum(rtf) / len(rtf), 2),
            "words": len(hyp),
        }
        print(f"{size:6s} WER {w:5.1%}  asr p50 {lat[len(lat) // 2]:6.0f} ms  "
              f"p95 {lat[int(len(lat) * 0.95)]:6.0f} ms  rtf {sum(rtf)/len(rtf):.2f}  "
              f"rss {rss_after_load:.0f} MB")

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "wav": str(wav), "reference_words": len(reference),
        "segments": len(chunks), "results": results,
    }, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
