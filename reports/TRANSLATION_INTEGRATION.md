# Translation Integration Plan — Offline Guru

**Date:** 2026-11-02
**Evidence base:** `reports/TRANSLATION_MODEL_COMPARISON.md`, `TRANSLATION_LATENCY_BENCHMARK.md`, `TRANSLATION_QUALITY_EVALUATION.md`
**Implementation status:** **NOT YET IMPLEMENTED.** This document is the plan. Phase 4 integration and Phase 5 contention test are pending.
**Production impact:** Production translation behavior is unchanged until this plan is implemented and regression-tested.

---

## 1. Decision summary

The benchmark evidence supports integrating **Candidate C (OPUS-MT CTranslate2 INT8)** as a **new bidirectional backend**, but **not** as a drop-in replacement for the existing production Marian for EN→BN. The existing model is adequate for EN→BN and uses less memory.

### What the evidence says

| Criterion | A (production Marian) | C (OPUS-MT CT2 INT8) | Decision |
|---|---|---|---|
| EN→BN latency p99 | 1583 ms ✅ | 1152 ms ✅ | Both pass; A uses less memory |
| EN→BN quality | Good ✅ | Good ✅ | Equivalent |
| EN→BN memory | 243 MB ✅ | 593 MB ⚠️ | A is lighter |
| BN→EN available | **No** ❌ | Yes ✅ | Only C provides BN→EN |
| BN→EN latency p99 | n/a | 869 ms ✅ | C passes |
| BN→EN quality | Garbage ❌

 |

---

## Phase 4 — Integration

The benchmark evidence supports adding the OPUS-MT CTranslate2 INT8 stack as a new optional bidirectional backend. The existing production Marian translator stays as the default EN→BN backend and as the fallback. BN→EN is gated behind configuration because its quality on scientific classroom Bangla is only PARTIAL.

Key constraints carried from the mission: preserve existing API contracts, keep translation asynchronous, avoid duplicate model instances, prevent translation from blocking ASR, add bounded queues with backpressure, preserve WebSocket recovery, maintain TTS behavior, support both directions, configuration-based model selection, keep the previous backend as fallback.

### 1.1 New module: `guru/translate_opus.py`

Add a new file `guru/translate_opus.py` containing:

- `OpusCt2Translator` — a thread-safe wrapper around one CTranslate2 INT8 OPUS-MT model (EN→BN or BN→EN), matching the production `MarianTranslator` API: `load()`, `translate(text) -> Translation`, `translate_batch(texts, beam_size=None) -> list[Translation]`. Reuses the `Translation` dataclass and the `normalize_source` / `_check_numbers` helpers from `guru/translate.py` (imported, not duplicated).
- `OpusBidirectionalTranslator` — a thin coordinator that holds two `OpusCt2Translator` instances (one EN→BN, one BN→EN), selects the right direction from the source text's script (ASCII → EN→BN; Bengali → BN→EN), and falls back to the EN→BN direction if the script is ambiguous. This is the object the pipeline would use when bidirectional mode is enabled.
- `OpusFallbackTranslator` — wraps an `OpusBidirectionalTranslator` and a `MarianTranslator`; when the OPUS model fails or is unavailable, falls back to the Marian for EN→BN and returns a skipped translation with a clear reason for BN→EN.

Thread safety: each `OpusCt2Translator` owns one `ctranslate2.Translator` and one `threading.Lock`, exactly like `MarianTranslator`. The bidirectional coordinator does not add a second model lock — it delegates to the directional translators.

Model loading once at startup: the coordinator is constructed with both directional configs and calls `load()` on each once. The `available` property checks both dirs.

No automatic download: the constructor accepts `local_ct2_dir_en_to_bn` and `local_ct2_dir_bn_to_en` `Path` objects and raises `ModelUnavailable` (reusing `guru.asr.ModelUnavailable` for consistency) if either is missing. The preparation script `scripts/prepare_opus_models.py` is the only way to get the files.

Configurable CPU thread count and beam size: the coordinator reads `intra_threads`, `inter_threads`, and `beam_size` from a new `OpusTranslateConfig` dataclass (see 1.3).

Input/output validation: same `max_input_chars` gating and empty/no-letter skipping as production, in the directional translator.

Timeout and error handling: each `translate` call is guarded by a `try/except` that records `skipped=True, reason="translation error: ..."` and never raises — same contract as production. A per-call timeout is implemented with `threading.Timer` or `concurrent.futures` with `timeout=` on the CTranslate2 call; CTranslate2's `Translator.translate_batch` is synchronous, so the timeout wraps the call, not the whole thread. If a timeout fires, the translation is marked skipped with reason "translation timed out".

### 1.2 Configuration: `guru/config.py`

Add a new `OpusTranslateConfig` dataclass:

```
@dataclass
class OpusTranslateConfig:
    enabled: bool = False                 # default OFF — bidirectional is opt-in
    en_to_bn_model_dir: str = "models/opus-en-bn-ct2"
    bn_to_en_model_dir: str = "models/opus-bn-en-ct2"
    compute_type: str = "int8"
    intra_threads: int = 2
    inter_threads: int = 1
    beam_size: int = 4
    max_input_chars: int = 1200
    timeout_s: float = 30.0
    fallback_to_marian: bool = True       # when True, EN→BN falls back to Marian on failure
```

Add `opus: OpusTranslateConfig = field(default_factory=OpusTranslateConfig)` to `Settings`.

Environment variable: `GURU_OPUS__ENABLED=true` etc., following the existing `GURU_SECTION__KEY` convention in `_apply_env`.

The existing `TranslateConfig.backend` field (`"marian"` / `"nllb"`) is untouched. The OPUS backend is a separate, opt-in path. This keeps the existing `build_translator(settings) -> MarianTranslator|NllbTranslator|PassthroughTranslator` contract intact.

### 1.3 `build_translator` extension

Extend `guru/translate.py`'s `build_translator(settings)` to return an `OpusBidirectionalTranslator` when `settings.opus.enabled` is True **and** both OPUS dirs are available. The return type is still `Any` (as it is now), so this is backward-compatible.

When `opus.enabled` is True but one of the OPUS dirs is missing, `build_translator` falls back to the existing Marian translator and logs a warning — this is the "keep the previous backend available as fallback" requirement.

When `opus.enabled` is False (default), behavior is unchanged.

### 1.4 Pipeline integration

The pipeline (`guru/pipeline.py`) currently calls `self.translator.translate(seg["en"])` inside `_enrich()`. The segment's English text is always the source. To support BN→EN, the pipeline needs to know the desired translation direction.

**Design decision:** The translation direction is a session-level setting. `CreateSessionBody` already has `source_lang` and `target_lang`. The pipeline reads `settings.translate.target_language` (currently `"bn"`) and, when the OPUS bidirectional translator is active, uses it to choose direction:

- `source_lang=en, target_lang=bn` → EN→BN (same as today)
- `source_lang=bn, target_lang=en` → BN→EN (new)
- When both OPUS models are loaded and `opus.enabled`, the bidirectional translator auto-detects direction from the source script, so the pipeline does not need to change its call site for the common case (English lecture → Bangla captions).

The pipeline's `_enrich()` call site changes only if we want explicit direction control. The minimal change: the `OpusBidirectionalTranslator.translate(text)` auto-detects direction from the script, so the existing `self.translator.translate(seg["en"])` keeps working. For BN→EN sessions, the ASR would produce Bengali text (a different ASR model / language setting), and the translator would detect Bengali script and translate BN→EN. This is the cleanest integration.

**ASR implication:** BN→EN mode requires the ASR to transcribe Bengali, not English. That means `settings.asr.language` must be `"bn"` and a Bengali ASR model must be available. The current project ships Whisper base with `language="en"`. Adding Bengali ASR is out of scope for this translation mission, but the integration must not break when `target_lang=en` is set without a Bengali ASR — the pipeline should gracefully skip translation with a clear reason.

### 1.5 Queues and backpressure

The existing pipeline already has bounded queues:

- `_final_q` (maxsize=8): ASR output → enrichment. Drops oldest on full.
- `_enrich_q` (maxsize=12): enrichment jobs. Drops on full, counts `enrichment_queue_drops`.
- `_tts_q` (maxsize=16): TTS jobs. Drops on full, counts `tts_queue_drops`.

These are sufficient for the translation backend change. No new queues are needed. The requirement "add bounded queues and clear backpressure behavior" is satisfied by the existing design — the OPUS translator is drop-in compatible with the `translate()` contract.

The one change: when the OPUS translator is slower than the Marian (which it is not for EN→BN, but could be for BN→EN under load), the enrichment queue may fill faster. The existing backpressure behavior (drop oldest segment, log warning, count metric) handles this. No change needed.

**Preventing translation from blocking ASR capture:** The pipeline's three-thread design (ASR / enrichment / TTS) already ensures this. The ASR thread only writes to `_final_q` and never waits for translation. The OPUS translator runs on the enrichment thread. As long as the OPUS translator's `translate()` call returns within the enrichment loop's timeout (0.1 s polling), ASR capture is unaffected. The benchmark shows OPUS CT2 INT8 p99 of 1152 ms (EN→BN) and 869 ms (BN→EN) for a single sentence — this is the per-sentence latency, and the enrichment thread processes one segment at a time, so a 1.2 s translation does not block the ASR thread. **No change needed.**

### 1.6 WebSocket recovery

The existing WebSocket recovery is handled by the REST `/api/student/state?after_seq=` endpoint: a reconnecting student fetches segments after their last seen sequence. This is independent of the translation backend. **No change needed.**

### 1.7 TTS behavior

TTS consumes the translated Bangla text (or English text in BN→EN mode) and synthesizes audio with Piper. The Piper voice is `bn_BD-google-medium` (Bangla). For BN→EN mode, the TTS would synthesize English text with a Bangla voice, which would sound wrong. **This is a known limitation.** The integration should:

- Keep TTS disabled (or use a passthrough) when `target_lang=en` and the TTS voice is Bangla. The `PiperTts.available` check and the `settings.tts.enabled` flag already gate this. The pipeline's `_enrich()` already checks `if self.settings.tts.enabled and translation.text:` before enqueueing TTS. For BN→EN mode, the teacher can set `tts.enabled=false` via config, or the integration can auto-disable TTS when the target language does not match the voice language.
- Document this limitation clearly: BN→EN captions are text-only until an English TTS voice is added.

**No code change required for TTS** beyond documenting the limitation. The existing `settings.tts.enabled` flag is the mechanism.

### 1.8 Fallback behavior

When `opus.enabled=true` but the OPUS translator fails (load error, timeout, or per-sentence error):

- EN→BN: if `opus.fallback_to_marian=true` and the Marian model is available, fall back to Marian for that sentence. Mark the translation with `reason="opus failed, fell back to marian"`.
- BN→EN: there is no fallback (Marian is EN→BN only). Mark as skipped with `reason="bn-to-en unavailable"`.

The fallback is per-sentence, not per-session, so a single OPUS failure does not disable the whole session.

### 1.9 Configuration-based model selection summary

| Config | Default | Effect |
|---|---|---|
| `GURU_TRANSLATE__BACKEND=marian` | ✅ (current) | Existing Marian EN→BN (unchanged) |
| `GURU_TRANSLATE__BACKEND=nllb` | — | Existing NLLB EN→BN (unchanged) |
| `GURU_OPUS__ENABLED=true` | false | Enable OPUS-MT bidirectional (EN→BN + BN→EN) |
| `GURU_OPUS__FALLBACK_TO_MARIAN=true` | true | EN→BN falls back to Marian on OPUS failure |
| `GURU_TRANSLATE__ENABLED=false` | — | Passthrough (unchanged) |

The existing `GURU_TRANSLATE__BACKEND` still selects the production backend. `GURU_OPUS__ENABLED` is an orthogonal switch that adds the OPUS bidirectional translator on top. When both are active, the OPUS translator is the primary and Marian is the fallback for EN→BN.

This is a conservative integration: the existing production path is untouched when `opus.enabled=false` (the default). The OPUS path is opt-in and falls back to production on failure.

---

## 2. Files to create or modify

| File | Action | Notes |
|---|---|---|
| `guru/translate_opus.py` | **Create** | `OpusCt2Translator`, `OpusBidirectionalTranslator`, `OpusFallbackTranslator` |
| `guru/config.py` | **Modify** | Add `OpusTranslateConfig`, add `opus` field to `Settings` |
| `guru/translate.py` | **Modify** | Extend `build_translator` to return OPUS translator when enabled |
| `scripts/prepare_opus_models.py` | **Create** (done) | Downloads and converts OPUS-MT models |
| `guru/opus_benchmark.py` | **Create** (done) | Isolated benchmark adapters (not used at runtime) |
| `tests/test_translate_opus.py` | **Create** | Unit tests for the new translator (mirrors `test_translate.py`) |
| `reports/TRANSLATION_MODEL_COMPARISON.md` | **Create** (done) | Model comparison |
| `reports/TRANSLATION_LATENCY_BENCHMARK.md` | **Create** (done) | Latency benchmark |
| `reports/TRANSLATION_QUALITY_EVALUATION.md` | **Create** (done) | Quality evaluation |
| `reports/TRANSLATION_INTEGRATION.md` | **Create** (done) | This file |

---

## 3. Regression tests to run before enabling

Before enabling `opus.enabled` in production:

1. **Existing test suite:** `pytest tests/` — must pass with `opus.enabled=false` (the default). This confirms the existing pipeline is untouched.
2. **New OPUS tests:** `pytest tests/test_translate_opus.py` — unit tests for `OpusCt2Translator` with a mock CTranslate2 translator (same pattern as `tests/test_translate.py` which mocks `_translator`).
3. **Offline verification:** `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python scripts/verify_offline.py` — confirms the runtime still works with zero network access when OPUS models are on disk.
4. **Pipeline smoke test:** Run the existing `scripts/bench_latency.py` with the current production config (Marian EN→BN) and confirm the latency numbers are unchanged from the baseline in `reports/PERFORMANCE_REPORT.md`. This confirms the integration did not regress the production path.
5. **Bidirectional smoke test:** With `opus.enabled=true`, run a short WAV file through the pipeline in EN→BN mode and confirm captions are produced. Then run in BN→EN mode (with a Bengali ASR — if available) and confirm English captions are produced. If Bengali ASR is not available, the BN→EN path should gracefully skip translation.

---

## 4. Phase 4 test classification

| Test | Result |
|---|---|
| Existing test suite passes with opus.enabled=false | NOT TESTED (pending implementation) |
| New OPUS unit tests | NOT TESTED (pending implementation) |
| Offline verification with OPUS models on disk | NOT TESTED (pending implementation) |
| Pipeline smoke test EN→BN with opus.enabled=true | NOT TESTED (pending implementation) |
| Pipeline smoke test BN→EN with opus.enabled=true | NOT TESTED (pending implementation, and requires Bengali ASR) |
| Backward compatibility: opus.enabled=false unchanged | NOT TESTED (pending implementation) |

---

## 5. Known limitations at integration time

1. **BN→EN quality is PARTIAL.** Scientific terms (প্রকাশসংশ্লেষণ, ক্লোরোফিল, ফটোলাইসিস) are not translated correctly by `Helsinki-NLP/opus-mt-bn-en`. BN→EN should be opt-in and used with caution.
2. **No English TTS voice for BN→EN audio.** BN→EN captions are text-only unless an English Piper voice is added. The current voice is `bn_BD-google-medium`.
3. **No Bengali ASR.** BN→EN requires the teacher to speak Bengali and the ASR to transcribe it. The current ASR is English-only (`language=en`). BN→EN is not usable end-to-end until a Bengali ASR model is added.
4. **chrF/COMET not measured.** Quality claims for BN→EN are based on a 10-sentence human sample, not automatic metrics.
5. **30-phone claim not substantiated.** Phase 5 contention test is pending. The benchmark numbers are single-sentence, single-threaded measurements, not multi-client pipeline behavior.

---

## 6. Implementation order

1. Create `guru/translate_opus.py` with `OpusCt2Translator` (reuses `Translation` and helpers from `guru/translate.py`).
2. Add `OpusTranslateConfig` and `opus` field to `guru/config.py`.
3. Extend `build_translator` in `guru/translate.py` to return the OPUS translator when enabled.
4. Add `tests/test_translate_opus.py` with mocked translator tests.
5. Run `pytest tests/` to confirm no regression.
6. Run offline verification with OPUS models on disk.
7. Run pipeline smoke test with `opus.enabled=true` in EN→BN mode.
8. Document BN→EN limitations (no Bengali ASR, no English TTS, weak science-term quality).
9. **Hold BN→EN for a separate human-review gate** before enabling it by default.

---

## 7. Phase 5 — Real-time contention test (planned, not executed)

The contention test runs ASR + translation + TTS simultaneously with 1, 5, 10, and 30 simulated clients. The existing `scripts/loadtest.py` is the starting point — it currently simulates 30 students joining a session and receiving WebSocket events. The contention test extends it to measure:

- End-to-end latency (speech end → caption delivered to student) per client count.
- Translation queue wait time (time from segment finalized to translation started).
- CPU and memory usage per client count.
- Queue drops (asr_queue_drops, enrichment_queue_drops, tts_queue_drops) per client count.
- Student fan-out delay (time from caption published to WebSocket delivered to each student).

The test uses `scripts/loadtest.py` extended with translate-specific metrics and a configurable translation backend (production Marian vs OPUS CT2 INT8). The 30-client simulation is done over WebSocket with a headless client, not physical phones, so the report will clearly label it as a simulation and not claim real 30-phone support.

**Status: NOT STARTED.**

---

*End of integration plan. Implementation pending.*
