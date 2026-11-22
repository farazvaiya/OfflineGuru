"""Typed configuration for Offline Guru.

Configuration is loaded from a TOML file (default: ``config/default.toml``) and
can be overridden with environment variables using a ``GURU_`` prefix and a
double underscore to separate nested keys, e.g.::

    GURU_SERVER__PORT=8080
    GURU_ASR__MODEL_DIR=/opt/models/whisper-small

Everything here is plain data; importing this module never loads a model and
never touches the network.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PREFIX = "GURU_"


@dataclass
class AsrConfig:
    """Offline speech recognition (faster-whisper / CTranslate2)."""

    # Measured on this laptop class: base gives 3.8% WER at RTF 0.36 vs small
    # 3.1% at RTF 1.09 (see docs/EVALUATION.md). base is the default.
    model_dir: str = "models/whisper-base"
    # Optional Bengali fine-tune (CTranslate2). Stock whisper-base cannot emit
    # Bengali script, so Bengali decodes are routed here; see
    # reports/BANGLA_TO_ENGLISH_ROOT_CAUSE.md. Empty or missing = fall back to
    # model_dir (English keeps working, Bengali is romanised again).
    bn_model_dir: str = "models/whisper-base-bn-ct2"
    compute_type: str = "int8"
    cpu_threads: int = 3
    beam_size: int = 2
    language: str = "en"
    # Auto language mode probes the recognizer once per interval (~1.2 s on this
    # CPU) and caches the answer in between. Longer = cheaper, slower to notice a
    # mid-lesson switch between English and Bengali.
    auto_probe_interval_s: float = 20.0
    # Comma-separated vocabulary bias for domain terms (e.g. "glucose, chlorophyll,
    # stomata"). Biased decoding measurably reduced science-term errors.
    hotwords: str = ""
    # Streaming segmentation knobs.  These are engineering defaults; see
    # docs/EVALUATION.md for the measurements behind them.
    min_segment_s: float = 0.8
    max_segment_s: float = 10.0
    silence_ms: int = 600
    # Provisional captions are OFF by default on 4-core CPUs: measured cost
    # (~1.8 s of ASR per 2.5 s of speech) doubles end-to-end latency. Enable on
    # faster hardware or when word-by-word preview matters more than latency.
    partial_enabled: bool = False
    partial_interval_s: float = 2.5
    partial_min_s: float = 1.2
    # Provisional captions only decode the tail of the buffer: this keeps the
    # live caption cost bounded when the teacher speaks for a long time.
    partial_max_audio_s: float = 5.0
    vad_threshold: float = 0.006
    preroll_ms: int = 300
    enabled: bool = True

    # -- real-time streaming ------------------------------------------------
    # True: decode a rolling window continuously and translate stable phrases
    # while the teacher is still speaking. False: the original behaviour, one
    # decode per VAD segment after a pause.
    streaming: bool = True
    # Minimum new audio before another decode is attempted. The decode itself
    # (~1.8 s for base on this CPU) is the real cadence limiter.
    stream_step_s: float = 0.5
    # Do not decode a window shorter than this (avoids decoding a cough).
    stream_min_audio_s: float = 0.6
    # Upper bound on the rolling window. Whisper pads to 30 s internally, so a
    # larger window costs almost nothing and improves accuracy.
    stream_max_window_s: float = 15.0
    # Audio kept before the last committed word, so the next decode has context.
    stream_context_s: float = 1.0
    # Greedy decoding: measured within 2% of beam 2 on this CPU.
    stream_beam_size: int = 1
    # Commit a word immediately when the model is this confident, instead of
    # waiting for a second agreeing hypothesis. 0.0 = strict LocalAgreement-2
    # (one extra decode of latency). 0.9 keeps first-caption latency at one
    # decode on a slow CPU.
    commit_confidence: float = 0.9


@dataclass
class TranslateConfig:
    """Offline machine translation (Marian / CTranslate2)."""

    # "marian" = fast, meets the 1-3 s target (~0.2 s/sentence).
    # "nllb"   = better Bangla, ~2.1 s/sentence; use when latency is less critical.
    backend: str = "marian"
    model_dir: str = "models/marian-en-bn-ct2"
    nllb_model_dir: str = "models/nllb-600m-ct2-int8"
    nllb_tokenizer_dir: str = "models/nllb-tokenizer"
    nllb_src_lang: str = "eng_Latn"
    nllb_tgt_lang: str = "ben_Beng"
    compute_type: str = "int8"
    intra_threads: int = 2
    beam_size: int = 4
    max_input_chars: int = 1200
    target_language: str = "bn"
    # Reverse OPUS/Marian model (Bengali -> English), converted offline to CT2 int8.
    bn_en_model_dir: str = "models/opus-bn-en-ct2"
    bn_en_tokenizer_dir: str = "models/opus-bn-en-src"
    enabled: bool = True

    # -- incremental translation -------------------------------------------
    # A phrase closes (and is translated) when it ends with sentence
    # punctuation, reaches phrase_max_chars, spans phrase_max_s seconds, or has
    # been open for phrase_flush_age_s with at least phrase_min_chars. The last
    # rule is what makes translation start mid-sentence.
    phrase_max_chars: int = 160
    phrase_max_s: float = 8.0
    phrase_min_chars: int = 12
    phrase_flush_age_s: float = 1.5


@dataclass
class TtsConfig:
    """Offline text-to-speech (Piper ONNX)."""

    voice_onnx: str = "models/piper/bn_BD-google-medium.onnx"
    voice_config: str = "models/piper/bn_BD-google-medium.onnx.json"
    english_voice_onnx: str = "models/piper/en_US-lessac-medium.onnx"
    english_voice_config: str = "models/piper/en_US-lessac-medium.onnx.json"
    enabled: bool = True
    length_scale: float = 1.0
    cache_dir: str = "data/audio"


@dataclass
class EducationConfig:
    """Lecture understanding, summarisation, quiz and feedback settings."""

    # The education pipeline is deterministic and grounded by construction.
    # No general-purpose LLM is used: on 4-core CPU-only laptops a useful LLM
    # would not leave headroom for live ASR, and extractive grounding removes
    # the fabrication risk entirely. See docs/MODEL_SELECTION.md.
    summary_max_sentences: int = 7
    quiz_default_count: int = 5
    auto_refresh_every_n_finals: int = 8
    min_evidence_chars: int = 25
    # Uploads during a live lecture compete with ASR for the interpreter (the
    # extraction path is pure Python). They are refused unless the teacher opts in.
    allow_upload_while_live: bool = False
    max_documents_per_session: int = 20
    # Store a Bangla side for each uploaded chunk so Bangla queries can reach
    # English slides (measured bn->en top-1 was 0.167 without it). The shipped
    # Marian model is en->bn only, so the corpus is translated at upload time
    # instead of translating the query.
    bilingual_index: bool = True


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    session_join_ttl_min: int = 240
    max_students: int = 40
    ws_queue_size: int = 64
    heartbeat_s: float = 20.0
    # When True, /api/* requires the teacher token for teacher endpoints.
    require_teacher_token: bool = True
    # Informational reachability probe (1.1.1.1:53). Disable for strict offline
    # verification; nothing in the app depends on the result.
    online_probe: bool = True
    # Interface/hotspot discovery is cached for this many seconds: the console
    # polls every 5 s and each probe shells out to ip/nmcli plus a TCP connect.
    network_cache_s: float = 30.0


@dataclass
class StorageConfig:
    data_dir: str = "data"
    db_path: str = "data/guru.db"
    keep_audio: bool = True
    keep_recordings: bool = False  # raw microphone audio is never persisted
    retention_days: int = 90
    # Background retention sweep interval. 0 keeps the startup-only behaviour.
    # A lecture that runs for a whole day should not keep expired sessions until
    # the next restart, which was the recorded limitation.
    retention_interval_s: float = 3600.0


@dataclass
class AudioConfig:
    """Microphone / capture source."""

    device: str = "default"  # ALSA device for arecord
    sample_rate: int = 16000
    channels: int = 1
    input_wav: str | None = None  # if set, replay this file instead of mic


@dataclass
class Settings:
    asr: AsrConfig = field(default_factory=AsrConfig)
    translate: TranslateConfig = field(default_factory=TranslateConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)
    education: EducationConfig = field(default_factory=EducationConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)

    def resolve(self, path: str | None) -> Path | None:
        """Resolve a possibly-relative path against the project root."""
        if not path:
            return None
        p = Path(path).expanduser()
        return p if p.is_absolute() else (PROJECT_ROOT / p)


def _apply_env(section: Any, prefix: str) -> None:
    """Recursively apply GURU_* environment overrides onto a dataclass.

    Documented variable shape: ``GURU_<SECTION>__<KEY>`` (single underscore
    between the root and the section, double before the leaf key), e.g.
    ``GURU_SERVER__PORT=8080``.
    """
    for f in fields(section):
        current = getattr(section, f.name)
        if is_dataclass(current):
            # Descend into nested sections unconditionally: the env var for a
            # nested key (GURU_SERVER__PORT) is derived from the section
            # prefix (GURU_SERVER), which is itself never a variable.
            _apply_env(current, f"{prefix}_{f.name}".upper())
            continue
        raw = os.environ.get(f"{prefix}__{f.name}".upper())
        if raw is None:
            continue
        if isinstance(current, bool):
            value: Any = raw.strip().lower() in {"1", "true", "yes", "on"}
        elif isinstance(current, int) and not isinstance(current, bool):
            value = int(raw)
        elif isinstance(current, float):
            value = float(raw)
        elif current is None and raw.strip().lower() in {"", "none", "null"}:
            value = None
        else:
            value = raw
        setattr(section, f.name, value)


def _apply_dict(section: Any, data: dict[str, Any]) -> None:
    for f in fields(section):
        if f.name not in data:
            continue
        current = getattr(section, f.name)
        if is_dataclass(current) and isinstance(data[f.name], dict):
            _apply_dict(current, data[f.name])
        else:
            setattr(section, f.name, data[f.name])


def load_settings(path: str | Path | None = None) -> Settings:
    """Load settings from TOML (if present) then apply env overrides."""
    settings = Settings()
    cfg_path = Path(path) if path else Path(
        os.environ.get("GURU_CONFIG", PROJECT_ROOT / "config" / "default.toml")
    )
    if cfg_path.is_file():
        with cfg_path.open("rb") as fh:
            _apply_dict(settings, tomllib.load(fh))
    _apply_env(settings, "GURU")
    return settings
