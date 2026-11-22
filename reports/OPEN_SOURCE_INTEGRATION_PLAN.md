# Open-Source Integration Plan — Real-Time Translation

**Date:** 2026-11-08
**Depends on:** `REALTIME_TRANSLATION_AUDIT.md`, `OPEN_SOURCE_PROJECT_COMPARISON.md`

---

## 1. What will be reused

| # | Component | Origin | License | How it is used |
|---|---|---|---|---|
| 1 | LocalAgreement-2 hypothesis buffer | WhisperLiveKit `local_agreement/online_asr.py` | Apache-2.0 | **Adapted** into `guru/realtime.py` (`LocalAgreement`), attribution header retained |
| 2 | Commit-then-trim window management | WhisperLiveKit `online_asr.py:339-353` | Apache-2.0 | **Adapted** into `SpeechWindow.trim_to()` |
| 3 | Prompt-from-committed-text | WhisperLiveKit `online_asr.py:188-210` | Apache-2.0 | **Adapted** into `LocalAgreement.prompt()` (200-char suffix) |
| 4 | Window overlap + pause flush + leading-silence preroll | PolyTalk `stt/app/main.py` | AGPL-3.0 | **Algorithm only, independently implemented** — no code copied |
| 5 | Cumulative-transcript delta extraction | PolyTalk `pipeline_service.py:216-269` | AGPL-3.0 | **Algorithm only, independently implemented** |
| 6 | Translation flush policy (sentence / chars / age / pause) | PolyTalk `pipeline_service.py:1018-1053` | AGPL-3.0 | **Algorithm only, independently implemented** |
| 7 | Per-stage latency instrumentation (queue wait, inference, emit delay) | PolyTalk `stt/app/main.py:330-352` | AGPL-3.0 | **Algorithm only, independently implemented** |

**License discipline.** Apache-2.0 code may be adapted into a GPL-3.0-or-later project
with attribution and a copy of the notice. AGPL-3.0 code may **not** be copied without
relicensing the whole project, so PolyTalk contributes *ideas* only; the implementation
in `guru/realtime.py` is written from scratch against the same requirements. This is
recorded in `LICENSES.md` and in the module docstring.

---

## 2. Why each was selected

- **LocalAgreement over SimulStreaming.** SimulStreaming needs PyTorch, alignment heads
  and a GPU-class budget. LocalAgreement needs only word timestamps from faster-whisper,
  which costs ~2% extra decode time (measured: 1.83 s vs 1.79 s on an 8 s window).
- **Commit-then-trim over fixed windows.** Re-decoding a growing buffer is the
  Realtime-translation failure mode: cost grows without bound and text duplicates.
  Trimming at the commit point keeps the window bounded.
- **PolyTalk's flush policy over "translate every chunk".** Translating 0.5 s fragments
  produces unusable Bangla. Flushing on a sentence boundary, or after a bounded
  character/age threshold, keeps clauses intact while still emitting during speech.
- **Marian over NLLB.** Measured 0.20 s vs 2.1 s per sentence on this CPU. NLLB remains
  selectable for quality-critical, latency-tolerant use.

---

## 3. Where it is integrated

```
guru/realtime.py            NEW  LocalAgreement, SpeechWindow, PhraseBuilder,
                                 TranslationBuffer, StageTimer, RealtimeStats
guru/pipeline.py            EDIT ClassroomPipeline gains a streaming mode that uses
                                 the above; segment mode unchanged
guru/asr.py                 EDIT transcribe_words() returning word timestamps
guru/config.py              EDIT AsrConfig streaming knobs, TranslateConfig flush knobs
config/default.toml         EDIT matching defaults
guru/storage.py             EDIT audio_status / segment_status / translation_at columns
guru/server/app.py          EDIT pipeline.status + pipeline.error events, health metrics
guru/server/static/student.js  EDIT ordered playback queue, dedupe, stale suppression
scripts/bench_realtime.py   NEW  chunk sweep + latency/RAM/CPU benchmark
scripts/eval_translation_quality.py NEW  EN→BN quality harness
tests/test_realtime.py      NEW  unit + integration tests
```

---

## 4. What must be adapted

| Item | Adaptation |
|---|---|
| LocalAgreement | Operates on Offline Guru's `Word` dataclass; comparison is case/punctuation-normalised; adds `flush_all()` for end-of-speech; no dependency on `timed_objects` |
| Window trimming | Trims to `commit_time - context_s` instead of WLK's sentence-boundary chunking, because we do not ship a sentence tokenizer |
| Prompt | 200-character suffix of committed text, passed as `initial_prompt`; disabled if it would exceed the model's context |
| Flush policy | Adds a clause-boundary splitter (comma / semicolon / conjunction) so a mid-sentence flush does not cut a clause in half |
| Metrics | Written to the existing `metrics` table, not to logs |

---

## 5. What must be rewritten (not reused)

- **Ordered playback.** No repository solves multi-student ordered playback. Written
  from scratch on both server (sequence numbers, `audio_status`) and client
  (`nextPlaySeq`, played-set, stale suppression).
- **Streaming orchestration.** Offline Guru's thread/queue model is kept; the streaming
  loop is new.
- **Bangla TTS integration.** Already Offline Guru's; only the feeding changes.

---

## 6. What remains unchanged

- `MicCapture` (arecord), `Segmenter` (used in segment mode and for VAD gating)
- `WhisperAsr` model loading, `MarianTranslator`, `NllbTranslator`, `PiperTts`
- `EventBus`, `_pump`, student/teacher auth, session isolation
- SQLite schema (additive columns only), retention, education services
- All 211 existing tests and the segment-mode behaviour

---

## 7. Expected CPU / RAM impact

| Item | Expected | Basis |
|---|---|---|
| Extra RSS | +10–20 MB | One hypothesis buffer + rolling window (≤ 15 s float32 = 960 KB) |
| Extra CPU | ~0 (same model, same thread count) | Streaming replaces segment decodes, it does not add a second model |
| Decode calls | ~1 per 1.8 s of speech (base) | Measured per-call cost |
| Peak RSS | ≤ 900 MB | Existing ~800 MB + buffers |

No second ASR model is loaded. The 30 students still share one model instance.

---

## 8. Expected latency impact

| Metric | Before | Expected after |
|---|---|---|
| First English partial caption | not emitted (disabled) | ~1.0–2.0 s after speech starts |
| First Bangla caption | ~2.7 s after sentence end | ~2.0–2.6 s after the phrase is spoken |
| First Bangla audio | ~3.0 s after sentence end | ~2.3–3.0 s after the phrase is spoken |
| Caption during a 10 s monologue | only at the 10 s force-cut | every ~2 s |

These are targets derived from the measured per-call ASR cost; the benchmark phase
replaces them with measured values.

---

## 9. Known risks

1. **ASR throughput.** base ≈ 1.8 s per decode. If a decode is slower than the audio it
   covers, the loop degrades to "one decode behind". Mitigation: single in-flight decode,
   cadence drop (never audio drop), `asr_rtf` and `asr_backlog` exposed.
2. **Commit accuracy.** LocalAgreement can commit a word that a full decode would have
   written differently. Mitigation: measure WER for both paths; keep segment mode.
3. **Fragment translation.** Clause-level translation loses cross-clause context.
   Mitigation: minimum buffer, clause-boundary split, quality report.
4. **Client autoplay policy.** Browsers block autoplay until a gesture; the existing
   "tap once" flow is preserved.
5. **Regression risk.** The streaming path is opt-in via `asr.streaming`; the default is
   chosen only after the benchmark, and the segment path stays tested.
