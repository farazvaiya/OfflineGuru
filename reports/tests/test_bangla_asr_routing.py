"""Regression tests for Bengali -> English ASR routing.

Stock whisper-base cannot emit Bengali script, so Bengali decodes must reach a
Bengali fine-tune (``asr.bn_model_dir``) while English keeps using
``asr.model_dir`` (see reports/BANGLA_TO_ENGLISH_ROOT_CAUSE.md). These tests pin
that behaviour down without loading real models: the recognizer is replaced with
a recorder so the chosen directory and ``language`` argument are observable.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from guru.asr import WhisperAsr
from guru.audio import Segment
from guru.bilingual import TurnLanguageRouter
from guru.bus import EventBus
from guru.pipeline import ClassroomPipeline
from guru.storage import Storage

from .conftest import FakeAsr, FakeTranslator, FakeTts, tone


# ---------------------------------------------------------------------------
# ASR model selection
# ---------------------------------------------------------------------------
class _RecorderModel:
    """Stands in for a loaded CTranslate2 Whisper model."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[dict] = []
        self.detect: tuple[str, float] = ("en", 0.0)

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        seg = SimpleNamespace(text="ok", avg_logprob=-0.2, no_speech_prob=0.0)
        info = SimpleNamespace(
            language=kwargs.get("language") or "en",
            language_probability=0.95,
        )
        return iter([seg]), info

    def detect_language(self, audio):
        language, prob = self.detect
        return language, prob, [(language, prob)]


@pytest.fixture
def models(monkeypatch):
    loaded: dict[Path, _RecorderModel] = {}

    def _get_model(self, path):
        return loaded.setdefault(path, _RecorderModel(path))

    monkeypatch.setattr(WhisperAsr, "_get_model", _get_model)
    return loaded


@pytest.fixture
def asr_settings(settings, tmp_path):
    base = tmp_path / "whisper-base"
    bn = tmp_path / "whisper-base-bn-ct2"
    base.mkdir()
    bn.mkdir()
    settings.asr.model_dir = str(base)
    settings.asr.bn_model_dir = str(bn)
    return settings


def _model_for(models, name: str) -> _RecorderModel:
    for path, model in models.items():
        if path.name == name:
            return model
    raise AssertionError(f"{name} was never loaded; loaded={[p.name for p in models]}")


def test_explicit_bengali_uses_bengali_model(asr_settings, models):
    asr = WhisperAsr(asr_settings)
    asr.transcribe(tone(0.5), language="bn")

    bn_model = _model_for(models, "whisper-base-bn-ct2")
    assert bn_model.calls[-1]["language"] == "bn"
    assert not models.keys() - {p for p in models if p.name == "whisper-base-bn-ct2"}


def test_english_still_uses_default_model(asr_settings, models):
    asr = WhisperAsr(asr_settings)
    asr.transcribe(tone(0.5), language="en")
    assert _model_for(models, "whisper-base").calls[-1]["language"] == "en"

    # Default (no language argument) is still the configured English default.
    asr.transcribe(tone(0.5))
    base = _model_for(models, "whisper-base")
    assert base.calls[-1]["language"] == "en"
    assert all(p.name == "whisper-base" for p in models)


def test_missing_bengali_model_falls_back_to_base(asr_settings, models, caplog):
    import shutil

    shutil.rmtree(asr_settings.asr.bn_model_dir)
    asr = WhisperAsr(asr_settings)
    result = asr.transcribe(tone(0.5), language="bn")

    assert result.text == "ok"  # a missing model degrades, it does not crash
    assert all(p.name == "whisper-base" for p in models)
    assert "Bengali ASR model missing" in caplog.text


def test_auto_mode_probes_and_decodes_with_bengali_model(asr_settings, models):
    asr = WhisperAsr(asr_settings)
    # Prime the probe result on the Bengali model before decoding.
    bn_model = asr._get_model(asr.bn_model_dir)
    bn_model.detect = ("bn", 0.99)

    result = asr.transcribe(tone(1.5), language="auto")

    assert bn_model.calls[-1]["language"] == "bn"
    assert result.language == "bn"
    assert asr.last_detected_language == "bn"
    assert asr.last_detection_prob >= 0.5


def test_auto_mode_ignores_unrelated_language_probe(asr_settings, models):
    asr = WhisperAsr(asr_settings)
    bn_model = asr._get_model(asr.bn_model_dir)
    bn_model.detect = ("cy", 0.84)  # observed on English speech with the bn model

    asr.transcribe(tone(1.5), language="auto")

    base = _model_for(models, "whisper-base")
    assert base.calls[-1]["language"] is None  # let Whisper detect for itself
    assert asr.last_detected_language in (None, "en")


def test_transcribe_words_routes_bengali_words_to_bengali_model(asr_settings, models):
    asr = WhisperAsr(asr_settings)
    asr.transcribe_words(tone(0.5), language="bn")
    assert _model_for(models, "whisper-base-bn-ct2").calls[-1]["word_timestamps"] is True


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
def test_router_decode_language_pins_explicit_and_lets_auto_decide():
    assert TurnLanguageRouter("bn").decode_language() == "bn"
    assert TurnLanguageRouter("en").decode_language() == "en"

    auto = TurnLanguageRouter("auto")
    assert auto.decode_language() == "auto"
    assert auto.observe_detected("bn", 0.95) is True
    assert auto.decode_language() == "bn"
    assert auto.target_language == "en"
    # Detection is per turn: the next turn probes again, while the sticky source
    # language (and therefore the translation direction) stays Bengali.
    auto.end_turn()
    assert auto.decode_language() == "auto"
    assert auto.source_language == "bn"


def test_router_ignores_weak_or_explicit_detection():
    auto = TurnLanguageRouter("auto")
    assert auto.observe_detected("bn", 0.3) is False
    assert auto.decode_language() == "auto"
    assert auto.observe_detected("cy", 0.99) is False
    assert auto.decode_language() == "auto"

    pinned = TurnLanguageRouter("en")
    assert pinned.observe_detected("bn", 0.99) is False
    assert pinned.decode_language() == "en"


# ---------------------------------------------------------------------------
# Pipeline plumbing
# ---------------------------------------------------------------------------
def make_pipeline(settings, tmp_path, asr: FakeAsr | None = None):
    storage = Storage(tmp_path / "pipeline.db")
    pipeline = ClassroomPipeline(
        settings, EventBus(), storage, asr or FakeAsr(), FakeTranslator(), FakeTts()
    )
    sid = storage.create_session("t", "tok", "BN01")
    pipeline.session_id = sid
    return pipeline, storage, sid


def _segment() -> Segment:
    return Segment(audio=tone(1.0), start_s=0.0, end_s=1.0, speech_s=1.0)


def test_bengali_session_sends_language_bn_for_final_decode(settings, tmp_path):
    asr = FakeAsr(text="আলো জ্বালানি গ্রহণের জন্য ক্লোরোফিল ব্যবহার করে।")
    pipeline, storage, sid = make_pipeline(settings, tmp_path, asr)
    pipeline.configure_languages("bn")

    pipeline._process_final(_segment(), 0.0)

    assert asr.languages[-1] == "bn"
    stored = storage.list_segments(sid)
    assert stored and stored[-1]["bn"].startswith("আলো")
    assert stored[-1]["source_language"] == "bn"


def test_english_session_sends_language_en(settings, tmp_path):
    asr = FakeAsr()
    pipeline, _storage, _sid = make_pipeline(settings, tmp_path, asr)
    pipeline.configure_languages("en")

    pipeline._process_final(_segment(), 0.0)

    assert asr.languages[-1] == "en"


def test_auto_session_routes_bengali_from_the_probe(settings, tmp_path):
    asr = FakeAsr(text="গাছপালা সূর্যালোক ব্যবহার করে খাদ্য তৈরি করে।")
    asr.detected_language = "bn"
    pipeline, storage, sid = make_pipeline(settings, tmp_path, asr)
    pipeline.configure_languages("auto")

    pipeline._process_final(_segment(), 0.0)

    # The decode is unconstrained, the probe decides the route.
    assert asr.languages[-1] == "auto"
    assert pipeline._language_router.source_language == "bn"
    assert pipeline._language_router.target_language == "en"
    stored = storage.list_segments(sid)
    assert stored[-1]["source_language"] == "bn"
    assert stored[-1]["bn"].startswith("গাছপালা")


def test_auto_session_caches_probe_between_decodes(settings, tmp_path):
    asr = FakeAsr(text="গাছপালা সূর্যালোক ব্যবহার করে খাদ্য তৈরি করে।")
    asr.detected_language = "bn"
    pipeline, _storage, _sid = make_pipeline(settings, tmp_path, asr)
    pipeline.configure_languages("auto")
    pipeline.settings.asr.auto_probe_interval_s = 60.0

    pipeline._process_final(_segment(), 0.0)
    pipeline._process_final(_segment(), 0.0)

    # Second decode reuses the cached language instead of probing again.
    assert asr.languages == ["auto", "bn"]


def test_partial_caption_uses_the_route_language(settings, tmp_path):
    asr = FakeAsr(text="আলো")
    pipeline, _storage, _sid = make_pipeline(settings, tmp_path, asr)
    pipeline.configure_languages("bn")

    pipeline._process_partial(np.zeros(16000 * 2, dtype=np.float32))

    assert asr.languages[-1] == "bn"
