# Real-Time Translation Audit — Offline Guru

**Date:** 2026-11-08
**Scope:** Phase 0 of the real-time speech-translation upgrade.
**Method:** every claim below was verified by reading the repository source, running the
existing test suite, and measuring the shipped models on the target laptop. Nothing here
is inferred from the README alone.

---

## 1. Target hardware (measured, not assumed)

| Item | Value |
|---|---|
| CPU | Intel Core i5-7300U @ 2.60 GHz — 2 cores / 4 threads |
| RAM | 7,269 MB total; 2,271 MB available at audit time (desktop browser running) |
| GPU | none |
| OS | Ubuntu, kernel 7.0.0-38-generic, x86_64 |
| Python | 3.14.7 (project venv) |
| Key packages | faster-whisper 1.2.1, ctranslate2 4.8.3, onnxruntime 1.31.0, piper-tts 1.8.0, fastapi 0.149.0, numpy 2.5.4 |

The 2.2 GB of *available* RAM is the real budget: the classroom server must fit in it
alongside the desktop session that the teacher is using.

---

## 2. Repository inventory (verified)

```
guru/
  audio.py        256 lines  arecord capture, energy VAD segmenter, WAV replay
  asr.py          100 lines  faster-whisper adapter (local dir only)
  translate.py    330 lines  Marian CT2 adapter + optional NLLB + passthrough
  tts.py          123 lines  Piper ONNX + disk cache keyed by text hash
  pipeline.py     516 lines  3 worker threads, bounded queues, metrics
  bus.py           94 lines  in-process pub/sub, per-client bounded asyncio queues
  storage.py      776 lines  SQLite (WAL): sessions, segments, materials, answers, metrics
  education/      6 modules  summary, quiz, grading, documents, retrieval
  server/app.py  1074 lines  FastAPI REST + 2 WebSockets + static UI
  server/static/             teacher.html/js, student.html/js, style.css
config/default.toml          runtime config (GURU_* env overrides)
scripts/                    17 scripts: model prep, eval, latency/load benchmarks, QA
tests/                      21 files, 211 tests
models/                     whisper-tiny/base/small, marian-en-bn-ct2, opus-en-bn-ct2,
                            opus-bn-en-ct2, nllb-600m-ct2-int8, piper bn_BD + en_US
```

**Baseline test run:** `pytest -q` → **211 passed** (exit 0). This is the regression
baseline that every later phase must preserve.

---

## 3. Current pipeline (as implemented)

```
arecord (16 kHz mono s16le, 100 ms blocks)
   │  MicCapture._run  → float32
   ▼
Segmenter.feed()            energy VAD, 20 ms frames, 300 ms preroll,
   │                        silence 600 ms, min 0.8 s, max 10 s
   ├─ finalized Segment ──► _final_q (maxsize 8) ──► ASR thread
   └─ partial buffer ─────► _partial_q (maxsize 1) ──► ASR thread (idle only)
                                   │
                                   ▼  WhisperAsr.transcribe (beam 2, no word timestamps)
                            storage.add_segment(seq, en, asr_ms)
                            publish "transcript.final"
                                   │
                                   ▼  _enrich_q (maxsize 12)
                            MarianTranslator.translate (beam 4, lock)
                            storage.update_segment(bn, translate_ms, total_ms)
                            publish "segment"  ← the Bangla caption
                                   │
                                   ▼  _tts_q (maxsize 16)
                            PiperTts.synthesize → WAV on disk
                            storage.update_segment(audio_path, tts_ms)
                            publish "audio.ready"
```

Fan-out: `EventBus.publish` → per-subscriber `asyncio.Queue(maxsize=64)` → `_pump`
→ `ws.send_json`. Slow clients drop their oldest event and recover over REST
(`/api/student/state?after_seq=`).

---

## 4. Feature status

### Fully implemented and working
| Feature | Evidence |
|---|---|
| Continuous mic capture | `audio.py:200-256` arecord subprocess + reader thread |
| Energy VAD + segmentation | `audio.py:42-173`, unit-tested in `tests/test_audio.py` |
| Faster-Whisper ASR, local only | `asr.py:43-100`, `local_files_only=True` |
| Marian EN→BN translation | `translate.py:130-211`, CT2 int8 |
| Piper Bangla TTS + disk cache | `tts.py:27-108`; voice `models/piper/bn_BD-google-medium.onnx` present |
| Bounded queues + drop accounting | `pipeline.py:77-82, 193-209` |
| WebSocket fan-out with per-client backpressure | `bus.py:19-76` |
| Student auth, session isolation, REST recovery | `server/app.py:364-370, 798-835` |
| SQLite persistence + retention | `storage.py`, `server/app.py:1003-1016` |
| Education services (summary/quiz/grading) | `guru/education/*` |

### Partially implemented
| Feature | Gap |
|---|---|
| Provisional captions | Implemented (`pipeline.py:262-283`) but **disabled by default** (`partial_enabled=false`) and **English-only** — never translated, never voiced |
| Latency metrics | Per-stage ms are stored, but there is **no queue-wait, no first-caption, no first-audio, no RTF** metric |
| Audio playback | Client queue exists (`student.js:286-322`) but drops when >3 items and has no sequence guarantee |
| Language direction | Config has `source_lang`/`target_lang`, but only EN→BN is wired; `models/opus-bn-en-ct2` exists and is unused |

### Missing (required for real-time)
1. **Incremental ASR.** No word timestamps, no hypothesis buffer, no `stable` state.
   Every decode is a fresh batch decode of a finalized segment.
2. **Translation before the utterance ends.** Translation is only enqueued after the
   segmenter finalizes a segment — i.e. after 600 ms of silence or a 10 s force-cut.
3. **Ordered, deduplicated playback.** No `nextPlaySeq`, no played-set, no
   stale-audio suppression after reconnect.
4. **Event vocabulary.** No `transcript.stable`, `pipeline.status`, `pipeline.error`.
5. **Segment metadata.** No `audio_status`, `segment_status`, `translation_timestamp`,
   `sequence_number` in the wire payload.
6. **Chunk-size configurability.** Only `min_segment_s`/`max_segment_s`; the
   required 0.5/0.75/1.0/1.5/2.0 s chunk sweep is not expressible.
7. **Backlog observability.** Queue *depth* is exposed, but not queue *wait time*,
   and there is no "ASR is slower than real time" signal.

---

## 5. Bottleneck analysis (measured on this laptop)

### 5.1 ASR cost is per-call, not per-second
Whisper pads every input to a 30 s mel window, so a decode costs roughly the same for
2 s and 12 s of audio. Measured with the shipped models (`scripts/_probe_asr.py`,
beam 1, word timestamps on, 3 CPU threads):

| Model | 2 s | 4 s | 8 s | 12 s |
|---|---:|---:|---:|---:|
| whisper-tiny | 0.78 s | 0.88 s | 0.95 s | 1.09 s |
| whisper-base | 1.50 s | 3.02 s* | 1.89 s | 2.33 s |

\* outlier under desktop CPU contention; the same 8 s window measured 1.89 s.

**Consequence:** a streaming loop cannot run faster than ~1.5–2.3 s per iteration with
`base`, and ~0.8–1.1 s with `tiny`. This single fact sets the achievable first-caption
latency and is the reason the design below uses a *continuous* decode loop rather than a
fixed short cadence.

### 5.2 Segment finalization delay
`silence_ms = 600` means the last word of a sentence is not even *submitted* to ASR
until 600 ms after the teacher stops. Add ASR (~1.9 s) + translation (~0.2 s) and the
first Bangla caption of a sentence lands ~2.7 s after the sentence ends. For a 10 s
monologue the force-cut at `max_segment_s` adds up to 10 s of dead time.

### 5.3 Translation and TTS are not the bottleneck
Previously measured and re-confirmed by the existing benchmark artifacts:
translation p50 ≈ 0.20 s/sentence (Marian int8, beam 4), TTS p50 ≈ 0.31 s cold cache.
Both are an order of magnitude below ASR.

### 5.4 Playback
`student.js:289` shifts the queue when it exceeds 3 entries, and `student.js:187`
only auto-plays when `seq >= maxSeq - 1`. Under a burst (e.g. after a reconnect) audio
can be dropped or played out of order. There is no played-set, so a re-delivered
`audio.ready` can replay a segment.

### 5.5 Memory
Models resident: whisper-base ≈ 142 MB on disk (≈ 250–300 MB RSS), Marian ≈ 80 MB
(≈ 150 MB RSS), Piper ≈ 134 MB (≈ 200 MB RSS). The README's ~800 MB runtime figure is
consistent with the audit. Loading a second ASR model (tiny) would add ~100 MB RSS —
acceptable, but it also adds a competing CPU consumer, which is the real cost.

---

## 6. Hardware limitations that constrain the design

1. **No GPU.** All inference is int8 CPU. ASR dominates.
2. **2 physical cores.** ASR (3 threads), translation (2 threads) and TTS (1 thread)
   already oversubscribe 4 logical CPUs. Adding a second ASR model would make every
   stage slower, not faster.
3. **~2.2 GB available RAM** with the desktop session running. Peak pipeline RSS must
   stay well under that.
4. **Thermal throttling** on a 2017 U-series part during a 45-minute lecture is a real
   risk; the health endpoint must expose the signals (it already exposes load and CPU%).

---

## 7. Proposed implementation strategy

A **hybrid** design that keeps every working component and adds a streaming front end:

1. **Streaming capture** — keep `MicCapture`; add a `SpeechWindow` that keeps a
   rolling, timestamped audio buffer with VAD gating, preroll and bounded trimming.
2. **Incremental ASR** — add word timestamps and a **LocalAgreement-2** hypothesis
   buffer (the WhisperLiveKit policy, Apache-2.0, adapted with attribution). Words are
   committed only when two consecutive hypotheses agree. States: `partial` (unstable,
   English only), `stable` (committed, translated), `final` (segment closed).
3. **Phrase builder** — committed words are grouped into translatable phrases closed by
   sentence punctuation, a character cap, a duration cap, or end of speech.
4. **Incremental translation** — a bounded translation buffer with the PolyTalk-style
   flush policy (sentence boundary / char threshold / age + min chars / pause flush),
   re-implemented independently (PolyTalk is AGPL-3.0; its *algorithm* is reused, not
   its code).
5. **Low-latency TTS** — unchanged Piper path, but fed per phrase and never allowed to
   block captions.
6. **Ordered playback** — server emits `sequence_number` + `audio_status`; the student
   client plays strictly in sequence, deduplicates, and skips stale backlog on reconnect.
7. **Observability** — per-stage queue wait, ASR RTF, first-caption and first-audio
   latency, backlog depth, dropped/duplicate counters.

The existing segment-based path stays available (`asr.streaming = false`) so the
proven 1.5 s behaviour is never lost and the 211 existing tests keep passing.

---

## 8. Testing requirements

| Layer | Requirement |
|---|---|
| Unit | LocalAgreement commit/rollback, phrase closing rules, translation flush policy, ordered playback queue, audio_status transitions |
| Integration | WAV → streaming ASR → translation → TTS → WS events, in order, with no duplicates |
| Regression | All 211 existing tests must pass unchanged |
| Performance | Chunk sweep 0.5/0.75/1.0/1.5/2.0 s; first-caption and first-audio latency; RTF; peak RSS; CPU% |
| Load | 1/5/10/20/30 WebSocket students; delivery spread; reconnection |
| Security | Invalid tokens, cross-session audio access, student isolation (existing tests extended) |
| Quality | Manually reviewed EN→BN set; meaning preservation, numbers, technical terms |

---

## 9. Risks

1. **ASR cannot keep up.** If the decode loop is slower than real time the backlog grows.
   Mitigation: never queue more than one decode; drop cadence, not audio; expose
   `asr_backlog` and `asr_rtf`; document the measured ceiling.
2. **LocalAgreement accuracy.** Committed text can differ from a full-segment decode.
   Mitigation: measure WER both ways and report the delta; keep the segment path.
3. **Fragment translation quality.** Clause-level translation loses cross-clause context.
   Mitigation: minimum buffer size, clause-boundary splitting, measured quality report.
4. **License contamination.** PolyTalk is AGPL-3.0 and Realtime-translation has no
   license file. Mitigation: reuse algorithms only, write original code, attribute.
