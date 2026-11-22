#!/usr/bin/env python3
"""End-to-end latency benchmark on a real lecture file, with real models.

This is a **file-based** benchmark, not a physical lecture: the audio comes from
``data/demo`` and is replayed in real time by ``WavSource`` at exactly the rate a
microphone would produce it. Every number below is read from the pipeline's own
instrumentation (the same columns the teacher console reports), so nothing is
estimated from an unrelated operation.

    .venv/bin/python scripts/bench_latency.py --wav data/demo/lecture_45s.wav

Stages are reported separately because they fail differently: ASR is the CPU
owner, translation is the serialisation point, TTS competes with both.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from guru.asr import WhisperAsr
from guru.audio import WavSource
from guru.bus import EventBus
from guru.config import load_settings
from guru.pipeline import ClassroomPipeline
from guru.storage import Storage
from guru.translate import build_translator
from guru.tts import NullTts, PiperTts


def describe(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    s = sorted(values)

    def pct(p: float) -> float:
        return round(s[min(len(s) - 1, max(0, round((p / 100) * (len(s) - 1))))], 1)

    return {
        "n": len(s),
        "mean": round(statistics.fmean(s), 1),
        "min": round(s[0], 1),
        "p50": pct(50),
        "p95": pct(95),
        "p99": pct(99),
        "max": round(s[-1], 1),
    }


async def run(args: argparse.Namespace) -> dict:
    settings = load_settings()
    storage = Storage(settings.resolve(settings.storage.db_path))
    bus = EventBus()
    asr = WhisperAsr(settings)
    translator = build_translator(settings)
    tts = PiperTts(settings) if settings.tts.enabled else NullTts()
    pipeline = ClassroomPipeline(settings, bus, storage, asr, translator, tts)

    session_id = storage.create_session("latency benchmark", "bench-token", "BENCH1", "en", "bn")
    pipeline.start(session_id, asyncio.get_running_loop())
    sub = bus.subscribe("teacher", session_id)
    delivered: list[float] = []

    async def consume() -> None:
        while True:
            event = await sub.queue.get()
            if event.get("type") == "segment":
                delivered.append(time.perf_counter())

    consumer = asyncio.create_task(consume())

    # Sample the process while the lecture runs: CPU is what limits a classroom
    # laptop, and RSS growth over one lecture is what would crash it.
    import psutil

    proc = psutil.Process()
    proc.cpu_percent(None)
    samples: list[dict[str, float]] = []

    async def sample() -> None:
        while True:
            await asyncio.sleep(0.5)
            samples.append({"cpu": psutil.cpu_percent(None),
                            "rss_mb": proc.memory_info().rss / 1e6})

    sampler = asyncio.create_task(sample())
    started = time.perf_counter()

    def feed() -> None:
        for chunk in WavSource(args.wav, speed=1.0).frames(chunk_ms=100):
            pipeline.submit_audio(chunk)

    await asyncio.to_thread(feed)
    await asyncio.to_thread(pipeline.stop)
    await asyncio.sleep(0.3)
    consumer.cancel()
    sampler.cancel()
    wall_s = time.perf_counter() - started
    cpus = [s["cpu"] for s in samples]
    rss = [s["rss_mb"] for s in samples]
    resources = {
        "cpu_pct_mean": round(statistics.fmean(cpus), 1) if cpus else 0.0,
        "cpu_pct_max": round(max(cpus), 1) if cpus else 0.0,
        "rss_mb_start": round(rss[0], 1) if rss else 0.0,
        "rss_mb_end": round(rss[-1], 1) if rss else 0.0,
        "rss_mb_peak": round(max(rss), 1) if rss else 0.0,
        "samples": len(samples),
    }

    segments = storage.list_segments(session_id)
    stages = {
        "asr_ms": [float(s["asr_ms"] or 0) for s in segments],
        "translate_ms": [float(s["translate_ms"] or 0) for s in segments],
        "tts_ms": [float(s["tts_ms"] or 0) for s in segments],
        "e2e_text_ms": [float(s["total_ms"] or 0) for s in segments],
    }
    speech_s = sum(float(s["end_s"]) - float(s["start_s"]) for s in segments)
    result = {
        "benchmark": "file-replay (not a physical microphone lecture)",
        "wav": args.wav,
        "wall_s": round(wall_s, 1),
        "speech_s": round(speech_s, 1),
        "segments": len(segments),
        "asr_drops": pipeline.stats.asr_queue_drops,
        "resources": resources,
        "stages": {name: describe(values) for name, values in stages.items()},
        "tts_audio_available_ms": describe([float(s["total_ms"] or 0) for s in segments]),
        "note": (
            "e2e_text_ms is speech-end -> translated caption published, measured by the "
            "pipeline. TTS is disk-cached per text, so tts_ms is near zero on a repeat run."
        ),
        "models": {
            "asr": settings.asr.model_dir,
            "translate": settings.translate.backend,
            "tts": settings.tts.voice_onnx,
        },
    }
    storage.purge_session(session_id)
    storage.close()
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default="data/demo/lecture_45s.wav")
    ap.add_argument("--out", default="reports/results/latency.json")
    args = ap.parse_args()

    result = asyncio.run(run(args))
    print(f"segments: {result['segments']}  wall: {result['wall_s']}s  "
          f"speech: {result['speech_s']}s  drops: {result['asr_drops']}")
    res = result["resources"]
    print(f"cpu: mean {res['cpu_pct_mean']}%  max {res['cpu_pct_max']}%  "
          f"rss {res['rss_mb_start']:.0f}->{res['rss_mb_end']:.0f} MB "
          f"(peak {res['rss_mb_peak']:.0f})")
    print(f"\n{'stage':14} {'n':>3} {'p50':>8} {'p95':>8} {'p99':>8} {'max':>8}")
    for name, stats in result["stages"].items():
        if stats.get("n"):
            print(f"{name:14} {stats['n']:>3} {stats['p50']:>8} {stats['p95']:>8} "
                  f"{stats['p99']:>8} {stats['max']:>8}")

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(f"\nwrote {out}")
    return 0 if result["segments"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
