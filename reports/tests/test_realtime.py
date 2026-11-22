"""Tests for the real-time streaming path.

Covers the streaming primitives (LocalAgreement, SpeechWindow, PhraseBuilder,
LatencyRecorder) and the streaming pipeline end to end with a deterministic
fake ASR that emits word timestamps.
"""

from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest

from guru.bus import EventBus
from guru.pipeline import ClassroomPipeline
from guru.realtime import (
    LatencyRecorder,
    LocalAgreement,
    PhraseBuilder,
    SpeechWindow,
    Word,
    normalize_word,
)
from guru.storage import Storage

from .conftest import FakeTranslator, FakeTts, silence, tone


# ---------------------------------------------------------------------------
# LocalAgreement
# ---------------------------------------------------------------------------
def words(*texts, start=0.0, step=0.3, prob=0.5):
    out = []
    t = start
    for text in texts:
        out.append(Word(text, t, t + step, prob))
        t += step
    return out


def test_local_agreement_commits_only_agreed_prefix():
    la = LocalAgreement(confidence=0.0)
    la.insert(words("the", "plant", "uses"), 0.0)
    assert la.commit() == []  # first hypothesis: nothing to agree with
    la.insert(words("the", "plant", "uses", "sunlight"), 0.0)
    committed = la.commit()
    assert [w.text for w in committed] == ["the", "plant", "uses"]
    assert la.pending_text() == "sunlight"


def test_local_agreement_stops_at_disagreement():
    la = LocalAgreement(confidence=0.0)
    la.insert(words("the", "plant", "uses"), 0.0)
    la.commit()
    la.insert(words("the", "plant", "needs", "water"), 0.0)
    committed = la.commit()
    assert [w.text for w in committed] == ["the", "plant"]
    assert la.pending_text() == "needs water"


def test_local_agreement_confidence_commits_immediately():
    la = LocalAgreement(confidence=0.9)
    la.insert(words("artificial", "intelligence", prob=0.99), 0.0)
    committed = la.commit()
    assert [w.text for w in committed] == ["artificial", "intelligence"]


def test_local_agreement_silent_decode_keeps_buffer():
    la = LocalAgreement(confidence=0.0)
    la.insert(words("hello", "world"), 0.0)
    la.commit()
    la.insert([], 0.0)  # a decode that heard nothing
    assert la.commit() == []
    la.insert(words("hello", "world", "again"), 0.0)
    assert [w.text for w in la.commit()] == ["hello", "world"]


def test_local_agreement_flush_all_has_no_duplicates():
    la = LocalAgreement(confidence=0.0)
    la.insert(words("one", "two", "three"), 0.0)
    la.commit()
    la.insert(words("one", "two", "three", "four"), 0.0)
    la.commit()
    tail = la.flush_all()
    assert [w.text for w in tail] == ["four"]
    assert la.pending_text() == ""


def test_local_agreement_prompt_is_bounded():
    la = LocalAgreement(confidence=0.9)
    la.insert(words(*[f"word{i}" for i in range(60)], prob=0.99), 0.0)
    la.commit()
    prompt = la.prompt(max_chars=40)
    assert len(prompt) <= 40
    assert prompt.endswith("word59")


def test_normalize_word_ignores_case_and_punctuation():
    assert normalize_word("Learn.") == normalize_word("learn")
    assert normalize_word("(glucose),") == "glucose"


# ---------------------------------------------------------------------------
# SpeechWindow
# ---------------------------------------------------------------------------
def test_speech_window_detects_start_and_end():
    w = SpeechWindow(vad_threshold=0.01, preroll_ms=100, silence_ms=200)
    started, ended = w.feed(silence(0.2))
    assert not started and not ended
    started, ended = w.feed(tone(0.5))
    assert started and not ended
    assert w.in_speech
    _started, ended = w.feed(silence(0.4))
    assert ended
    assert not w.in_speech
    assert w.take_ended() is True
    assert w.take_ended() is False  # consumed once


def test_speech_window_keeps_preroll():
    w = SpeechWindow(vad_threshold=0.01, preroll_ms=200, silence_ms=200)
    w.feed(silence(0.5))
    w.feed(tone(0.3))
    # The buffer starts before the detected speech, so the first phoneme is kept.
    assert w.start_time < w.speech_start


def test_speech_window_trim_keeps_context():
    w = SpeechWindow(vad_threshold=0.01, preroll_ms=0, silence_ms=500, context_s=0.5)
    w.feed(tone(2.0))
    before = w.duration_s
    w.trim_to(1.5)
    assert w.duration_s < before
    assert abs(w.start_time - 1.0) < 0.05  # 1.5 - context 0.5


def test_speech_window_caps_length():
    w = SpeechWindow(vad_threshold=0.01, preroll_ms=0, silence_ms=500, max_window_s=2.0)
    w.feed(tone(4.0))
    assert w.duration_s <= 2.05


def test_speech_window_reset_clears_audio():
    w = SpeechWindow(vad_threshold=0.01, preroll_ms=0, silence_ms=200)
    w.feed(tone(0.5))
    w.reset()
    assert w.duration_s == 0.0
    assert not w.in_speech


def test_speech_window_rebase_keeps_audio_after_frontier():
    """F-03: audio that arrives during a decode must survive the phrase flush."""
    w = SpeechWindow(vad_threshold=0.01, preroll_ms=0, silence_ms=500, context_s=0.2)
    w.feed(tone(3.0))
    end_before = w.end_time
    kept = w.rebase(1.0)
    assert w.start_time == pytest.approx(0.8, abs=0.05)  # frontier minus context
    assert w.end_time == pytest.approx(end_before, abs=0.01)  # clock does not jump
    assert kept == pytest.approx(2.2, abs=0.05)
    assert w.duration_s == pytest.approx(2.2, abs=0.05)
    assert w.in_speech  # speech continues, no new VAD trigger needed
    assert w.duration_s < 3.0


def test_speech_window_rebase_keeps_everything_when_frontier_is_old():
    """A frontier behind the buffer start must not wipe the buffer.

    This is the second half of F-03: at end-of-speech the last committed word is
    often older than the audio still buffered, and the first version of rebase()
    treated that as "drop everything".
    """
    w = SpeechWindow(vad_threshold=0.01, preroll_ms=0, silence_ms=500, context_s=1.0)
    w.feed(tone(2.0))
    w.rebase(1.5)  # trims to 0.5
    before = w.duration_s
    kept = w.rebase(0.4)  # frontier - context is before the buffer start
    assert w.duration_s == pytest.approx(before, abs=0.02)
    assert kept == pytest.approx(before, abs=0.02)
    assert w.duration_s > 0


def test_speech_window_rebase_at_end_clears_audio():
    w = SpeechWindow(vad_threshold=0.01, preroll_ms=0, silence_ms=500, context_s=0.0)
    w.feed(tone(1.0))
    kept = w.rebase(w.end_time)
    assert kept == 0.0
    assert w.duration_s == 0.0
    assert not w.in_speech
    assert w.start_time == pytest.approx(1.0, abs=0.01)


def test_stream_flush_does_not_recommit_the_retained_context(streaming_settings, tmp_path):
    """The carried-over context must not be published twice."""
    pipeline, _bus, _storage = make_stream_pipeline(streaming_settings, tmp_path)
    pipeline._build_stream()
    pipeline._window.feed(tone(4.0))
    spoken = words("the", "plant", "uses", "sunlight", "to", "make", "food", start=0.0)
    pipeline._agreement.insert(spoken, 0.0)
    pipeline._agreement.commit()
    pipeline._flush_stream_tail()
    assert pipeline._window.duration_s > 1.0  # context retained

    # The next decode re-reads the retained audio and returns the same words.
    pipeline._agreement.insert(spoken, 0.0)
    recommitted = pipeline._agreement.commit()
    assert [w.text for w in recommitted] == []
    pipeline._agreement.insert(spoken + words("today", start=2.1), 0.0)
    fresh = pipeline._agreement.commit()
    assert all(w.text != "sunlight" for w in fresh)


def test_stream_flush_carries_audio_that_arrived_during_the_decode(streaming_settings, tmp_path):
    """Ending a phrase must not throw away the buffer (the 37 % loss in F-03)."""
    pipeline, _bus, _storage = make_stream_pipeline(streaming_settings, tmp_path)
    pipeline._build_stream()
    window = pipeline._window
    window.feed(tone(3.0))
    pipeline._agreement.insert(words("the", "plant", "uses", "sunlight"), 0.0)
    pipeline._agreement.commit()
    end_before = window.end_time

    pipeline._flush_stream_tail()

    assert window.end_time == pytest.approx(end_before, abs=0.01)
    assert pipeline.stats.audio_carried_s > 1.0
    assert window.duration_s > 1.0  # the tail was kept, not reset to empty


# ---------------------------------------------------------------------------
# PhraseBuilder
# ---------------------------------------------------------------------------
def test_phrase_closes_on_sentence_punctuation():
    pb = PhraseBuilder(max_chars=500, max_s=60, min_chars=1, flush_age_s=60)
    closed = pb.add(words("Plants", "make", "food."), now=time.monotonic())
    assert len(closed) == 1
    assert closed[0].text == "Plants make food."
    assert len(pb) == 0


def test_phrase_closes_on_char_cap_at_clause_boundary():
    pb = PhraseBuilder(max_chars=40, max_s=60, min_chars=1, flush_age_s=60)
    closed = pb.add(
        words("Plants", "make", "food,", "and", "they", "release", "oxygen"),
        now=time.monotonic(),
    )
    assert closed
    assert closed[0].text.endswith("food,")
    # The remainder stays open for the next phrase.
    assert "oxygen" in pb.text()


def test_phrase_closes_on_age():
    pb = PhraseBuilder(max_chars=500, max_s=60, min_chars=5, flush_age_s=0.0)
    pb.add(words("artificial", "intelligence"), now=time.monotonic() - 5)
    closed = pb.tick(now=time.monotonic())
    assert len(closed) == 1
    assert closed[0].text == "artificial intelligence"


def test_phrase_does_not_close_before_min_chars():
    pb = PhraseBuilder(max_chars=500, max_s=60, min_chars=50, flush_age_s=0.0)
    pb.add(words("short"), now=time.monotonic() - 5)
    assert pb.tick(now=time.monotonic()) == []


def test_phrase_flush_returns_remainder():
    pb = PhraseBuilder(max_chars=500, max_s=60, min_chars=1, flush_age_s=60)
    pb.add(words("unfinished", "thought"), now=time.monotonic())
    phrase = pb.flush()
    assert phrase is not None and phrase.text == "unfinished thought"
    assert pb.flush() is None


# ---------------------------------------------------------------------------
# LatencyRecorder
# ---------------------------------------------------------------------------
def test_latency_recorder_summary():
    rec = LatencyRecorder(window=10)
    # Nearest-rank percentiles, matching the repo's existing describe() helper.
    for value in (10, 20, 30, 40, 50):
        rec.add("asr_ms", value)
    summary = rec.summary("asr_ms")
    assert summary["n"] == 5
    assert summary["p50"] == 30
    assert summary["max"] == 50
    assert rec.summary("missing") == {"n": 0}


# ---------------------------------------------------------------------------
# Streaming pipeline
# ---------------------------------------------------------------------------
class FakeStreamingAsr:
    """Emits a fixed word sequence with timestamps, like faster-whisper would."""

    available = True

    def __init__(self, script=None, prob=0.99):
        self.script = script or [
            "Artificial", "intelligence", "is", "changing", "the", "way",
            "students", "learn.",
        ]
        self.prob = prob
        self.calls = 0

    def load(self) -> None:
        return None

    def transcribe_words(self, audio, offset=0.0, beam_size=1, prompt=None):
        self.calls += 1
        out = []
        t = offset
        for text in self.script:
            out.append(Word(text, t, t + 0.25, self.prob))
            t += 0.25
        return out


def make_stream_pipeline(settings, tmp_path, asr=None):
    storage = Storage(tmp_path / "stream.db")
    bus = EventBus()
    pipeline = ClassroomPipeline(
        settings, bus, storage, asr or FakeStreamingAsr(), FakeTranslator(), FakeTts()
    )
    return pipeline, bus, storage


async def collect_until(queue, predicate, timeout=8.0):
    deadline = asyncio.get_running_loop().time() + timeout
    seen = []
    while asyncio.get_running_loop().time() < deadline:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=0.2)
        except TimeoutError:
            continue
        seen.append(event)
        if predicate(event):
            return event, seen
    raise AssertionError(f"event not seen; got {[e.get('type') for e in seen]}")


def test_streaming_pipeline_emits_ordered_segments(streaming_settings, tmp_path):
    async def scenario():
        pipeline, bus, storage = make_stream_pipeline(streaming_settings, tmp_path)
        sid = storage.create_session("t", "tok", "STRM01")
        pipeline.start(sid, asyncio.get_running_loop())
        sub = bus.subscribe("teacher", sid)

        audio = np.concatenate([silence(0.2), tone(1.2), silence(0.6)])
        for i in range(0, len(audio), 1600):
            pipeline.submit_audio(audio[i : i + 1600])

        seg_event, seen = await collect_until(sub.queue, lambda e: e["type"] == "segment")
        seg = seg_event["segment"]
        assert seg["bn"].startswith("বাংলা: ")
        assert seg["sequence_number"] == seg["seq"]
        assert seg["segment_status"] == "final"
        assert seg["audio_status"] in {"pending", "disabled"}
        assert seg["source_text"] == seg["en"]
        assert seg["translated_text"] == seg["bn"]

        audio_event, _ = await collect_until(sub.queue, lambda e: e["type"] == "audio.ready")
        assert audio_event["sequence_number"] == seg["seq"]
        assert audio_event["audio_status"] == "ready"

        await asyncio.to_thread(pipeline.stop)
        stored = storage.list_segments(sid)
        assert stored
        seqs = [row["seq"] for row in stored]
        assert seqs == sorted(seqs)
        assert len(seqs) == len(set(seqs))  # no duplicate segments
        assert stored[-1]["audio_status"] == "ready"
        assert stored[-1]["translation_at"] > 0
        assert pipeline.stats.stream_decodes >= 1
        assert pipeline.stats.first_caption_ms is not None

    asyncio.run(scenario())


def test_streaming_pipeline_publishes_partials_and_stable(streaming_settings, tmp_path):
    async def scenario():
        asr = FakeStreamingAsr(prob=0.1)  # low confidence: agreement path only
        pipeline, bus, storage = make_stream_pipeline(streaming_settings, tmp_path, asr)
        sid = storage.create_session("t", "tok", "STRM02")
        pipeline.start(sid, asyncio.get_running_loop())
        sub = bus.subscribe("teacher", sid)

        audio = np.concatenate([silence(0.2), tone(1.0), silence(0.6)])
        for i in range(0, len(audio), 1600):
            pipeline.submit_audio(audio[i : i + 1600])

        _event, seen = await collect_until(
            sub.queue, lambda e: e["type"] in {"transcript.stable", "transcript.final"}
        )
        types = {e["type"] for e in seen}
        assert "transcript.partial" in types
        await asyncio.to_thread(pipeline.stop)

    asyncio.run(scenario())


def test_streaming_pipeline_survives_translation_failure(streaming_settings, tmp_path):
    class BrokenTranslator(FakeTranslator):
        def translate(self, text):
            raise RuntimeError("model exploded")

    async def scenario():
        storage = Storage(tmp_path / "broken.db")
        bus = EventBus()
        pipeline = ClassroomPipeline(
            streaming_settings, bus, storage, FakeStreamingAsr(), BrokenTranslator(), FakeTts()
        )
        sid = storage.create_session("t", "tok", "STRM03")
        pipeline.start(sid, asyncio.get_running_loop())
        sub = bus.subscribe("teacher", sid)

        audio = np.concatenate([silence(0.2), tone(1.0), silence(0.6)])
        for i in range(0, len(audio), 1600):
            pipeline.submit_audio(audio[i : i + 1600])

        _event, seen = await collect_until(sub.queue, lambda e: e["type"] == "pipeline.error")
        assert any(e["stage"] == "translate" for e in seen if e["type"] == "pipeline.error")
        await asyncio.to_thread(pipeline.stop)
        # The English transcript is still stored even though translation failed.
        stored = storage.list_segments(sid)
        assert stored and stored[0]["en"]
        assert pipeline.stats.translate_failures >= 1

    asyncio.run(scenario())


def test_streaming_pipeline_marks_failed_audio(streaming_settings, tmp_path):
    class SilentTts(FakeTts):
        def synthesize(self, text, use_cache=True):
            return b"", 0.0

    async def scenario():
        storage = Storage(tmp_path / "silent.db")
        bus = EventBus()
        pipeline = ClassroomPipeline(
            streaming_settings, bus, storage, FakeStreamingAsr(), FakeTranslator(), SilentTts()
        )
        sid = storage.create_session("t", "tok", "STRM04")
        pipeline.start(sid, asyncio.get_running_loop())
        sub = bus.subscribe("teacher", sid)

        audio = np.concatenate([silence(0.2), tone(1.0), silence(0.6)])
        for i in range(0, len(audio), 1600):
            pipeline.submit_audio(audio[i : i + 1600])

        await collect_until(sub.queue, lambda e: e["type"] == "segment.updated")
        await asyncio.to_thread(pipeline.stop)
        stored = storage.list_segments(sid)
        assert stored and stored[0]["audio_status"] == "failed"
        assert pipeline.stats.tts_failures >= 1

    asyncio.run(scenario())


def test_streaming_health_reports_mode_and_latency(streaming_settings, tmp_path):
    pipeline, _bus, storage = make_stream_pipeline(streaming_settings, tmp_path)
    health = pipeline.health()
    assert health["mode"] == "streaming"
    assert "stream_decodes" in health["stats"]
    assert health["window"] is None  # not started yet


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------
def test_streaming_pipeline_ignores_silence(streaming_settings, tmp_path):
    async def scenario():
        pipeline, bus, storage = make_stream_pipeline(streaming_settings, tmp_path)
        sid = storage.create_session("t", "tok", "STRM05")
        pipeline.start(sid, asyncio.get_running_loop())
        sub = bus.subscribe("teacher", sid)
        for i in range(0, len(silence(1.5)), 1600):
            pipeline.submit_audio(silence(1.5)[i : i + 1600])
        await asyncio.sleep(0.4)
        await asyncio.to_thread(pipeline.stop)
        assert storage.list_segments(sid) == []
        assert pipeline.stats.stream_decodes == 0
        assert pipeline.stats.last_error is None

    asyncio.run(scenario())


def test_streaming_pipeline_survives_metric_write_failure(streaming_settings, tmp_path):
    async def scenario():
        pipeline, bus, storage = make_stream_pipeline(streaming_settings, tmp_path)

        def boom(*_args, **_kwargs):
            raise RuntimeError("disk full")

        storage.record_metrics = boom  # type: ignore[assignment]
        sid = storage.create_session("t", "tok", "STRM06")
        pipeline.start(sid, asyncio.get_running_loop())
        sub = bus.subscribe("teacher", sid)
        audio = np.concatenate([silence(0.2), tone(1.0), silence(0.6)])
        for i in range(0, len(audio), 1600):
            pipeline.submit_audio(audio[i : i + 1600])
        # Metrics failing must not stop captions.
        await collect_until(sub.queue, lambda e: e["type"] == "segment")
        await asyncio.to_thread(pipeline.stop)
        assert storage.list_segments(sid)

    asyncio.run(scenario())


def test_streaming_pipeline_pause_drops_audio(streaming_settings, tmp_path):
    async def scenario():
        pipeline, _bus, storage = make_stream_pipeline(streaming_settings, tmp_path)
        sid = storage.create_session("t", "tok", "STRM07")
        pipeline.start(sid, asyncio.get_running_loop())
        pipeline.pause()
        audio = np.concatenate([silence(0.2), tone(1.0), silence(0.6)])
        for i in range(0, len(audio), 1600):
            pipeline.submit_audio(audio[i : i + 1600])
        await asyncio.sleep(0.3)
        await asyncio.to_thread(pipeline.stop)
        assert storage.list_segments(sid) == []

    asyncio.run(scenario())


def test_streaming_stop_is_idempotent(streaming_settings, tmp_path):
    async def scenario():
        pipeline, _bus, storage = make_stream_pipeline(streaming_settings, tmp_path)
        sid = storage.create_session("t", "tok", "STRM08")
        pipeline.start(sid, asyncio.get_running_loop())
        await asyncio.to_thread(pipeline.stop)
        await asyncio.to_thread(pipeline.stop)  # must not raise or hang
        assert pipeline.status == "idle"

    asyncio.run(scenario())


def test_public_segment_carries_playback_contract():
    from guru.server.app import _public_segment

    payload = _public_segment(
        {
            "id": "abc",
            "seq": 7,
            "start_s": 1.234,
            "end_s": 3.456,
            "en": "hello",
            "bn": "হ্যালো",
            "audio_path": "/tmp/x.wav",
            "audio_status": "ready",
            "segment_status": "final",
            "translation_at": 123.0,
            "total_ms": 900,
        }
    )
    assert payload["sequence_number"] == 7
    assert payload["segment_id"] == "abc"
    assert payload["source_text"] == "hello"
    assert payload["translated_text"] == "হ্যালো"
    assert payload["audio_status"] == "ready"
    assert payload["audio_reference"] == "/api/student/audio/abc"
    assert payload["segment_status"] == "final"
    assert payload["translation_timestamp"] == 123.0


def test_tts_cache_reuses_generated_audio_for_identical_phrase(settings, tmp_path):
    from guru.tts import PiperTts

    tts = PiperTts(settings)
    calls = []
    tts.load = lambda: None
    tts.voice_path = tmp_path / "voice.onnx"
    tts.cfg.length_scale = 1.0
    class Voice:
        def synthesize(self, text, cfg):
            calls.append(text)
            class Chunk:
                audio_int16_bytes = b"\0\0" * 10
                sample_rate = 22050
            yield Chunk()
    tts._voice = Voice()
    first, _ = tts.synthesize("same translated phrase")
    second, _ = tts.synthesize("same translated phrase")
    assert first == second
    assert calls == ["same translated phrase"]
