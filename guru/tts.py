"""Offline Bangla text-to-speech with Piper (ONNX, CPU).

Audio is produced as WAV bytes and cached on disk keyed by text + voice settings,
so 30 students replaying the same caption cost one synthesis, not 30.

Student devices play the audio themselves; the teacher laptop never controls a
student's physical headphone output.  Text captions are always available as a
fallback when audio is disabled or synthesis fails.
"""

from __future__ import annotations

import hashlib
import io
import logging
import threading
import time
import wave
from pathlib import Path

from .asr import ModelUnavailable
from .config import Settings, TtsConfig

log = logging.getLogger(__name__)


class PiperTts:
    def __init__(self, settings: Settings) -> None:
        self.cfg: TtsConfig = settings.tts
        self.voice_path = settings.resolve(self.cfg.voice_onnx)
        self.config_path = settings.resolve(self.cfg.voice_config)
        self.cache_dir = settings.resolve(self.cfg.cache_dir) or Path("data/audio")
        self._voice = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self.voice_path and self.voice_path.is_file())

    def load(self) -> None:
        if self._voice is not None:
            return
        if not self.available:
            raise ModelUnavailable(f"TTS voice not found at {self.voice_path}")
        from piper import PiperVoice

        t0 = time.perf_counter()
        self._voice = PiperVoice.load(
            str(self.voice_path),
            config_path=str(self.config_path) if self.config_path else None,
        )
        log.info("TTS voice loaded in %.0f ms", (time.perf_counter() - t0) * 1000)

    def _cache_key(self, text: str) -> str:
        raw = f"{text}|{self.voice_path}|{self.cfg.length_scale}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def cache_path(self, text: str) -> Path:
        return self.cache_dir / f"{self._cache_key(text)}.wav"

    def forget_cached(self, texts: list[str]) -> int:
        """Delete cached audio for these captions; returns the number removed.

        Cache files are keyed by a hash of the text, so a purged session can only
        be cleaned up while its Bangla transcript still exists.
        """
        removed = 0
        for text in {t for t in texts if (t or "").strip()}:
            try:
                self.cache_path(text).unlink()
            except FileNotFoundError:
                continue
            removed += 1
        return removed

    def synthesize(self, text: str, use_cache: bool = True) -> tuple[bytes, float]:
        """Return (wav_bytes, synthesis_ms). Cached hits report ~0 ms."""
        text = text.strip()
        if not text:
            return b"", 0.0
        self.load()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self.cache_path(text)
        if use_cache and cache_file.is_file():
            return cache_file.read_bytes(), 0.0

        from piper.config import SynthesisConfig

        syn_cfg = SynthesisConfig(length_scale=self.cfg.length_scale)
        t0 = time.perf_counter()
        buf = io.BytesIO()
        with self._lock:
            pcm = bytearray()
            sample_rate = 22050
            for chunk in self._voice.synthesize(text, syn_cfg):
                pcm.extend(chunk.audio_int16_bytes)
                sample_rate = chunk.sample_rate
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(bytes(pcm))
        data = buf.getvalue()
        if use_cache and data:
            tmp = cache_file.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(cache_file)
        return data, (time.perf_counter() - t0) * 1000


class NullTts:
    """Used when TTS is disabled; students fall back to captions."""

    available = True

    def load(self) -> None:  # pragma: no cover - trivial
        return None

    def synthesize(self, text: str, use_cache: bool = True) -> tuple[bytes, float]:
        return b"", 0.0

    def forget_cached(self, texts: list[str]) -> int:
        return 0
