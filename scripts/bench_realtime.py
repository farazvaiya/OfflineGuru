"""Reproducible real-time benchmark for the streaming pipeline.

Feeds a WAV file through the *real* models at real-time pace and records what a
student would actually experience: when the first Bangla caption appears, when
the first Bangla audio is ready, and how each stage behaves under load.

Usage
-----
    .venv/bin/python scripts/bench_realtime.py --wav data/demo/lecture.wav
    .venv/bin/python scripts/bench_realtime.py --chunks 0.5,1.0,2.0 --seconds 45
    .venv/bin/python scripts/bench_realtime.py --model models/whisper-tiny

The output JSON is written to reports/results/realtime_bench.json by default.
Nothing here is simulated: if a stage is slow, the numbers say so.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from guru.asr import WhisperAsr  # noqa: E402
from guru.bus import EventBus  # noqa: E402
from guru.config import load_settings  # noqa: E402
from guru.pipeline import ClassroomPipeline  # noqa: E402
from guru.storage import Storage  # noqa: E402
from guru.translate import build_translator  # noqa: E402
from guru.tts import NullTts, PiperTts  # noqa: E402

SAMPLE_RATE = 16000


def describe(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    n = len(ordered)

    def pct(p: float) -> float:
        return round(ordered[min(n - 1, max(0, round((p / 100) * (n - 1))))], 1)

    return {
        "n": n,
        "mean": round(statistics.fmean(ordered), 1),
        "p50": pct(50),
        "p95": pct(95),
        "max": round(ordered[-1], 1),
    }


class RssSampler:
    """Sample this process' RSS in the background."""

    def __init__(self, interval: float = 0.25) -> None:
        import psutil

        self._proc = psutil.Process()
        self._interval = interval
        self._stop = threading.Event()
        self.peak_mb = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                rss = self._proc.memory_info().rss / 1e6
                self.peak_mb = max(self.peak_mb, rss)
            except Exception:  # pragma: no cover
                return
            self._stop.wait(self._interval)

    def __enter__(self) -> "RssSampler":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)


async def run_config(
    settings,
    audio: np.ndarray,
    chunk_s: float,
    asr,
    translator,
    tts,
    workdir: Path,
    fresh_tts_cache: bool = False,
) -> dict:
    if fresh_tts_cache:
        # PiperTts reports ~0 ms on a cache hit. Wiping the cache before every
        # config keeps the TTS column an honest cold-synthesis measurement
        # instead of a mix of real work and cache lookups.
        cache_dir = Path(getattr(tts, "cache_dir", workdir / "tts_cache"))
        if cache_dir.is_dir():
            for wav in cache_dir.glob("*.wav"):
                wav.unlink()
    db_path = workdir / f"bench_{chunk_s}.db"
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(db_path) + suffix)
        if candidate.exists():
            candidate.unlink()
    storage = Storage(db_path)
    bus = EventBus()
    pipeline = ClassroomPipeline(settings, bus, storage, asr, translator, tts)
    # Unique join code per run: the code column is UNIQUE across the database.
    code = f"B{int(chunk_s * 100):03d}{int(time.time()) % 1000:03d}"
    session_id = storage.create_session("bench", "tok", code)
    sub = bus.subscribe("teacher", session_id)

    events: list[dict] = []
    speech_started_at: float | None = None
    first_caption_ms: float | None = None
    first_audio_ms: float | None = None
    # Per-segment latency is measured from the moment the phrase *ended in the
    # audio*, not from the start of the lecture: that is what a student feels.
    caption_latencies: list[float] = []
    audio_latencies: list[float] = []
    seqs: list[int] = []
    feed_started_at = time.perf_counter()

    async def drain() -> None:
        nonlocal speech_started_at, first_caption_ms, first_audio_ms
        while True:
            try:
                event = sub.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            events.append(event)
            kind = event.get("type")
            now = time.perf_counter()
            if kind == "pipeline.status" and event.get("status") == "speech":
                if speech_started_at is None:
                    speech_started_at = now
            elif kind == "segment":
                seg = event["segment"]
                seqs.append(seg["seq"])
                phrase_end_wall = feed_started_at + float(seg["end_s"])
                latency = (now - phrase_end_wall) * 1000
                caption_latencies.append(latency)
                if first_caption_ms is None and speech_started_at is not None:
                    first_caption_ms = (now - speech_started_at) * 1000
            elif kind == "audio.ready":
                seg = next(
                    (s for s in events if s.get("type") == "segment"
                     and s["segment"]["seq"] == event.get("sequence_number")),
                    None,
                )
                if seg is not None:
                    phrase_end_wall = feed_started_at + float(seg["segment"]["end_s"])
                    latency = (now - phrase_end_wall) * 1000
                    audio_latencies.append(latency)
                if first_audio_ms is None and speech_started_at is not None:
                    first_audio_ms = (now - speech_started_at) * 1000

    block = max(1, int(chunk_s * SAMPLE_RATE))
    started = time.perf_counter()
    with RssSampler() as rss:
        pipeline.start(session_id, asyncio.get_running_loop())
        for i in range(0, len(audio), block):
            pipeline.submit_audio(audio[i : i + block])
            await asyncio.sleep(chunk_s)
            await drain()
        await asyncio.to_thread(pipeline.stop)
        await drain()
    wall_s = time.perf_counter() - started

    stored = storage.list_segments(session_id)
    health = pipeline.health()
    stats = health["stats"]
    result = {
        "chunk_s": chunk_s,
        "audio_s": round(len(audio) / SAMPLE_RATE, 1),
        "wall_s": round(wall_s, 1),
        "segments": len(stored),
        "duplicate_seqs": len(seqs) - len(set(seqs)),
        "first_caption_ms": round(first_caption_ms, 1) if first_caption_ms else None,
        "first_audio_ms": round(first_audio_ms, 1) if first_audio_ms else None,
        "caption_latency_ms": describe(caption_latencies),
        "audio_latency_ms": describe(audio_latencies),
        "caption_latency_note": "ms from phrase end in the audio to caption delivery",
        "e2e_note": "e2e_ms = phrase finalised -> caption emitted (translate + emit); "
                    "audio_e2e_ms = phrase finalised -> Bangla audio ready (translate + TTS)",
        "asr_ms": health["latency"].get("asr_ms", {"n": 0}),
        "asr_rtf": health["latency"].get("asr_rtf", {"n": 0}),
        "translate_ms": health["latency"].get("translate_ms", {"n": 0}),
        "tts_ms": health["latency"].get("tts_ms", {"n": 0}),
        "e2e_ms": health["latency"].get("e2e_ms", {"n": 0}),
        "audio_e2e_ms": health["latency"].get("audio_e2e_ms", {"n": 0}),
        "stream_decodes": stats["stream_decodes"],
        "stream_backlog": stats["stream_backlog"],
        "stream_phrases": stats["stream_phrases"],
        "asr_queue_drops": stats["asr_queue_drops"],
        "enrichment_drops": stats["enrichment_queue_drops"],
        "tts_drops": stats["tts_queue_drops"],
        "tts_failures": stats["tts_failures"],
        "translate_failures": stats["translate_failures"],
        "peak_rss_mb": round(rss.peak_mb, 1),
        "audio_ready": sum(1 for s in stored if s.get("audio_path")),
    }
    storage.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", default="data/demo/lecture.wav")
    parser.add_argument("--chunks", default="0.5,0.75,1.0,1.5,2.0")
    parser.add_argument("--seconds", type=float, default=45.0,
                        help="excerpt length; 0 = whole file")
    parser.add_argument("--model", default=None, help="override ASR model dir")
    parser.add_argument("--out", default="reports/results/realtime_bench.json")
    parser.add_argument("--no-tts", action="store_true")
    parser.add_argument(
        "--fresh-tts-cache",
        action="store_true",
        help="wipe the TTS cache before each config so tts_ms is cold synthesis",
    )
    args = parser.parse_args()

    import soundfile as sf

    wav = ROOT / args.wav if not Path(args.wav).is_absolute() else Path(args.wav)
    data, sr = sf.read(str(wav), dtype="float32")
    if sr != SAMPLE_RATE:
        raise SystemExit(f"{wav}: expected {SAMPLE_RATE} Hz, got {sr}")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if args.seconds and args.seconds > 0:
        data = data[: int(args.seconds * SAMPLE_RATE)]

    settings = load_settings()
    if args.model:
        settings.asr.model_dir = args.model
    settings.asr.streaming = True
    settings.storage.data_dir = "data/bench"
    settings.storage.db_path = "data/bench/bench.db"

    asr = WhisperAsr(settings)
    translator = build_translator(settings)
    tts = NullTts() if args.no_tts else PiperTts(settings)
    if args.fresh_tts_cache and not args.no_tts:
        settings.tts.cache_dir = "data/bench/tts_cache"
        tts.cache_dir = ROOT / "data" / "bench" / "tts_cache"

    print(f"loading models ({settings.asr.model_dir}) ...", flush=True)
    for name, obj in (("asr", asr), ("translator", translator), ("tts", tts)):
        t0 = time.perf_counter()
        try:
            obj.load()
            print(f"  {name}: {(time.perf_counter() - t0) * 1000:.0f} ms", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  {name}: UNAVAILABLE ({exc})", flush=True)

    workdir = ROOT / "data" / "bench"
    workdir.mkdir(parents=True, exist_ok=True)
    results = []
    for chunk in [float(c) for c in args.chunks.split(",") if c.strip()]:
        settings.asr.stream_step_s = chunk
        print(f"\n=== chunk {chunk}s ===", flush=True)
        result = asyncio.run(
            run_config(
                settings, data, chunk, asr, translator, tts, workdir,
                fresh_tts_cache=args.fresh_tts_cache,
            )
        )
        results.append(result)
        print(json.dumps(result, indent=2), flush=True)

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "wav": str(wav),
        "audio_s": round(len(data) / SAMPLE_RATE, 1),
        "asr_model": str(settings.asr.model_dir),
        "compute_type": settings.asr.compute_type,
        "cpu_threads": settings.asr.cpu_threads,
        "translate_backend": settings.translate.backend,
        "tts_enabled": not args.no_tts,
        "tts_cache": "cold (wiped before each config)" if args.fresh_tts_cache else "warm",
        "results": results,
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {out}", flush=True)


if __name__ == "__main__":
    main()
