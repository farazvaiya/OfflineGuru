#!/usr/bin/env python3
"""Real ASR-under-upload benchmark: Whisper transcribing while a deck is extracted.

The earlier report measured +4 % on a synthetic CPU loop, which is not the same
thing as speech recognition sharing the interpreter and the CPU with document
extraction. This script loads the actual local Whisper model, transcribes real
audio, and repeats it while a 150-slide PPTX is being extracted and indexed in
another thread.

    .venv/bin/python scripts/bench_asr_upload.py --repeats 3

Everything runs offline: models come from ``models/``, the deck is generated in
memory, and the audio is a local WAV.
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import threading
import time
import wave
import zipfile
from pathlib import Path

import numpy as np
import psutil

ROOT = Path(__file__).resolve().parent.parent

import sys

sys.path.insert(0, str(ROOT))

from guru.asr import WhisperAsr
from guru.config import load_settings
from guru.education.documents import chunk_pages, extract_pages

SLIDE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
 <p:cSld><p:spTree><p:sp><p:txBody>{paras}</p:txBody></p:sp></p:spTree></p:cSld>
</p:sld>"""


def build_pptx(slides: int = 150) -> bytes:
    """A minimal deck the project's own extractor can read (no external tools)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(1, slides + 1):
            paras = "".join(
                f"<a:p><a:r><a:t>Slide {i}: photosynthesis converts light energy "
                f"into chemical energy inside the chloroplast, step {j}.</a:t></a:r></a:p>"
                for j in range(1, 4)
            )
            zf.writestr(f"ppt/slides/slide{i}.xml", SLIDE_XML.format(paras=paras))
    return buf.getvalue()


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    return audio, rate


def timed_extract(deck: bytes, out: list[float]) -> None:
    """Extract and chunk a deck on a worker thread, appending its elapsed ms.

    Module-level (not a closure) so the thread target cannot capture a loop
    variable by reference.
    """
    t0 = time.perf_counter()
    pages = extract_pages(deck, "deck.pptx")
    chunk_pages(pages)
    out.append((time.perf_counter() - t0) * 1000)


def transcribe_window(asr: WhisperAsr, audio: np.ndarray, rate: int, seconds: float = 6.0,
                      offset_s: float = 0.0) -> float:
    start = int(offset_s * rate)
    stop = start + int(seconds * rate)
    chunk = audio[start:stop]
    t0 = time.perf_counter()
    asr.transcribe(chunk)
    return (time.perf_counter() - t0) * 1000


def stats(values: list[float]) -> dict[str, float]:
    s = sorted(values)
    return {
        "n": len(s),
        "mean": round(statistics.fmean(s), 1),
        "p50": round(statistics.median(s), 1),
        "p95": round(s[min(len(s) - 1, round(0.95 * (len(s) - 1)))], 1),
        "min": round(s[0], 1),
        "max": round(s[-1], 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default="data/demo/lecture_45s.wav")
    ap.add_argument("--slides", type=int, default=150)
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()

    settings = load_settings()
    asr = WhisperAsr(settings)
    load_t0 = time.perf_counter()
    asr.load()
    load_s = round(time.perf_counter() - load_t0, 2)

    audio, rate = read_wav(ROOT / args.wav)
    duration_s = round(len(audio) / rate, 1)
    deck = build_pptx(args.slides)
    proc = psutil.Process()

    print(f"model loaded in {load_s}s; audio {duration_s}s; deck {args.slides} slides "
          f"({len(deck) / 1024:.0f} kB)")

    # 1. extraction alone
    extract_ms: list[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        pages = extract_pages(deck, "deck.pptx")
        chunks = chunk_pages(pages)
        extract_ms.append((time.perf_counter() - t0) * 1000)
    print(f"extraction alone: {stats(extract_ms)} pages={len(pages)} chunks={len(chunks)}")

    # 2. ASR alone
    baseline: list[float] = []
    for rep in range(args.repeats):
        baseline.append(transcribe_window(asr, audio, rate, offset_s=rep * 6.0))
    print(f"ASR alone:        {stats(baseline)}")

    # 3. ASR while the same deck is extracted in a worker thread
    rss_before = proc.memory_info().rss / 1e6
    concurrent: list[float] = []
    extract_while: list[float] = []
    for rep in range(args.repeats):
        box: list[float] = []
        worker = threading.Thread(target=timed_extract, args=(deck, box))
        worker.start()
        concurrent.append(transcribe_window(asr, audio, rate, offset_s=rep * 6.0))
        worker.join()
        extract_while.append(box[0])

    rss_after = proc.memory_info().rss / 1e6
    print(f"ASR + extraction: {stats(concurrent)}")
    print(f"extraction under ASR load: {stats(extract_while)}")
    print(f"RSS: {rss_before:.0f} MB -> {rss_after:.0f} MB")

    baseline_mean = statistics.fmean(baseline)
    with_load_mean = statistics.fmean(concurrent)
    delta = (with_load_mean - baseline_mean) / baseline_mean * 100
    print(f"\nASR slowdown while extracting: {delta:+.1f}% "
          f"({baseline_mean:.0f} ms -> {with_load_mean:.0f} ms)")

    out = {
        "audio": args.wav,
        "audio_s": duration_s,
        "slides": args.slides,
        "model_load_s": load_s,
        "extraction_alone_ms": stats(extract_ms),
        "extraction_under_asr_ms": stats(extract_while),
        "asr_alone_ms": stats(baseline),
        "asr_with_extraction_ms": stats(concurrent),
        "asr_slowdown_pct": round(delta, 1),
        "rss_mb": [round(rss_before, 1), round(rss_after, 1)],
        "chunks": len(chunks),
        "note": "Real Whisper inference; the deck is extracted on a worker thread "
                "in the same process, which is how the upload endpoint runs it.",
    }
    path = ROOT / "reports" / "results" / "asr_upload_contention.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
