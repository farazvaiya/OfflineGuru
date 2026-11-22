#!/usr/bin/env python3
"""ASR accuracy / independent-sample evaluation for the harness.

Two modes:

``--mode wer``
    Segment a WAV with the project's own VAD segmenter and score Whisper against a
    reference transcript. This is the same method scripts/eval_asr.py uses, kept
    deliberately identical so the number is comparable with the historical
    baseline. The reference file is REQUIRED and must correspond to the audio.

``--mode agreement``
    For audio that has no human reference, measure word agreement between two
    independent Whisper sizes on the same segmentation. Agreement is a lower bound
    on how much of the speech both models heard; it is not a WER substitute and is
    reported as such.

Usage
-----
    .venv/bin/python engineering_harness/run_asr_eval.py --mode wer \
        --wav data/demo/lecture.wav --text data/demo/lecture.txt --label baseline
    .venv/bin/python engineering_harness/run_asr_eval.py --mode agreement \
        --wav /tmp/og/emma16k.wav --label independent
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import evidence  # noqa: E402
from guru.asr import WhisperAsr  # noqa: E402
from guru.audio import SAMPLE_RATE, Segmenter  # noqa: E402
from guru.config import load_settings  # noqa: E402


def normalise(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return [w for w in text.split() if w]


def wer(reference: list[str], hypothesis: list[str]) -> float:
    """Levenshtein word error rate (insertions + deletions + substitutions)."""
    if not reference:
        return 0.0
    prev = list(range(len(hypothesis) + 1))
    for i, r in enumerate(reference, 1):
        cur = [i]
        for j, h in enumerate(hypothesis, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(reference)


def alignment_counts(reference: list[str], hypothesis: list[str]) -> dict[str, int]:
    """Backtrace the edit matrix so error *types* can be reported."""
    n, m = len(reference), len(hypothesis)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1,
                          d[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1]))
    sub = dele = ins = hit = 0
    i, j = n, m
    while i > 0 and j > 0:
        if reference[i - 1] == hypothesis[j - 1] and d[i][j] == d[i - 1][j - 1]:
            hit += 1
            i -= 1
            j -= 1
        elif d[i][j] == d[i - 1][j - 1] + 1:
            sub += 1
            i -= 1
            j -= 1
        elif d[i][j] == d[i - 1][j] + 1:
            dele += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    while i > 0:
        dele += 1
        i -= 1
    while j > 0:
        ins += 1
        j -= 1
    return {"correct": hit, "substitutions": sub, "deletions": dele, "insertions": ins}


def segments_of(wav: Path, vad: float = 0.006) -> tuple[list[np.ndarray], Segmenter, float]:
    import soundfile as sf

    audio, sr = sf.read(str(wav), dtype="float32")
    if sr != SAMPLE_RATE:
        raise SystemExit(f"{wav}: expected {SAMPLE_RATE} Hz, got {sr}; convert first")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    seg = Segmenter(min_segment_s=0.8, max_segment_s=12, silence_ms=600, vad_threshold=vad)
    out: list[np.ndarray] = []
    # Frame-aligned feeding: 1600 samples = 5 x 320-sample frames.
    for i in range(0, len(audio), 1600):
        out.extend(s.audio for s in seg.feed(audio[i:i + 1600]))
    tail = seg.flush()
    if tail:
        out.append(tail.audio)
    return out, seg, len(audio) / sr


def load_model(model_dir: str) -> tuple[WhisperAsr, float]:
    settings = load_settings()
    settings.asr.model_dir = model_dir
    asr = WhisperAsr(settings)
    t0 = time.perf_counter()
    asr.load()
    return asr, (time.perf_counter() - t0)


def mode_wer(args: argparse.Namespace) -> int:
    wav = evidence.REPO / args.wav
    text_file = evidence.REPO / args.text
    if not text_file.is_file():
        raise SystemExit(f"reference transcript missing: {text_file}")
    reference = normalise(text_file.read_text())
    chunks, seg, audio_s = segments_of(wav, args.vad)
    speech_s = sum(len(c) for c in chunks) / SAMPLE_RATE
    asr, load_s = load_model(args.model)
    texts, lat = [], []
    empty = 0
    for c in chunks:
        r = asr.transcribe(c)
        texts.append(r.text)
        lat.append(r.latency_ms)
        if len(normalise(r.text)) == 0:
            empty += 1
    hyp = normalise(" ".join(texts))
    score = wer(reference, hyp)
    counts = alignment_counts(reference, hyp)
    ordered = sorted(lat)

    def pct(p: float) -> float:
        return round(ordered[min(len(ordered) - 1, round((p / 100) * (len(ordered) - 1)))], 1)

    payload = {
        "mode": "wer",
        "wav": args.wav,
        "reference": args.text,
        "reference_words": len(reference),
        "hypothesis_words": len(hyp),
        "model": args.model,
        "segments": len(chunks),
        "audio_s": round(audio_s, 1),
        "speech_s": round(speech_s, 1),
        "empty_segments": empty,
        "wer": round(score, 4),
        "coverage": round(len(hyp) / max(1, len(reference)), 3),
        "errors": counts,
        "asr_ms": {"p50": pct(50), "p95": pct(95)},
        "rtf_mean": round(sum(lat) / 1000 / max(0.1, speech_s), 3),
        "load_s": round(load_s, 2),
        "vad_threshold": args.vad,
    }
    path = evidence.write(f"asr_wer_{args.label}", payload, command=" ".join(sys.argv))
    print(json_dumps(payload))
    print(f"wrote {path}")
    return 0


def mode_agreement(args: argparse.Namespace) -> int:
    import soundfile as sf

    wav = Path(args.wav)
    if not wav.is_absolute():
        wav = evidence.REPO / wav
    audio, sr = sf.read(str(wav), dtype="float32")
    if sr != SAMPLE_RATE:
        raise SystemExit(f"{wav}: expected {SAMPLE_RATE} Hz, got {sr}; convert first")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    seg = Segmenter(min_segment_s=0.8, max_segment_s=12, silence_ms=600, vad_threshold=args.vad)
    chunks = []
    for i in range(0, len(audio), 1600):
        chunks.extend(s.audio for s in seg.feed(audio[i:i + 1600]))
    tail = seg.flush()
    if tail:
        chunks.append(tail.audio)

    results = {}
    for name in args.models.split(","):
        asr, load_s = load_model(f"models/whisper-{name.strip()}")
        per_chunk = []
        t0 = time.perf_counter()
        for c in chunks:
            per_chunk.append(normalise(asr.transcribe(c).text))
        elapsed = time.perf_counter() - t0
        results[name.strip()] = {
            "words": sum(len(x) for x in per_chunk),
            "per_chunk": per_chunk,
            "load_s": round(load_s, 2),
            "decode_s": round(elapsed, 1),
            "empty_segments": sum(1 for x in per_chunk if not x),
        }
        del asr

    names = list(results)
    primary = names[0]
    others = names[1:]
    comparisons = {}
    for other in others:
        # Chunk-wise agreement: an insertion/deletion of a whole word counts once.
        matched = total = 0
        for a, b in zip(results[primary]["per_chunk"], results[other]["per_chunk"]):
            counts = alignment_counts(a, b)
            matched += counts["correct"]
            total += max(len(a), len(b), 1)
        comparisons[f"{primary}_vs_{other}"] = round(matched / max(1, total), 3)

    payload = {
        "mode": "inter_model_word_agreement",
        "wav": str(wav.relative_to(evidence.REPO)) if wav.is_relative_to(evidence.REPO) else str(wav),
        "audio_s": round(len(audio) / SAMPLE_RATE, 1),
        "segments": len(chunks),
        "models": {k: {kk: vv for kk, vv in v.items() if kk != "per_chunk"} for k, v in results.items()},
        "agreement": comparisons,
        "note": (
            "Agreement between independent model sizes is a lower bound on shared speech "
            "content. It is NOT a WER substitute: two models can agree on the same error. "
            "Used only for audio that has no human-verified reference transcript."
        ),
    }
    path = evidence.write(f"asr_{args.label}", payload, command=" ".join(sys.argv))
    print(json_dumps(payload))
    print(f"wrote {path}")
    return 0


def json_dumps(d: dict) -> str:
    import json

    return json.dumps(d, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["wer", "agreement"], required=True)
    ap.add_argument("--wav", required=True)
    ap.add_argument("--text", default=None)
    ap.add_argument("--label", required=True)
    ap.add_argument("--model", default="models/whisper-base")
    ap.add_argument("--models", default="base,small")
    ap.add_argument("--vad", type=float, default=0.006)
    args = ap.parse_args()
    if args.mode == "wer":
        if not args.text:
            raise SystemExit("--text is required in wer mode (there is no default reference)")
        return mode_wer(args)
    return mode_agreement(args)


if __name__ == "__main__":
    raise SystemExit(main())
