"""Direction routing for continuous English/Bengali classroom translation.

The language decision is turn-scoped: only stable committed transcript text is
counted, and a route changes after a VAD turn boundary when script evidence is
strong enough. A single provisional chunk can never flip the route.
"""

from __future__ import annotations

import re
import threading
from typing import Any

from .config import Settings
from .translate import MarianTranslator, NllbTranslator, PassthroughTranslator
from .tts import NullTts, PiperTts

_BENGALI = re.compile(r"[\u0980-\u09FF]")
_LATIN = re.compile(r"[A-Za-z]")


def language_pair(source: str) -> tuple[str, str]:
    source = (source or "en").strip().lower()
    if source in {"bn", "ben", "bengali"}:
        return "bn", "en"
    if source in {"en", "eng", "english"}:
        return "en", "bn"
    if source == "auto":
        return "auto", "auto"
    raise ValueError("source language must be 'en', 'bn', or 'auto'")


class TurnLanguageRouter:
    """Keep one direction for a turn; switch only on confident script evidence."""

    def __init__(self, source_language: str = "en", min_letters: int = 12,
                 confidence: float = 0.78) -> None:
        source, target = language_pair(source_language)
        self.requested_source = source
        self.source_language = "en" if source == "auto" else source
        self.target_language = "bn" if self.source_language == "en" else "en"
        self.min_letters = max(1, int(min_letters))
        self.confidence = min(0.99, max(0.5, float(confidence)))
        self._bn = 0
        self._latin = 0
        self._turn_source: str | None = None
        self.turn_switches = 0

    @property
    def asr_language(self) -> str | None:
        # Auto mode lets Whisper detect a whole decode window. Explicit modes pin
        # the recognizer; turn changes happen only after VAD end, never per chunk.
        if self.requested_source == "auto":
            return self._turn_source
        return self.source_language

    def decode_language(self) -> str:
        """Language to hand the recognizer for the next decode ("auto" = let it detect).

        Explicit sessions are pinned to their source language, which is also what
        selects the Bengali model. In auto mode the recognizer decides until the
        turn's language is known.
        """
        return self.asr_language or "auto"

    def observe_detected(self, language: str | None, prob: float = 1.0) -> bool:
        """Route this turn from a recognizer language probe.

        Script counting alone cannot discover Bengali: a model that cannot write
        Bengali script emits romanised Latin, so the parser never sees Bengali
        characters and the turn stays on the English route. The probe gives the
        route a signal before the first caption is committed. Returns True when
        the route changed.
        """
        if self.requested_source != "auto" or self._turn_source is not None:
            return False
        if language not in {"bn", "en"} or prob < 0.5:
            return False
        self._turn_source = language
        if language == self.source_language:
            return False
        self.source_language = language
        self.target_language = "en" if language == "bn" else "bn"
        self.turn_switches += 1
        return True

    def observe_stable(self, text: str) -> None:
        self._bn += len(_BENGALI.findall(text or ""))
        self._latin += len(_LATIN.findall(text or ""))
        total = self._bn + self._latin
        if self.requested_source == "auto" and self._turn_source is None and total >= self.min_letters:
            bn_ratio = self._bn / total
            latin_ratio = self._latin / total
            winner = "bn" if bn_ratio >= self.confidence else (
                "en" if latin_ratio >= self.confidence else None
            )
            if winner:
                self._turn_source = winner
                if winner != self.source_language:
                    self.source_language = winner
                    self.target_language = "en" if winner == "bn" else "bn"
                    self.turn_switches += 1

    def end_turn(self) -> tuple[str, str]:
        self._bn = self._latin = 0
        self._turn_source = None
        return self.source_language, self.target_language

    def snapshot(self) -> dict[str, str]:
        return {"source_language": self.source_language, "target_language": self.target_language}


class BidirectionalTranslator:
    """Lazy, offline directional Marian/OPUS models, each loaded at most once."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.backend = (settings.translate.backend or "marian").lower()
        self._models: dict[tuple[str, str], Any] = {}
        self._lock = threading.RLock()

    @property
    def available(self) -> bool:
        if not self.settings.translate.enabled:
            return True
        if self.backend == "nllb":
            model = self.settings.resolve(self.settings.translate.nllb_model_dir)
            return bool(model and (model / "model.bin").is_file())
        en_bn = self.settings.resolve(self.settings.translate.model_dir)
        bn_en = self.settings.resolve(self.settings.translate.bn_en_model_dir)
        return bool((en_bn and (en_bn / "model.bin").is_file()) or
                    (bn_en and (bn_en / "model.bin").is_file()))

    def _build(self, source: str, target: str) -> Any:
        if not self.settings.translate.enabled:
            return PassthroughTranslator()
        cfg = self.settings.translate
        if self.backend == "nllb":
            import copy
            settings = copy.copy(self.settings)
            settings.translate = copy.copy(cfg)
            settings.translate.nllb_src_lang = "ben_Beng" if source == "bn" else "eng_Latn"
            settings.translate.nllb_tgt_lang = "eng_Latn" if target == "en" else "ben_Beng"
            return NllbTranslator(settings)
        model_dir = cfg.model_dir if (source, target) == ("en", "bn") else cfg.bn_en_model_dir
        if (source, target) == ("bn", "en"):
            import copy

            settings = copy.copy(self.settings)
            settings.translate = copy.copy(cfg)
            settings.translate.model_dir = model_dir
            settings.translate.tokenizer_dir = cfg.bn_en_tokenizer_dir
            return MarianTranslator(settings, model_dir=model_dir,
                                    source_language=source, target_language=target)
        return MarianTranslator(self.settings, model_dir=model_dir,
                                source_language=source, target_language=target)

    def load_direction(self, source: str, target: str) -> None:
        key = (source, target)
        with self._lock:
            if key not in self._models:
                self._models[key] = self._build(source, target)
            model = self._models[key]
        model.load()

    def load(self) -> None:
        # Directional weights stay lazy until a session selects a route.
        return None

    def translate_for(self, text: str, source: str, target: str):
        if (source, target) not in {("en", "bn"), ("bn", "en")}:
            raise ValueError(f"unsupported translation direction: {source}->{target}")
        key = (source, target)
        with self._lock:
            model = self._models.get(key)
            if model is None:
                model = self._build(source, target)
                self._models[key] = model
        return model.translate(text)

    def translate(self, text: str):
        return self.translate_for(text, "en", "bn")

    def translate_batch(self, texts: list[str], beam_size: int | None = None):
        with self._lock:
            model = self._models.get(("en", "bn"))
            if model is None:
                model = self._build("en", "bn")
                self._models[("en", "bn")] = model
        return model.translate_batch(texts, beam_size=beam_size)


class BidirectionalPiperTts:
    """Lazy voice-specific Piper instances; one synthesis is shared per phrase."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._voices: dict[str, Any] = {}
        self._lock = threading.RLock()

    @property
    def available(self) -> bool:
        cfg = self.settings.tts
        return any(path and self.settings.resolve(path).is_file() for path in
                   (cfg.voice_onnx, cfg.english_voice_onnx))

    def _voice(self, target: str):
        target = "en" if target == "en" else "bn"
        with self._lock:
            if target not in self._voices:
                import copy
                settings = copy.copy(self.settings)
                settings.tts = copy.copy(self.settings.tts)
                if target == "en":
                    settings.tts.voice_onnx = settings.tts.english_voice_onnx
                    settings.tts.voice_config = settings.tts.english_voice_config
                self._voices[target] = PiperTts(settings)
            return self._voices[target]

    def load(self) -> None:
        # The selected target voice is preloaded after session routing is known.
        return None

    def load_for(self, target: str) -> None:
        self._voice(target).load()

    def synthesize_for(self, text: str, target: str):
        return self._voice(target).synthesize(text)

    @property
    def cache_dir(self):
        return self.settings.resolve(self.settings.tts.cache_dir)

    def cache_path(self, text: str):
        return self._voice("bn").cache_path(text)

    def synthesize(self, text: str, use_cache: bool = True):
        return self._voice("bn").synthesize(text, use_cache=use_cache)

    def forget_cached(self, texts: list[str]) -> int:
        # Build cache keys from configured voice paths without loading ONNX models.
        removed = 0
        for target in ("bn", "en"):
            voice = self._voice(target)
            removed += voice.forget_cached(texts)
        return removed


class NullBidirectionalTts(NullTts):
    def synthesize_for(self, text: str, target: str):
        return self.synthesize(text)

    def load_for(self, target: str) -> None:
        self.load()
