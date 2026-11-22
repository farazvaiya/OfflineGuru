"""Isolated OPUS-MT benchmark adapters (EN->BN and BN->EN).

This module is intentionally separate from the production ``guru.translate``
module so the existing Marian/NLLB pipeline is never touched during benchmarking.

Supports three inference paths per direction:

* ``ct2_int8``  — CTranslate2 INT8 (the candidate optimization)
* ``transformers_cpu`` — HuggingFace Transformers + MarianTokenizer on CPU
* ``ct2_float`` — CTranslate2 float16/float32 (control, when available)

Model loading happens once at startup.  There is **no automatic download** inside
this module: the caller must point it at directories that already contain the
CTranslate2-converted models (see ``scripts/prepare_opus_models.py``).

Design notes
------------

* Both OPUS-MT directions use the same Marian architecture, so the same
  tokenizer/ conversion pipeline works for each.
* Marian models ship a ``MarianMTModel`` + ``MarianTokenizer`` pair.  For the
  CTranslate2 path we convert with ``ctranslate2.converters.TransformersConverter``
  and tokenize with ``MarianTokenizer`` loaded from the CT2 directory.
* For the Transformers CPU path we load the original HF repo with
  ``local_files_only=True`` (offline) and reuse the already-converted CT2
  directory's tokenizer assets when present.
* Thread safety: each adapter instance owns one CTranslate2 ``Translator`` and
  one lock, matching the production ``MarianTranslator`` pattern.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_HAS_LETTER = __import__("re").compile(r"[A-Za-z\u0980-\u09FF]")


# ---------------------------------------------------------------------------
# Shared helpers (mirrors ``guru/translate.py`` so the benchmark is self-standing)
# ---------------------------------------------------------------------------

def normalize_source(text: str) -> str:
    import re
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    return re.sub(r"\s+", " ", cleaned).strip()


_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def normalize_digits(text: str) -> str:
    text = text.translate(_BN_DIGITS)
    return text.translate(_ARABIC_DIGITS)


def extract_numbers(text: str) -> list[str]:
    import re
    return [m.group(0).strip() for m in re.finditer(r"[0-9০-৯]+(?:[.,][0-9০-৯]+)?", text)]


def _check_numbers(source: str, translated: str) -> tuple[bool, list[str]]:
    translated_digits = normalize_digits(translated)
    missing = [
        n for n in extract_numbers(source)
        if normalize_digits(n).rstrip(".,") not in translated_digits
    ]
    return (not missing, missing)


@dataclass
class Translation:
    source: str
    text: str
    latency_ms: float
    skipped: bool = False
    reason: str | None = None
    numbers_preserved: bool = True
    missing_numbers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Configuration for one adapter direction
# ---------------------------------------------------------------------------

@dataclass
class OpusDirectionConfig:
    """Configuration for one translation direction."""
    name: str                      # e.g. "en_to_bn" or "bn_to_en"
    model_id: str                  # HF repo id, e.g. "shhossain/opus-mt-en-to-bn"
    revision: str                  # HF revision/commit, recorded for the report
    src_lang: str                  # e.g. "en", "bn"
    tgt_lang: str                  # e.g. "bn", "en"
    src_lang_code: str | None = None   # Marian lang code for target_prefix if needed
    tgt_lang_code: str | None = None   # Marian lang code for target_prefix if needed
    license: str = "unknown"       # recorded from model card when known
    local_ct2_dir: Path | None = None   # pre-converted CTranslate2 model
    local_hf_dir: Path | None = None    # original HF checkout (tokenizer assets)
    allowed_paths: tuple[str, ...] = ("ct2_int8", "transformers_cpu", "ct2_float")


@dataclass
class OpusAdapterSettings:
    """Settings shared by both direction adapters."""
    en_to_bn: OpusDirectionConfig
    bn_to_en: OpusDirectionConfig
    beam_size: int = 4
    intra_threads: int = 2
    inter_threads: int = 1
    max_input_chars: int = 1200
    max_decoding_length: int = 512
    timeout_s: float = 30.0


# ---------------------------------------------------------------------------
# Adapter base + concrete implementations
# ---------------------------------------------------------------------------

class OpusAdapter:
    """Thread-safe adapter for one OPUS-MT direction.

    Public API mirrors the production translator closely so benchmark results are
    comparable::

        adapter = OpusEnToBnAdapter(settings)
        adapter.load()                 # raises if the model dir is missing
        out = adapter.translate_batch(["Hello world"], beam_size=4)

    ``translate_batch`` returns a list of ``Translation`` objects with per-item
    latency, number-preservation tracking, and skip reasons — same shape as the
    production path.
    """

    def __init__(self, cfg: OpusDirectionConfig, shared: OpusAdapterSettings) -> None:
        self.cfg = cfg
        self.shared = shared
        self._model: Any = None
        self._tokenizer: Any = None
        self._lock = threading.Lock()
        self._load_time_s: float = 0.0
        self._loaded = False

    @property
    def available(self) -> bool:
        raise NotImplementedError

    def load(self) -> None:
        raise NotImplementedError

    def translate_batch(self, texts: list[str], beam_size: int | None = None) -> list[Translation]:
        raise NotImplementedError

    def translate(self, text: str, beam_size: int | None = None) -> Translation:
        return self.translate_batch([text], beam_size)[0]

    # -- internal helpers -------------------------------------------------
    def _prepare(self, texts: list[str]) -> tuple[list[str], list[Translation], list[int]]:
        prepared = [normalize_source(t) for t in texts]
        out: list[Translation] = [
            Translation(source=s, text="", latency_ms=0.0) for s in prepared
        ]
        pending: list[int] = []
        for i, source in enumerate(prepared):
            if not source or not _HAS_LETTER.search(source):
                out[i].skipped = True
                out[i].reason = "no translatable text"
                out[i].text = source
            elif len(source) > self.shared.max_input_chars:
                out[i].skipped = True
                out[i].reason = "input too long"
                out[i].text = source
            else:
                pending.append(i)
        return prepared, out, pending

    def _run_batch(self, sources: list[str], beam: int) -> list[str]:
        raise NotImplementedError

    def _run_one(self, source: str, beam: int) -> str:
        return self._run_batch([source], beam)[0]


class OpusCt2Adapter(OpusAdapter):
    """CTranslate2 INT8 (or FLOAT) inference for one OPUS-MT direction."""

    def __init__(self, cfg: OpusDirectionConfig, shared: OpusAdapterSettings,
                 compute_type: str = "int8") -> None:
        super().__init__(cfg, shared)
        self.compute_type = compute_type

    @property
    def available(self) -> bool:
        d = self.cfg.local_ct2_dir
        return bool(d and d.is_dir() and (d / "model.bin").is_file())

    def load(self) -> None:
        if self._loaded:
            return
        if not self.available:
            raise RuntimeError(
                f"{self.cfg.name}: CTranslate2 model not found at {self.cfg.local_ct2_dir}"
            )
        import ctranslate2

        t0 = time.perf_counter()
        self._tokenizer = self._load_tokenizer()
        self._model = ctranslate2.Translator(
            str(self.cfg.local_ct2_dir),
            device="cpu",
            compute_type=self.compute_type,
            inter_threads=self.shared.inter_threads,
            intra_threads=self.shared.intra_threads,
            max_queued_batches=0,
        )
        self._load_time_s = time.perf_counter() - t0
        self._loaded = True
        log.info("%s CT2 %s loaded in %.0f ms",
                 self.cfg.name, self.compute_type,
                 self._load_time_s * 1000)

    def _load_tokenizer(self) -> Any:
        from transformers import MarianTokenizer

        # Prefer the CT2 directory's copied tokenizer assets (offline-safe).
        ct2 = self.cfg.local_ct2_dir
        for name in ("tokenizer_config.json", "vocab.json", "special_tokens_map.json",
                     "source.spm", "target.spm", "generation_config.json"):
            if (ct2 / name).is_file():
                log.debug("%s: using tokenizer asset from CT2 dir: %s", self.cfg.name, name)
                break
        return MarianTokenizer.from_pretrained(str(self.cfg.local_ct2_dir), local_files_only=True)

    def _run_batch(self, sources: list[str], beam: int) -> list[str]:
        token_batches = [
            self._tokenizer.convert_ids_to_tokens(self._tokenizer.encode(text))
            for text in sources
        ]
        results = self._model.translate_batch(
            token_batches,
            beam_size=beam,
            max_decoding_length=self.shared.max_decoding_length,
            replace_unknowns=True,
        )
        out: list[str] = []
        for result in results:
            target_ids = self._tokenizer.convert_tokens_to_ids(result.hypotheses[0])
            out.append(self._tokenizer.decode(target_ids, skip_special_tokens=True).strip())
        return out

    def translate_batch(self, texts: list[str], beam_size: int | None = None) -> list[Translation]:
        if not self._loaded:
            self.load()
        prepared, out, pending = self._prepare(texts)
        if not pending:
            return out

        beam = beam_size or self.shared.beam_size
        t0 = time.perf_counter()
        with self._lock:
            for start in range(0, len(pending), 16):
                chunk = pending[start:start + 16]
                if len(chunk) == 1:
                    translated = self._run_one(prepared[chunk[0]], beam)
                    out[chunk[0]].text = translated
                    ok, missing = _check_numbers(prepared[chunk[0]], translated)
                    out[chunk[0]].numbers_preserved = ok
                    out[chunk[0]].missing_numbers = missing
                else:
                    translations = self._run_batch([prepared[i] for i in chunk], beam)
                    for i, translated in zip(chunk, translations):
                        out[i].text = translated
                        ok, missing = _check_numbers(prepared[i], translated)
                        out[i].numbers_preserved = ok
                        out[i].missing_numbers = missing
        elapsed = (time.perf_counter() - t0) * 1000
        per_item = elapsed / max(1, len(pending))
        for i in pending:
            out[i].latency_ms = per_item
        return out


class OpusTransformersAdapter(OpusAdapter):
    """HuggingFace Transformers CPU inference for one OPUS-MT direction.

    Uses ``MarianMTModel.generate`` + ``MarianTokenizer`` loaded from the local
    HF directory.  This **requires** the original HF model checkout (with
    ``model.safetensors`` or ``pytorch_model.bin``), not the CT2-converted
    directory.

    If the HF dir is missing the model weights, loading fails loudly so the
    benchmark records BLOCKED rather than silently inventing numbers.
    """

    def __init__(self, cfg: OpusDirectionConfig, shared: OpusAdapterSettings) -> None:
        super().__init__(cfg, shared)

    @property
    def available(self) -> bool:
        d = self.cfg.local_hf_dir
        if d is None or not d.is_dir():
            return False
        # Must have HF model weights, not just tokenizer assets.
        return bool(
            (d / "model.safetensors").is_file()
            or (d / "pytorch_model.bin").is_file()
            or (d / "model.bin").is_file()
        )

    def load(self) -> None:
        if self._loaded:
            return
        if not self.available:
            raise RuntimeError(
                f"{self.cfg.name}: Transformers HF model dir not found or missing weights "
                f"(hf={self.cfg.local_hf_dir}; expected model.safetensors/pytorch_model.bin)"
            )
        import torch
        from transformers import MarianMTModel, MarianTokenizer

        t0 = time.perf_counter()
        tok_dir = self.cfg.local_hf_dir
        self._tokenizer = MarianTokenizer.from_pretrained(str(tok_dir), local_files_only=True)

        model_dir = self.cfg.local_hf_dir
        # HF Marian models ship either .safetensors or .bin; accept both.
        if not ((model_dir / "model.safetensors").is_file()
                or (model_dir / "pytorch_model.bin").is_file()
                or (model_dir / "model.bin").is_file()):
            raise RuntimeError(
                f"{self.cfg.name}: HF dir has no model weights: {model_dir}")
        self._model = MarianMTModel.from_pretrained(
            str(model_dir), local_files_only=True,
            torch_dtype=torch.float32,
            ignore_mismatched_sizes=False,
        )
        self._model.eval()
        self._load_time_s = time.perf_counter() - t0
        self._loaded = True
        log.info("%s transformers loaded in %.0f ms", self.cfg.name, self._load_time_s * 1000)

    def _run_batch(self, sources: list[str], beam: int) -> list[str]:
        import torch
        with torch.no_grad():
            inputs = self._tokenizer(
                sources,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=self.shared.max_input_chars,
            )
            # Marian models on CPU: move to CPU explicitly.
            inputs = {k: v.to("cpu") for k, v in inputs.items()}
            generated = self._model.generate(
                **inputs,
                max_length=self.shared.max_decoding_length,
                num_beams=beam,
                num_return_sequences=1,
                early_stopping=True,
            )
        return [self._tokenizer.decode(g, skip_special_tokens=True).strip() for g in generated]

    def translate_batch(self, texts: list[str], beam_size: int | None = None) -> list[Translation]:
        if not self._loaded:
            self.load()
        prepared, out, pending = self._prepare(texts)
        if not pending:
            return out

        beam = beam_size or self.shared.beam_size
        # Transformers path: one batch call for the whole pending set (simpler,
        # and matches how the production benchmark is usually written).
        t0 = time.perf_counter()
        with self._lock:
            translations = self._run_batch([prepared[i] for i in pending], beam)
        elapsed = (time.perf_counter() - t0) * 1000
        for i, translated in zip(pending, translations):
            out[i].text = translated
            ok, missing = _check_numbers(prepared[i], translated)
            out[i].numbers_preserved = ok
            out[i].missing_numbers = missing
            out[i].latency_ms = elapsed / max(1, len(pending))
        return out


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------

def make_en_to_bn_adapter(settings: OpusAdapterSettings,
                          path: str = "ct2_int8") -> OpusAdapter:
    """Return the EN->BN adapter for the requested inference path."""
    cfg = settings.en_to_bn
    if path == "ct2_int8":
        return OpusCt2Adapter(cfg, settings, compute_type="int8")
    if path == "ct2_float":
        return OpusCt2Adapter(cfg, settings, compute_type="float32")
    if path == "transformers_cpu":
        return OpusTransformersAdapter(cfg, settings)
    raise ValueError(f"unknown EN->BN path: {path!r}; pick one of {cfg.allowed_paths}")


def make_bn_to_en_adapter(settings: OpusAdapterSettings,
                          path: str = "ct2_int8") -> OpusAdapter:
    """Return the BN->EN adapter for the requested inference path."""
    cfg = settings.bn_to_en
    if path == "ct2_int8":
        return OpusCt2Adapter(cfg, settings, compute_type="int8")
    if path == "ct2_float":
        return OpusCt2Adapter(cfg, settings, compute_type="float32")
    if path == "transformers_cpu":
        return OpusTransformersAdapter(cfg, settings)
    raise ValueError(f"unknown BN->EN path: {path!r}; pick one of {cfg.allowed_paths}")
