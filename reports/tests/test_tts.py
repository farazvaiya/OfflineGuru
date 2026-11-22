from __future__ import annotations

import pytest

from guru.asr import ModelUnavailable
from guru.tts import NullTts, PiperTts


def test_empty_text_returns_no_audio(settings):
    tts = PiperTts(settings)
    data, ms = tts.synthesize("   ")
    assert data == b"" and ms == 0.0
    assert tts._voice is None  # nothing was loaded for empty input


def test_missing_voice_raises_model_unavailable(settings):
    tts = PiperTts(settings)  # settings fixture points at a missing voice file
    assert tts.available is False
    with pytest.raises(ModelUnavailable):
        tts.synthesize("বাংলা বাক্য")


def test_null_tts_returns_empty():
    data, ms = NullTts().synthesize("কিছু লেখা")
    assert data == b"" and ms == 0.0


def test_cache_key_changes_with_text_and_length_scale(settings):
    tts = PiperTts(settings)
    k1 = tts._cache_key("hello")
    k2 = tts._cache_key("world")
    assert k1 != k2
    settings.tts.length_scale = 1.5
    k3 = tts._cache_key("hello")
    assert k3 != k1  # voice settings are part of the key


def test_forget_cached_removes_only_the_given_captions(settings):
    tts = PiperTts(settings)
    tts.cache_dir.mkdir(parents=True, exist_ok=True)
    drop = tts.cache_path("মুছে ফেলো")
    keep = tts.cache_path("রাখো")
    drop.write_bytes(b"RIFF")
    keep.write_bytes(b"RIFF")

    assert tts.forget_cached(["মুছে ফেলো", "", "   "]) == 1
    assert not drop.exists() and keep.exists()
    assert tts.forget_cached(["মুছে ফেলো"]) == 0  # already gone, not an error
    assert NullTts().forget_cached(["anything"]) == 0
