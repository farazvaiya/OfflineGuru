from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from guru.asr import AsrResult
from guru.config import Settings
from guru.translate import Translation


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.storage.data_dir = str(tmp_path / "data")
    s.storage.db_path = str(tmp_path / "data" / "test.db")
    s.tts.cache_dir = str(tmp_path / "data" / "audio")
    s.asr.model_dir = str(tmp_path / "missing-asr")
    s.translate.model_dir = str(tmp_path / "missing-mt")
    s.tts.voice_onnx = str(tmp_path / "missing-voice.onnx")
    # The original suite exercises the segment path. Streaming has its own
    # fixture (streaming_settings) and its own tests in test_realtime.py.
    s.asr.streaming = False
    return s


@pytest.fixture
def streaming_settings(settings: Settings) -> Settings:
    """Settings with the real-time streaming path enabled."""
    settings.asr.streaming = True
    settings.asr.stream_min_audio_s = 0.2
    settings.asr.stream_step_s = 0.0
    settings.asr.stream_max_window_s = 6.0
    settings.translate.phrase_flush_age_s = 0.0
    settings.translate.phrase_min_chars = 1
    return settings


class FakeAsr:
    """Deterministic stand-in for Whisper; echoes a fixed classroom sentence."""

    available = True

    def __init__(self, text: str = "Photosynthesis is the process by which green plants make food using sunlight."):
        self.text = text
        self.calls = 0
        # Language the pipeline asked for on each decode (None = adapter default).
        self.languages: list[str | None] = []
        # Simulates the real model's language probe when language="auto".
        self.detected_language: str | None = None
        self.last_detected_language: str | None = None
        self.last_detection_prob = 0.0

    def load(self) -> None:
        return None

    def load_for(self, language: str | None = None) -> None:
        return None

    def transcribe(self, audio: np.ndarray, beam_size: int | None = None,
                   language: str | None = None) -> AsrResult:
        self.calls += 1
        self.languages.append(language)
        detected = None
        if language in (None, "auto"):
            detected = self.detected_language
        elif language in {"bn", "en"}:
            detected = language
        self.last_detected_language = detected
        self.last_detection_prob = 0.9 if detected else 0.0
        return AsrResult(
            text=self.text,
            language=(language if language not in (None, "auto") else (detected or "en")),
            avg_logprob=-0.25,
            no_speech_prob=0.01,
            latency_ms=42.0,
            audio_s=len(audio) / 16000.0,
        )


class FakeTranslator:
    available = True

    def __init__(self, prefix: str = "বাংলা: "):
        self.prefix = prefix
        self.calls = 0

    def load(self) -> None:
        return None

    def translate_batch(self, texts, beam_size=None):
        self.calls += len(texts)
        return [Translation(source=t, text=self.prefix + t, latency_ms=30.0) for t in texts]

    def translate(self, text: str) -> Translation:
        return self.translate_batch([text])[0]


class FakeTts:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def load(self) -> None:
        return None

    def synthesize(self, text: str, use_cache: bool = True):
        self.calls += 1
        # Minimal valid 8-bit mono WAV header + silence.
        data = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
        data += (1).to_bytes(2, "little") + (1).to_bytes(2, "little")
        data += (16000).to_bytes(4, "little") + (16000).to_bytes(4, "little")
        data += (1).to_bytes(2, "little") + (8).to_bytes(2, "little")
        data += b"data" + (64).to_bytes(4, "little") + b"\x80" * 64
        return data, 12.0


@pytest.fixture
def fake_asr() -> FakeAsr:
    return FakeAsr()


@pytest.fixture
def fake_translator() -> FakeTranslator:
    return FakeTranslator()


@pytest.fixture
def fake_tts() -> FakeTts:
    return FakeTts()


def tone(seconds: float, amplitude: float = 0.2, freq: float = 220.0, sample_rate: int = 16000) -> np.ndarray:
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds: float, sample_rate: int = 16000) -> np.ndarray:
    return np.zeros(int(seconds * sample_rate), dtype=np.float32)
