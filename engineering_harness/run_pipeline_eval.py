#!/usr/bin/env python3
"""Measure the *streaming* pipeline against a reference transcript.

This is the harness's missing-speech instrument. It replays a WAV through the real
ClassroomPipeline at real-time pace (ASR only: translation and TTS are disabled so
the measurement isolates capture -> VAD -> streaming ASR -> caption), then scores
what a student would have received against the known transcript.

Why real-time pace matters: the streaming loop's cadence (stream_step_s, the
end-of-speech flush, phrase ageing) depends on wall-clock time. Replaying faster
would produce a different — and flatteringly better — decode schedule.

Everything writes to a throwaway data directory, so a benchmark can never touch
data/guru.db or a class recording.

Usage
-----
    .venv/bin/python engineering_harness/run_pipeline_eval.py \
        --wav data/demo/lecture.wav --text data/demo/lecture.txt --label baseline
    .venv/bin/python engineering_harness/run_pipeline_eval.py ... --no-streaming
    .venv/bin/python engineering_harness/run_pipeline_eval.py ... --override asr.commit_confidence=0.0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import evidence  # noqa: E402
from guru.asr import WhisperAsr  # noqa: E402
from guru.audio import WavSource  # noqa: E402
from guru.bus import EventBus  # noqa: E402
from guru.config import load_settings  # noqa: E402
from guru.pipeline import ClassroomPipeline  # noqa: E402
from guru.storage import Storage  # noqa: E402
from guru.translate import PassthroughTranslator  # noqa: E402
from guru.tts import NullTts  # noqa: E402


def normalise(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return [w for w in text.split() if w]


def wer(reference: list[str], hypothesis: list[str]) -> float:
    if not reference:
        return 0.0
    prev = list(range(len(hypothesis) + 1))
    for i, r in enumerate(reference, 1):
        cur = [i]
        for j, h in enumerate(hypothesis, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(reference)


def apply_override(settings, dotted: str) -> None:
    key, _, raw = dotted.partition("=")
    section, _, field = key.partition(".")
    if not field:
        raise SystemExit(f"override must look like section.field=value: {dotted}")
    target = getattr(settings, section)
    current = getattr(target, field)
    if isinstance(current, bool):
        value: object = raw.lower() in ("1", "true", "yes", "on")
    elif isinstance(current, int):
        value = int(raw)
    elif isinstance(current, float):
        value = float(raw)
    else:
        value = raw
    setattr(target, field, value)


def describe(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    n = len(ordered)

    def pct(p: float) -> float:
        return round(ordered[min(n - 1, max(0, round((p / 100) * (n - 1))))], 1)

    return {"n": n, "mean": round(sum(ordered) / n, 1), "p50": pct(50), "p95": pct(95),
            "max": round(ordered[-1], 1)}


class Sampler:
    """Sample process RSS and system CPU while the pipeline runs."""

    def __init__(self, interval: float = 0.5) -> None:
        self.interval = interval
        self.samples: list[dict[str, float]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        import psutil

        proc = psutil.Process()
        psutil.cpu_percent(None)
        while not self._stop.wait(self.interval):
            self.samples.append({
                "cpu_pct": psutil.cpu_percent(None),
                "rss_mb": proc.memory_info().rss / 1e6,
            })

    def __enter__(self) -> "Sampler":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def summary(self) -> dict[str, float]:
        cpus = [s["cpu_pct"] for s in self.samples]
        rss = [s["rss_mb"] for s in self.samples]
        return {
            "cpu_pct_mean": round(sum(cpus) / len(cpus), 1) if cpus else 0.0,
            "cpu_pct_max": round(max(cpus), 1) if cpus else 0.0,
            "rss_mb_start": round(rss[0], 1) if rss else 0.0,
            "rss_mb_peak": round(max(rss), 1) if rss else 0.0,
            "samples": len(cpus),
        }


async def run(args: argparse.Namespace) -> dict:
    settings = load_settings()
    settings.asr.model_dir = args.model
    settings.asr.streaming = bool(args.streaming)
    for ov in args.override or []:
        apply_override(settings, ov)
    # ASR-only measurement: translation and TTS would add their own queueing and
    # blur what this instrument is for.
    settings.translate.enabled = False
    settings.tts.enabled = False
    settings.storage.keep_audio = False
    settings.storage.keep_recordings = False

    workdir = Path("/tmp/og/eval") / args.label
    (workdir / "data").mkdir(parents=True, exist_ok=True)
    settings.storage.data_dir = str(workdir / "data")
    settings.storage.db_path = str(workdir / "data" / "eval.db")
    (workdir / "data" / "eval.db").unlink(missing_ok=True)

    storage = Storage(settings.resolve(settings.storage.db_path))
    bus = EventBus()
    asr = WhisperAsr(settings)
    pipeline = ClassroomPipeline(settings, bus, storage, asr,
                                 PassthroughTranslator(), NullTts())
    session = storage.create_session("harness eval", "bench", "HARN01", "en", "bn")
    sid = session

    delivered: list[dict] = []
    first_event_ts: dict[str, float] = {}
    feed_start = time.perf_counter()

    pipeline.start(sid, asyncio.get_running_loop())
    sub = bus.subscribe("teacher", sid)
    pipeline.set_final_callback(lambda rec: None)

    stop_consumer = asyncio.Event()

    async def consume() -> None:
        while not stop_consumer.is_set():
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=0.2)
            except TimeoutError:
                continue
            kind = event.get("type")
            if kind in ("transcript.final", "segment", "transcript.stable"):
                first_event_ts.setdefault(kind, round((time.perf_counter() - feed_start) * 1000, 1))
            if kind in ("segment", "transcript.final"):
                delivered.append({"type": kind, "t": time.perf_counter() - feed_start,
                                  "event": event})

    consumer = asyncio.create_task(consume())
    wav = evidence.REPO / args.wav
    if not wav.is_file():
        raise SystemExit(f"wav missing: {wav}")

    with Sampler() as sampler:
        def feed() -> None:
            for chunk in WavSource(wav, speed=args.speed).frames(chunk_ms=100):
                pipeline.submit_audio(np.asarray(chunk))
                if args.limit_s and time.perf_counter() - feed_start >= args.limit_s:
                    return

        await asyncio.to_thread(feed)
        await asyncio.to_thread(pipeline.stop)
        await asyncio.sleep(0.5)
    stop_consumer.set()
    consumer.cancel()

    wall_s = time.perf_counter() - feed_start
    segments = storage.list_segments(sid)
    stats = pipeline.stats
    health = pipeline.health()
    pipeline.set_final_callback(None)

    hyp = normalise(" ".join(s["en"] for s in segments))
    texts = [s["en"].strip() for s in segments]
    reference = normalise((evidence.REPO / args.text).read_text()) if args.text else []
    speech_s = sum(float(s["end_s"]) - float(s["start_s"]) for s in segments)
    empty = sum(1 for t in texts if not normalise(t))
    short = sum(1 for t in texts if len(normalise(t)) <= 1)

    payload = {
        "label": args.label,
        "mode": "streaming" if args.streaming else "segment",
        "wav": args.wav,
        "reference": args.text,
        "model": args.model,
        "overrides": args.override or [],
        "speed": args.speed,
        "wall_s": round(wall_s, 1),
        "segments": len(segments),
        "speech_s": round(speech_s, 1),
        "reference_words": len(reference),
        "hypothesis_words": len(hyp),
        "coverage_vs_reference": round(len(hyp) / max(1, len(reference)), 3) if reference else None,
        "wer": round(wer(reference, hyp), 4) if reference else None,
        "empty_segments": empty,
        "single_word_segments": short,
        "first_event_ms": first_event_ts,
        "stats": {
            "first_caption_ms": stats.first_caption_ms,
            "segments_finalized": stats.segments_finalized,
            "segments_translated": stats.segments_translated,
            "segments_empty": stats.segments_empty,
            "asr_queue_drops": stats.asr_queue_drops,
            "enrichment_queue_drops": stats.enrichment_queue_drops,
            "stream_decodes": stats.stream_decodes,
            "stream_commits": stats.stream_commits,
            "stream_phrases": stats.stream_phrases,
            "stream_backlog": stats.stream_backlog,
        },
        "latency": health.get("latency", {}),
        "resources": sampler.summary(),
        "texts": texts,
    }
    storage.purge_session(sid)
    storage.close()
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default="data/demo/lecture.wav")
    ap.add_argument("--text", default="data/demo/lecture.txt")
    ap.add_argument("--label", required=True)
    ap.add_argument("--model", default="models/whisper-base")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--limit-s", type=float, default=0.0)
    ap.add_argument("--override", action="append", default=None)
    ap.add_argument("--no-streaming", dest="streaming", action="store_false")
    ap.set_defaults(streaming=True)
    args = ap.parse_args()
    payload = asyncio.run(run(args))
    path = evidence.write(f"pipeline_{args.label}", payload, command=" ".join(sys.argv))
    printable = {k: v for k, v in payload.items() if k != "texts"}
    print(json.dumps(printable, indent=2))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
