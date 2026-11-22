#!/usr/bin/env python3
"""Measure Bengali ASR quality before/after the model-selection fix.

Reference pairs come from ``data/guru.db`` (``segments.bn``) whose synthetic
Bengali audio was rendered with Piper ``bn_BD`` and stored under
``data/audio/<session>/<segment_id>.wav``.  These are TTS clips, not human
microphone recordings: absolute WER is therefore optimistic, but the
before/after comparison is like-for-like and valid.

    .venv/bin/python scripts/eval_bn_asr.py
    .venv/bin/python scripts/eval_bn_asr.py --models base,bn --out reports/results/bangla_asr_eval.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from guru.config import load_settings

SAMPLE_RATE = 16000
BENGALI = re.compile(r"[\u0980-\u09FF]")
LETTERS = re.compile(r"[\u0980-\u09FFA-Za-z]")


# ---------------------------------------------------------------------------
# text metrics
# ---------------------------------------------------------------------------
def normalise(text: str) -> str:
    """Lowercase and strip punctuation, keeping Bengali script and digits."""
    text = (text or "").lower().replace("\u09bc", "")
    text = re.sub(r"[^\u0980-\u09FF\u09E6-\u09EFa-z0-9 ]+", " ", text)
    return " ".join(text.split())


def edit_distance(ref: list[str], hyp: list[str]) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1]


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = reference.split(), hypothesis.split()
    return 0.0 if not ref else edit_distance(ref, hyp) / len(ref)


def cer(reference: str, hypothesis: str) -> float:
    ref, hyp = list(reference.replace(" ", "")), list(hypothesis.replace(" ", ""))
    return 0.0 if not ref else edit_distance(ref, hyp) / len(ref)


def bengali_ratio(text: str) -> float:
    letters = len(LETTERS.findall(text or ""))
    return 0.0 if letters == 0 else len(BENGALI.findall(text)) / letters


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------
def load_wav(path: str) -> np.ndarray:
    """Read any PCM WAV into 16 kHz mono float32 (linear resample)."""
    import wave

    with wave.open(path) as handle:
        channels = handle.getnchannels()
        rate = handle.getframerate()
        width = handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    audio = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if width == 1:
        audio = (audio - 128.0) / 128.0
    else:
        audio /= float(np.iinfo(dtype).max)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        n = round(len(audio) * SAMPLE_RATE / rate)
        audio = np.interp(
            np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio
        ).astype(np.float32)
    return audio


def reference_pairs(db_path: Path) -> list[dict]:
    wavs = {
        os.path.splitext(os.path.basename(p))[0]: p
        for p in glob.glob(str(ROOT / "data" / "audio" / "**" / "*.wav"), recursive=True)
    }
    rows: list[dict] = []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    for row in con.execute(
        "select id, session_id, bn, en, source_text from segments where bn != ''"
    ):
        wav = wavs.get(row["id"])
        # A handful of database rows are not real sentences (e.g. a run of "m");
        # they would dominate a mean WER without saying anything about ASR.
        if wav and BENGALI.search(row["bn"] or ""):
            rows.append({"id": row["id"], "wav": wav, "bn": row["bn"], "en": row["en"]})
    con.close()
    return sorted(rows, key=lambda r: r["id"])


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------
def evaluate(models: list[str], settings, pairs: list[dict], limit: int | None) -> dict:
    import psutil
    from faster_whisper import WhisperModel

    if limit:
        pairs = pairs[:limit]
    process = psutil.Process()
    out: dict[str, dict] = {}
    for key in models:
        path = settings.asr.model_dir if key == "base" else settings.asr.bn_model_dir
        path = settings.resolve(path)
        if not path or not path.is_dir():
            print(f"[{key}] missing model at {path}", file=sys.stderr)
            continue
        rss0 = process.memory_info().rss
        t_load = time.perf_counter()
        model = WhisperModel(
            str(path),
            device="cpu",
            compute_type=settings.asr.compute_type,
            cpu_threads=settings.asr.cpu_threads,
            local_files_only=True,
        )
        load_s = time.perf_counter() - t_load
        rss_loaded = process.memory_info().rss
        rows = []
        for pair in pairs:
            audio = load_wav(pair["wav"])
            t0 = time.perf_counter()
            segments, _info = model.transcribe(
                audio,
                language="bn",
                beam_size=settings.asr.beam_size,
                vad_filter=False,
                condition_on_previous_text=False,
                temperature=0.0,
                without_timestamps=True,
            )
            hypothesis = " ".join(s.text.strip() for s in segments).strip()
            latency_s = time.perf_counter() - t0
            ref = normalise(pair["bn"])
            hyp = normalise(hypothesis)
            rows.append(
                {
                    "id": pair["id"],
                    "audio_s": round(len(audio) / SAMPLE_RATE, 2),
                    "reference": pair["bn"],
                    "hypothesis": hypothesis,
                    "wer": round(wer(ref, hyp), 3),
                    "cer": round(cer(ref, hyp), 3),
                    "bengali_ratio": round(bengali_ratio(hypothesis), 3),
                    "char_completeness": round(len(hyp) / len(ref), 3) if ref else 0.0,
                    "latency_s": round(latency_s, 2),
                    "rtf": round(latency_s / max(0.1, len(audio) / SAMPLE_RATE), 2),
                }
            )
            print(
                f"[{key}] {pair['id']} wer={rows[-1]['wer']:.2f} "
                f"cer={rows[-1]['cer']:.2f} bn={rows[-1]['bengali_ratio']:.2f} "
                f"rtf={rows[-1]['rtf']:.2f}"
            )
        total_audio = sum(r["audio_s"] for r in rows) or 1.0
        out[key] = {
            "model_dir": str(path),
            "load_s": round(load_s, 2),
            "rss_delta_mb": round((rss_loaded - rss0) / 2**20, 1),
            "clips": len(rows),
            "audio_s": round(total_audio, 1),
            "wer": round(sum(r["wer"] for r in rows) / len(rows), 3),
            "wer_median": round(float(np.median([r["wer"] for r in rows])), 3),
            "clips_wer_le_0_3": sum(1 for r in rows if r["wer"] <= 0.3),
            "cer": round(sum(r["cer"] for r in rows) / len(rows), 3),
            "bengali_ratio": round(sum(r["bengali_ratio"] for r in rows) / len(rows), 3),
            "char_completeness": round(
                sum(r["char_completeness"] for r in rows) / len(rows), 3
            ),
            "rtf": round(sum(r["latency_s"] for r in rows) / total_audio, 2),
            "clips_with_bengali_script": sum(1 for r in rows if r["bengali_ratio"] > 0.5),
            "rows": rows,
        }
        del model
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="base,bn",
                    help="comma list of 'base' (asr.model_dir) and 'bn' (asr.bn_model_dir)")
    ap.add_argument("--db", default="data/guru.db")
    ap.add_argument("--limit", type=int, default=0, help="evaluate only the first N clips")
    ap.add_argument("--out", default="reports/results/bangla_asr_eval.json")
    args = ap.parse_args()

    settings = load_settings()
    pairs = reference_pairs(ROOT / args.db)
    if not pairs:
        raise SystemExit("no reference pairs with audio found")
    print(f"{len(pairs)} reference clips from {args.db}")
    result = evaluate(
        [m.strip() for m in args.models.split(",") if m.strip()],
        settings,
        pairs,
        args.limit or None,
    )
    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "corpus": "Piper bn_BD synthetic speech paired with segments.bn",
        "beam_size": settings.asr.beam_size,
        "models": result,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nwrote {out_path}")
    for key, data in result.items():
        print(
            f"{key:>5}: WER {data['wer']:.2f} (median {data['wer_median']:.2f})  "
            f"CER {data['cer']:.2f}  "
            f"Bengali-script clips {data['clips_with_bengali_script']}/{data['clips']}  "
            f"RTF {data['rtf']:.2f}  +{data['rss_delta_mb']:.0f} MB"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
