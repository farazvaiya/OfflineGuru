"""Offline speech recognition via faster-whisper (CTranslate2).

The model is loaded lazily and only from a local directory.  If the directory is
missing the class raises ``ModelUnavailable``; the server surfaces that as a
health warning instead of silently calling a cloud API.

Two models may be configured.  ``model_dir`` is the general multilingual model
(English, plus whatever else it can actually write).  ``bn_model_dir`` is an
optional Bengali fine-tune: stock whisper-base cannot emit Bengali script at
all - it romanises Bengali speech into Latin/other scripts - so every Bengali
decode is routed to the fine-tune.  See
``reports/BANGLA_TO_ENGLISH_ROOT_CAUSE.md`` for the evidence.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .config import AsrConfig, Settings
from .realtime import Word

log = logging.getLogger(__name__)


class ModelUnavailable(RuntimeError):
    """Raised when an offline model asset is missing or fails to load."""


@dataclass
class AsrResult:
    text: str
    language: str
    avg_logprob: float
    no_speech_prob: float
    latency_ms: float
    audio_s: float

    @property
    def confidence(self) -> float:
        """Rough 0..1 confidence from the model's own log probabilities."""
        import math

        return max(0.0, min(1.0, math.exp(self.avg_logprob)))


class WhisperAsr:
    #: Languages served by ``bn_model_dir``.
    BN_LANGUAGES = frozenset({"bn", "ben", "bengali"})
    #: Confidence a language probe needs before it is trusted for routing.
    DETECT_MIN_PROB = 0.5

    def __init__(self, settings: Settings) -> None:
        self.cfg: AsrConfig = settings.asr
        self.model_dir = settings.resolve(self.cfg.model_dir)
        bn_dir = (getattr(self.cfg, "bn_model_dir", "") or "").strip()
        self.bn_model_dir: Path | None = settings.resolve(bn_dir) if bn_dir else None
        self._models: dict[Path, Any] = {}
        self._lock = threading.RLock()
        self._warned_bn_missing = False
        # Filled by the last auto-language decode so callers can route a turn.
        self.last_detected_language: str | None = None
        self.last_detection_prob: float = 0.0

    @property
    def available(self) -> bool:
        return bool(self.model_dir and self.model_dir.is_dir())

    @property
    def bn_available(self) -> bool:
        return bool(self.bn_model_dir and self.bn_model_dir.is_dir())

    # -- model selection ---------------------------------------------------
    def _dir_for(self, language: str | None) -> Path:
        if language and str(language).lower() in self.BN_LANGUAGES:
            if self.bn_available:
                return self.bn_model_dir  # type: ignore[return-value]
            if not self._warned_bn_missing:
                self._warned_bn_missing = True
                log.warning(
                    "Bengali ASR model missing at %s; falling back to %s "
                    "(Bengali will be romanised instead of written in Bengali script)",
                    self.bn_model_dir or "<unset>",
                    self.model_dir,
                )
        return self.model_dir

    def _get_model(self, path: Path | None) -> Any:
        if path is None:
            raise ModelUnavailable("no ASR model directory configured")
        with self._lock:
            model = self._models.get(path)
            if model is not None:
                return model
            if not path.is_dir():
                raise ModelUnavailable(f"ASR model not found at {path}")
            from faster_whisper import WhisperModel

            t0 = time.perf_counter()
            model = WhisperModel(
                str(path),
                device="cpu",
                compute_type=self.cfg.compute_type,
                cpu_threads=self.cfg.cpu_threads,
                local_files_only=True,
            )
            self._models[path] = model
            log.info("ASR loaded in %.0f ms (%s)", (time.perf_counter() - t0) * 1000, path)
            return model

    def load(self, language: str | None = None) -> None:
        """Load the model that serves ``language`` (default: the base model)."""
        self._get_model(self._dir_for(self._language(language)))

    def load_for(self, language: str | None) -> None:
        self.load(language)

    def _language(self, language: str | None) -> str | None:
        resolved = str(language if language is not None else (self.cfg.language or "en")).strip()
        if resolved.lower() in {"", "auto"}:
            return None
        return resolved

    # -- language probe ----------------------------------------------------
    def detect_language(self, audio: np.ndarray) -> tuple[str | None, float]:
        """Best-effort language probe, used only when the caller asked for auto.

        The probe runs on the Bengali model's language head because the stock
        base model mislabels Bengali speech (observed top-1: ``ko`` at 0.27 on
        real Bengali), while the fine-tune labels it ``bn`` at 0.92-1.00 and
        does not misfire on English speech.  A probe costs ~1.2 s on this CPU
        regardless of clip length, so callers should cache the answer for a
        whole VAD turn instead of probing every decode window.
        """
        self.last_detected_language = None
        self.last_detection_prob = 0.0
        if audio.size == 0 or not self.bn_available:
            return None, 0.0
        model = self._get_model(self.bn_model_dir)
        try:
            lang, prob, _all_probs = model.detect_language(audio)
        except Exception:  # pragma: no cover - defensive
            log.debug("language detection failed", exc_info=True)
            return None, 0.0
        prob = float(prob or 0.0)
        # Only the two languages this deployment translates are actionable;
        # anything else is left to the multilingual model's own detection.
        if lang in {"bn", "en"} and prob >= self.DETECT_MIN_PROB:
            self.last_detected_language = lang
            self.last_detection_prob = prob
            return lang, prob
        return None, prob

    def _resolve(self, audio: np.ndarray, language: str | None) -> str | None:
        lang = self._language(language)
        if lang is None:
            detected, _prob = self.detect_language(audio)
            if detected:
                return detected
        return lang

    # -- decoding ----------------------------------------------------------
    def transcribe(self, audio: np.ndarray, beam_size: int | None = None,
                   language: str | None = None) -> AsrResult:
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32, copy=False)
        lang = self._resolve(audio, language)
        model = self._get_model(self._dir_for(lang))
        t0 = time.perf_counter()
        hotwords = (self.cfg.hotwords or "").strip() or None
        segments, info = model.transcribe(
            audio,
            language=lang,
            beam_size=beam_size if beam_size is not None else self.cfg.beam_size,
            vad_filter=False,          # the segmenter already did VAD
            condition_on_previous_text=False,  # avoids cross-segment hallucination loops
            temperature=0.0,
            without_timestamps=True,
            hotwords=hotwords,          # bias domain vocabulary (science terms)
        )
        # Materialise once: ``segments`` is a generator and is consumed below.
        seg_list = list(segments)
        text = " ".join(s.text.strip() for s in seg_list if s.text.strip()).strip()
        latency = (time.perf_counter() - t0) * 1000
        probs = [getattr(s, "avg_logprob", -1.0) for s in seg_list] or [-1.0]
        no_speech = [getattr(s, "no_speech_prob", 0.0) for s in seg_list] or [0.0]
        info_lang = getattr(info, "language", None) or lang or self.cfg.language
        if self.last_detected_language is None and info_lang:
            # Expose the model's own detection when the probe was unavailable.
            self.last_detected_language = info_lang
            self.last_detection_prob = float(getattr(info, "language_probability", 0.0) or 0.0)
        return AsrResult(
            text=text,
            language=info_lang,
            avg_logprob=float(sum(probs) / len(probs)),
            no_speech_prob=float(max(no_speech)),
            latency_ms=latency,
            audio_s=len(audio) / 16000.0,
        )

    def transcribe_words(
        self,
        audio: np.ndarray,
        offset: float = 0.0,
        beam_size: int = 1,
        prompt: str | None = None,
        language: str | None = None,
    ) -> list[Word]:
        """Decode a window and return word-level tokens with absolute times.

        This is the streaming entry point: LocalAgreement needs per-word
        timestamps and probabilities to decide what is stable.  Measured cost of
        word timestamps on this CPU: ~2% over a plain decode.
        """
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32, copy=False)
        if audio.size == 0:
            return []
        lang = self._resolve(audio, language)
        model = self._get_model(self._dir_for(lang))
        hotwords = (self.cfg.hotwords or "").strip() or None
        segments, _info = model.transcribe(
            audio,
            language=lang,
            beam_size=max(1, int(beam_size)),
            vad_filter=False,
            condition_on_previous_text=False,
            temperature=0.0,
            word_timestamps=True,
            initial_prompt=(prompt or None),
            hotwords=hotwords,
        )
        # Whisper pads every input to 30 s and can emit word timestamps past the
        # real audio length. Unclamped, those timestamps push the rolling
        # window's clock forward and every later segment is mis-timed.
        duration = len(audio) / 16000.0
        words: list[Word] = []
        for seg in segments:
            if getattr(seg, "no_speech_prob", 0.0) > 0.9:
                continue
            for word in getattr(seg, "words", None) or []:
                text = (word.word or "").strip()
                if not text:
                    continue
                start = min(max(0.0, float(word.start)), duration)
                end = min(max(start, float(word.end)), duration)
                words.append(
                    Word(
                        text=text,
                        start=offset + start,
                        end=offset + end,
                        prob=float(getattr(word, "probability", 0.0) or 0.0),
                    )
                )
        return words
