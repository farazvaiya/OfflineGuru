"""Read-only probe: does enrichment/TTS backlog starve the streaming ASR worker?

Replays a WAV through the real pipeline with the real translator and TTS (unlike
run_pipeline_eval, which disables both) and samples pipeline.health() so the
enrichment and TTS queue depths and the streaming backlog can be observed.
No project files are modified.
"""

import asyncio
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path("/home/faraz/project/offline_guru")))

from guru.asr import WhisperAsr
from guru.audio import WavSource
from guru.bilingual import BidirectionalPiperTts, BidirectionalTranslator
from guru.bus import EventBus
from guru.config import load_settings
from guru.pipeline import ClassroomPipeline
from guru.storage import Storage


async def main() -> None:
    settings = load_settings()
    work = Path("/tmp/og/contention")
    (work / "data").mkdir(parents=True, exist_ok=True)
    settings.storage.data_dir = str(work / "data")
    settings.storage.db_path = str(work / "data" / "probe.db")
    settings.storage.keep_audio = False
    (work / "data" / "probe.db").unlink(missing_ok=True)

    storage = Storage(settings.resolve(settings.storage.db_path))
    bus = EventBus()
    asr = WhisperAsr(settings)
    translator = BidirectionalTranslator(settings)
    tts = BidirectionalPiperTts(settings) if settings.tts.enabled else None
    pipeline = ClassroomPipeline(settings, bus, storage, asr, translator, tts)
    sid = storage.create_session("probe", "bench", "PRB01", "en", "bn")

    pipeline.start(sid, asyncio.get_running_loop())
    samples = {"enrichment": 0, "tts": 0, "backlog": 0}
    peak = {"enrichment": 0, "tts": 0, "backlog": 0}
    seg_latency = []

    stop = asyncio.Event()

    async def sample() -> None:
        while not stop.is_set():
            h = pipeline.health()
            q = h.get("queues", {})
            for k in samples:
                v = q.get(k) if k != "backlog" else h.get("stats", {}).get("stream_backlog", 0)
                v = v or 0
                peak[k] = max(peak[k], v)
                samples[k] += 1
            await asyncio.sleep(0.25)

    sampler = asyncio.create_task(sample())

    def feed() -> None:
        for chunk in WavSource("/home/faraz/project/offline_guru/data/demo/lecture_60s.wav", speed=1.0).frames(chunk_ms=100):
            pipeline.submit_audio(np.asarray(chunk))

    t0 = time.perf_counter()
    await asyncio.to_thread(feed)
    await asyncio.to_thread(pipeline.stop)
    stop.set()
    sampler.cancel()
    wall = time.perf_counter() - t0

    segs = storage.list_segments(sid)
    h = pipeline.health()
    print({
        "wall_s": round(wall, 1),
        "segments": len(segs),
        "speech_s": round(sum(s["end_s"] - s["start_s"] for s in segs), 1),
        "queue_peak": peak,
        "stats": h.get("stats"),
        "latency": h.get("latency"),
        "queues_final": h.get("queues"),
        "translate_ms": [round(s["translate_ms"]) for s in segs],
        "tts_ms": [round(s["tts_ms"]) for s in segs],
    })
    storage.purge_session(sid)
    storage.close()


asyncio.run(main())
