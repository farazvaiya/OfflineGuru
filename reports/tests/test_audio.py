from __future__ import annotations

import numpy as np

from guru.audio import SAMPLE_RATE, Segmenter

from .conftest import silence, tone


def feed_all(segmenter: Segmenter, audio: np.ndarray, chunk: int = 1600):
    out = []
    for i in range(0, len(audio), chunk):
        out.extend(segmenter.feed(audio[i : i + chunk]))
    return out


def test_single_utterance_is_segmented():
    seg = Segmenter(min_segment_s=0.5, max_segment_s=10, silence_ms=600)
    audio = np.concatenate([silence(0.5), tone(1.2), silence(1.0)])
    segs = feed_all(seg, audio)
    assert len(segs) == 1
    s = segs[0]
    assert 1.2 <= s.duration_s <= 1.8
    assert s.speech_s > 1.0
    # Trailing silence must be trimmed.
    assert s.duration_s < 1.7


def test_short_blip_is_dropped():
    seg = Segmenter(min_segment_s=0.8, silence_ms=600)
    audio = np.concatenate([silence(0.3), tone(0.1), silence(1.0)])
    assert feed_all(seg, audio) == []
    assert seg.dropped_segments == 1


def test_long_monologue_is_force_cut():
    seg = Segmenter(min_segment_s=0.5, max_segment_s=2.0, silence_ms=600)
    audio = tone(5.0)
    segs = feed_all(seg, audio)
    assert len(segs) >= 2
    for s in segs[:-1]:
        assert s.duration_s <= 2.3


def test_partial_buffer_only_during_speech():
    seg = Segmenter(min_segment_s=0.5)
    seg.feed(silence(0.5))
    assert seg.partial_audio() is None
    seg.feed(tone(1.0))
    partial = seg.partial_audio()
    assert partial is not None and len(partial) > SAMPLE_RATE * 0.5


def test_flush_returns_pending_speech():
    seg = Segmenter(min_segment_s=0.5, silence_ms=600)
    seg.feed(tone(1.0))
    flushed = seg.flush()
    assert flushed is not None
    assert flushed.duration_s > 0.9


def test_noise_floor_does_not_trigger_on_hiss():
    """Low-level background noise must not open a segment."""
    rng = np.random.default_rng(0)
    noise = (rng.normal(0, 0.002, SAMPLE_RATE * 3)).astype(np.float32)
    seg = Segmenter(min_segment_s=0.5, vad_threshold=0.006)
    assert feed_all(seg, noise) == []


def test_two_utterances_separated_by_pause():
    seg = Segmenter(min_segment_s=0.5, silence_ms=400)
    audio = np.concatenate([tone(1.0), silence(0.8), tone(1.0), silence(0.8)])
    segs = feed_all(seg, audio)
    assert len(segs) == 2
    assert segs[0].end_s <= segs[1].start_s
