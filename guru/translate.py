"""Offline English→Bangla translation using Marian + CTranslate2.

Why Marian and not a general LLM: on CPU-only laptops a 1–3 s latency budget is
only realistic for a small dedicated encoder-decoder.  See docs/MODEL_SELECTION.md
for the measured comparison.

The translator never calls the network: the tokenizer and model both load from
the local ``marian-en-bn-ct2`` directory with ``local_files_only=True``.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .asr import ModelUnavailable
from .config import Settings, TranslateConfig

log = logging.getLogger(__name__)

_HAS_LETTER = re.compile(r"[A-Za-z\u0980-\u09FF]")

# One decode call per chunk keeps the translator lock hold short: a live caption
# waits for at most one chunk, never for the fifteen decodes a summary used to need.
BATCH_CHUNK = 16


@dataclass
class Translation:
    source: str
    text: str
    latency_ms: float
    skipped: bool = False
    reason: str | None = None
    numbers_preserved: bool = True
    missing_numbers: list[str] = field(default_factory=list)


def normalize_source(text: str) -> str:
    """Collapse whitespace and strip control characters from transcript text."""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    return re.sub(r"\s+", " ", cleaned).strip()


_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def normalize_digits(text: str) -> str:
    """Map Bengali/Devanagari digits to ASCII so number checks are script-agnostic."""
    text = text.translate(_BN_DIGITS)
    return text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))


def extract_numbers(text: str) -> list[str]:
    """Numeric tokens in either ASCII or Bengali digits (returns ASCII)."""
    return [m.group(0).strip() for m in re.finditer(r"[0-9০-৯]+(?:[.,][0-9০-৯]+)?", text)]


def _pending_indices(prepared: list[str], out: list[Translation], max_chars: int) -> list[int]:
    """Mark untranslatable items and return the indices that need the model."""
    pending: list[int] = []
    for i, source in enumerate(prepared):
        if not source or not _HAS_LETTER.search(source):
            out[i].skipped = True
            out[i].reason = "no translatable text"
            out[i].text = source  # echo numbers/symbols unchanged
        elif len(source) > max_chars:
            out[i].skipped = True
            out[i].reason = "input too long"
            out[i].text = source
        else:
            pending.append(i)
    return pending


def _check_numbers(item: Translation, source: str, translated: str) -> None:
    """Record whether the source's numbers survived the translation."""
    translated_digits = normalize_digits(translated)
    missing = [
        n for n in extract_numbers(source)
        if normalize_digits(n).rstrip(".,") not in translated_digits
    ]
    item.numbers_preserved = not missing
    item.missing_numbers = missing


def _decode_one(out: list[Translation], prepared: list[str], index: int, beam: int,
                decode_one: Any) -> None:
    source = prepared[index]
    try:
        translated = decode_one(source, beam)
    except Exception as exc:  # keep the classroom running
        log.exception("translation failed")
        out[index].skipped = True
        out[index].reason = f"translation error: {exc}"
        out[index].text = ""
        return
    out[index].text = translated
    _check_numbers(out[index], source, translated)


def _decode_chunk(out: list[Translation], prepared: list[str], chunk: list[int], beam: int,
                  decode_one: Any, decode_many: Any) -> None:
    try:
        translations = decode_many([prepared[i] for i in chunk], beam)
    except Exception:  # a batch failure must not lose the whole summary
        log.exception("batched translation failed; retrying one by one")
        for i in chunk:
            _decode_one(out, prepared, i, beam, decode_one)
        return
    for i, translated in zip(chunk, translations):
        out[i].text = translated
        _check_numbers(out[i], prepared[i], translated)


def _translate_pending(out: list[Translation], prepared: list[str], pending: list[int], beam: int,
                       decode_one: Any, decode_many: Any) -> None:
    for start in range(0, len(pending), BATCH_CHUNK):
        chunk = pending[start : start + BATCH_CHUNK]
        if len(chunk) == 1:
            _decode_one(out, prepared, chunk[0], beam, decode_one)
        else:
            _decode_chunk(out, prepared, chunk, beam, decode_one, decode_many)


class MarianTranslator:
    """Thread-safe wrapper around a CTranslate2 Marian model."""

    def __init__(self, settings: Settings, model_dir: str | None = None,
                 source_language: str = "en", target_language: str = "bn") -> None:
        self.cfg: TranslateConfig = settings.translate
        self.settings = settings
        self.model_dir = settings.resolve(model_dir or self.cfg.model_dir)
        self.tokenizer_dir = settings.resolve(getattr(self.cfg, "tokenizer_dir", None)) or self.model_dir
        self.source_language = source_language
        self.target_language = target_language
        self._translator: Any = None
        self._tokenizer: Any = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self.model_dir and (self.model_dir / "model.bin").is_file())

    def load(self) -> None:
        if self._translator is not None:
            return
        if not self.available:
            raise ModelUnavailable(f"translation model not found at {self.model_dir}")
        import ctranslate2
        from transformers import MarianTokenizer

        t0 = time.perf_counter()
        # Load the tokenizer class explicitly: the directory's config.json belongs
        # to CTranslate2, so AutoTokenizer would look for a model_type it does not own.
        # local_files_only guarantees the offline contract even if the directory
        # were incomplete (otherwise transformers could try the Hub).
        tokenizer_dir = self.tokenizer_dir
        meta_path = self.model_dir / "guru_meta.json"
        if meta_path.is_file():
            import json

            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            tokenizer_path = metadata.get("tokenizer_dir")
            if tokenizer_path:
                tokenizer_dir = self.settings.resolve(tokenizer_path) or tokenizer_dir
        self._tokenizer = MarianTokenizer.from_pretrained(
            str(tokenizer_dir), local_files_only=True
        )
        self._translator = ctranslate2.Translator(
            str(self.model_dir),
            device="cpu",
            compute_type=self.cfg.compute_type,
            inter_threads=1,
            intra_threads=self.cfg.intra_threads,
        )
        log.info("translator loaded in %.0f ms", (time.perf_counter() - t0) * 1000)

    def _translate_many(self, sources: list[str], beam_size: int) -> list[str]:
        """Decode several sentences in one model call."""
        # Marian expects a trailing </s>; encode() already appends it.
        token_batches = [
            self._tokenizer.convert_ids_to_tokens(self._tokenizer.encode(text))
            for text in sources
        ]
        results = self._translator.translate_batch(
            token_batches,
            beam_size=beam_size,
            max_decoding_length=512,
            replace_unknowns=True,
        )
        out: list[str] = []
        for result in results:
            target_ids = self._tokenizer.convert_tokens_to_ids(result.hypotheses[0])
            out.append(self._tokenizer.decode(target_ids, skip_special_tokens=True).strip())
        return out

    def _translate_one(self, text: str, beam_size: int) -> str:
        return self._translate_many([text], beam_size)[0]

    def translate_batch(self, texts: list[str], beam_size: int | None = None) -> list[Translation]:
        if self._translator is None:
            self.load()
        prepared = [normalize_source(t) for t in texts]
        out: list[Translation] = [Translation(source=s, text="", latency_ms=0.0) for s in prepared]
        pending = _pending_indices(prepared, out, self.cfg.max_input_chars)
        if not pending:
            return out

        beam = beam_size or self.cfg.beam_size
        t0 = time.perf_counter()
        with self._lock:
            _translate_pending(out, prepared, pending, beam, self._translate_one, self._translate_many)
        elapsed = (time.perf_counter() - t0) * 1000
        per_item = elapsed / max(1, len(pending))
        for i in pending:
            out[i].latency_ms = per_item
        return out

    def translate(self, text: str) -> Translation:
        return self.translate_batch([text])[0]


class NllbTranslator:
    """Optional higher-quality backend: NLLB-200 distilled 600M, int8 CPU.

    Measured ~10× slower than Marian (2.1 s vs 0.22 s per sentence) but noticeably
    more accurate on science text. Select with ``GURU_TRANSLATE__BACKEND=nllb``;
    end-to-end latency then sits around 3 s rather than 1.5 s.
    """

    def __init__(self, settings: Settings) -> None:
        self.cfg: TranslateConfig = settings.translate
        self.model_dir = settings.resolve(self.cfg.nllb_model_dir)
        self.tokenizer_dir = settings.resolve(self.cfg.nllb_tokenizer_dir)
        self._model: Any = None
        self._tokenizer: Any = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(
            self.model_dir
            and (self.model_dir / "model.bin").is_file()
            and self.tokenizer_dir
            and (self.tokenizer_dir / "tokenizer.json").is_file()
        )

    def load(self) -> None:
        if self._model is not None:
            return
        if not self.available:
            raise ModelUnavailable(f"NLLB model or tokenizer missing ({self.model_dir})")
        import ctranslate2
        from transformers import AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(
            str(self.tokenizer_dir),
            src_lang=self.cfg.nllb_src_lang,
            local_files_only=True,
        )
        self._model = ctranslate2.Translator(
            str(self.model_dir),
            device="cpu",
            compute_type=self.cfg.compute_type,
            inter_threads=1,
            intra_threads=self.cfg.intra_threads,
        )

    def _translate_many(self, sources: list[str], beam_size: int) -> list[str]:
        """Decode several sentences in one model call."""
        token_batches = [
            self._tokenizer.convert_ids_to_tokens(self._tokenizer.encode(text))
            for text in sources
        ]
        results = self._model.translate_batch(
            token_batches,
            target_prefix=[[self.cfg.nllb_tgt_lang]] * len(sources),
            beam_size=beam_size,
            max_decoding_length=512,
        )
        out: list[str] = []
        for result in results:
            tokens = [t for t in result.hypotheses[0] if t != self.cfg.nllb_tgt_lang]
            ids = self._tokenizer.convert_tokens_to_ids(tokens)
            out.append(self._tokenizer.decode(ids, skip_special_tokens=True).strip())
        return out

    def _translate_one(self, text: str, beam_size: int) -> str:
        return self._translate_many([text], beam_size)[0]

    def translate_batch(self, texts: list[str], beam_size: int | None = None) -> list[Translation]:
        if self._model is None:
            self.load()
        prepared = [normalize_source(t) for t in texts]
        out = [Translation(source=s, text="", latency_ms=0.0) for s in prepared]
        pending = _pending_indices(prepared, out, self.cfg.max_input_chars)
        if not pending:
            return out

        beam = beam_size or self.cfg.beam_size
        t0 = time.perf_counter()
        with self._lock:
            _translate_pending(out, prepared, pending, beam, self._translate_one, self._translate_many)
        elapsed = (time.perf_counter() - t0) * 1000
        per_item = elapsed / max(1, len(pending))
        for i in pending:
            out[i].latency_ms = per_item
        return out

    def translate(self, text: str) -> Translation:
        return self.translate_batch([text])[0]


class PassthroughTranslator:
    """Fallback used when translation is disabled or unavailable."""

    available = True

    def load(self) -> None:  # pragma: no cover - trivial
        return None

    def translate_batch(self, texts: list[str], beam_size: int | None = None) -> list[Translation]:
        return [
            Translation(source=t, text=t, latency_ms=0.0, skipped=True, reason="translation disabled")
            for t in texts
        ]

    def translate(self, text: str) -> Translation:
        return self.translate_batch([text])[0]


def build_translator(settings: Settings) -> Any:
    """Return the configured translation backend (never loads the model here)."""
    if not settings.translate.enabled:
        return PassthroughTranslator()
    backend = (settings.translate.backend or "marian").lower()
    if backend == "nllb":
        return NllbTranslator(settings)
    return MarianTranslator(settings)
