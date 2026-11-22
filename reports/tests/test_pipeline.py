from __future__ import annotations

import asyncio
import logging

import numpy as np

from guru.audio import Segment
from guru.bus import EventBus
from guru.pipeline import ClassroomPipeline
from guru.storage import Storage

from .conftest import FakeAsr, FakeTranslator, FakeTts, silence, tone


def make_pipeline(settings, tmp_path):
    settings.asr.min_segment_s = 0.3
    settings.asr.max_segment_s = 3.0
    settings.asr.silence_ms = 300
    settings.asr.partial_interval_s = 0.2
    settings.asr.partial_enabled = True
    settings.storage.data_dir = str(tmp_path / "data")
    storage = Storage(tmp_path / "test.db")
    bus = EventBus()
    pipeline = ClassroomPipeline(
        settings, bus, storage, FakeAsr(), FakeTranslator(), FakeTts()
    )
    return pipeline, bus, storage


async def collect(event_q, predicate, timeout=6.0):
    deadline = asyncio.get_running_loop().time() + timeout
    seen = []
    while asyncio.get_running_loop().time() < deadline:
        try:
            ev = await asyncio.wait_for(event_q.get(), timeout=0.2)
        except TimeoutError:
            continue
        seen.append(ev)
        if predicate(ev):
            return ev, seen
    raise AssertionError(f"event not seen; got {[e.get('type') for e in seen]}")


def test_pipeline_publishes_text_then_audio(settings, tmp_path):
    async def scenario():
        pipeline, bus, storage = make_pipeline(settings, tmp_path)
        sid = storage.create_session("t", "tok", "PIPE01")
        pipeline.start(sid, asyncio.get_running_loop())
        sub = bus.subscribe("teacher", sid)
        audio = np.concatenate([silence(0.2), tone(0.8), silence(0.5)])
        for i in range(0, len(audio), 1600):
            pipeline.submit_audio(audio[i : i + 1600])

        seg_event, _ = await collect(sub.queue, lambda e: e["type"] == "segment")
        assert seg_event["segment"]["bn"].startswith("বাংলা: ")
        assert seg_event["segment"]["audio_url"] is None  # text arrives before audio

        audio_event, _ = await collect(sub.queue, lambda e: e["type"] == "audio.ready")
        assert "/api/student/audio/" in audio_event["audio_url"]

        await asyncio.to_thread(pipeline.stop)
        stored = storage.list_segments(sid)
        assert len(stored) >= 1
        assert stored[-1]["audio_path"]
        assert stored[-1]["total_ms"] > 0

    asyncio.run(scenario())


def test_pipeline_drops_oldest_when_asr_is_saturated(settings, tmp_path):
    pipeline, _bus, storage = make_pipeline(settings, tmp_path)
    sid = storage.create_session("t", "tok", "PIPE02")
    pipeline.session_id = sid
    # Simulate a stalled ASR worker: fill the bounded queue by hand.
    seg = Segment(audio=np.zeros(16000, dtype=np.float32), start_s=0, end_s=1, speech_s=1)
    for _ in range(pipeline._final_q.maxsize):
        pipeline._final_q.put_nowait((seg, 0.0))
    pipeline._enqueue_final(seg)
    assert pipeline.stats.asr_queue_drops == 1
    assert pipeline._final_q.qsize() == pipeline._final_q.maxsize


def test_pipeline_stop_flushes_tail(settings, tmp_path):
    async def scenario():
        pipeline, _bus, storage = make_pipeline(settings, tmp_path)
        sid = storage.create_session("t", "tok", "PIPE03")
        pipeline.start(sid, asyncio.get_running_loop())
        pipeline.submit_audio(tone(1.2))
        await asyncio.to_thread(pipeline.stop)
        assert storage.segment_count(sid) >= 1

    asyncio.run(scenario())


def test_partial_caption_is_published(settings, tmp_path):
    async def scenario():
        pipeline, bus, storage = make_pipeline(settings, tmp_path)
        sid = storage.create_session("t", "tok", "PIPE04")
        pipeline.start(sid, asyncio.get_running_loop())
        sub = bus.subscribe("teacher", sid)
        for _ in range(8):
            pipeline.submit_audio(tone(0.5))
            await asyncio.sleep(0.25)
        ev, _ = await collect(sub.queue, lambda e: e["type"] == "transcript.partial", timeout=8)
        assert ev["en"]
        await asyncio.to_thread(pipeline.stop)

    asyncio.run(scenario())


def test_pipeline_persists_latency_metrics(settings, tmp_path):
    """A real segment must leave e2e/translate/ASR metrics behind (HIGH-1)."""
    async def scenario():
        pipeline, bus, storage = make_pipeline(settings, tmp_path)
        sid = storage.create_session("t", "tok", "PIPE06")
        pipeline.start(sid, asyncio.get_running_loop())
        sub = bus.subscribe("teacher", sid)
        audio = np.concatenate([tone(0.8), silence(0.5)])
        for i in range(0, len(audio), 1600):
            pipeline.submit_audio(audio[i : i + 1600])
        await collect(sub.queue, lambda e: e["type"] == "segment")
        await asyncio.to_thread(pipeline.stop)

        e2e = storage.metric_summary("e2e_ms", sid)
        assert e2e["count"] >= 1, "e2e_ms was not recorded for a real segment"
        assert e2e["max"] > 0
        assert storage.metric_summary("translate_ms", sid)["count"] >= 1
        assert storage.metric_summary("asr_ms", sid)["count"] >= 1
        # The health endpoint reads the unscoped summary, so that must see it too.
        assert storage.metric_summary("e2e_ms")["count"] >= 1

    asyncio.run(scenario())


def test_pipeline_metric_failure_is_logged_not_swallowed(settings, tmp_path, caplog):
    pipeline, _bus, storage = make_pipeline(settings, tmp_path)
    pipeline.session_id = storage.create_session("t", "tok", "PIPE07")

    def boom(*_args, **_kwargs):
        raise ValueError("bad metric entry")

    pipeline.storage.record_metrics = boom  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING, logger="guru.pipeline"):
        pipeline._metrics([("e2e_ms", 1.0)])  # must not raise: class continues
    assert any("metric write failed" in r.getMessage() for r in caplog.records)


def test_pipeline_handles_unavailable_translator(settings, tmp_path):
    """A translation failure must still deliver the English transcript."""
    from guru.translate import Translation

    class Broken:
        available = False

        def load(self):
            return None

        def translate_batch(self, texts, beam_size=None):
            return [Translation(source=t, text="", skipped=True, reason="boom") for t in texts]

        def translate(self, text):
            return self.translate_batch([text])[0]

    async def scenario():
        pipeline, bus, storage = make_pipeline(settings, tmp_path)
        pipeline.translator = Broken()
        sid = storage.create_session("t", "tok", "PIPE05")
        pipeline.start(sid, asyncio.get_running_loop())
        sub = bus.subscribe("teacher", sid)
        audio = np.concatenate([tone(0.8), silence(0.5)])
        for i in range(0, len(audio), 1600):
            pipeline.submit_audio(audio[i : i + 1600])
        ev, _ = await collect(sub.queue, lambda e: e["type"] == "transcript.final")
        assert ev["segment"]["en"]
        await asyncio.to_thread(pipeline.stop)

    asyncio.run(scenario())
