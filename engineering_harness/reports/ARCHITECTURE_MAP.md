# Architecture Map — Offline Guru

Derived from source, not from the project's own plan documents. Line references are to the tree as audited on 2026-11-10.

---

## 1. Process topology

```
                       ┌──────────────────────────────────────────────┐
   arecord (child)     │  uvicorn / FastAPI event loop (1 thread)     │
   ALSA 16 kHz mono ──►│  guru/server/app.py                          │
   stdout pipe         │   REST: sessions, join, documents, quizzes   │
   stderr pipe  ──✗     │   WS:   /ws/teacher, /ws/student             │
                       │   lifespan: models, retention, education loop│
                       └───────────────┬──────────────────────────────┘
                                       │ submit_audio()  (capture thread)
                       ┌───────────────▼──────────────────────────────┐
                       │ ClassroomPipeline  (guru/pipeline.py)         │
                       │                                               │
                       │  capture thread ──► SpeechWindow / Segmenter  │
                       │        │                    │                 │
                       │        │            _asr_wake Event           │
                       │        ▼                    ▼                 │
                       │   [guru-asr]  ── faster-whisper (CTranslate2) │
                       │        │  committed phrases                  │
                       │        ▼  _enrich_q (max 12)                  │
                       │   [guru-enrich] ── Marian/NLLB translate      │
                       │        │  caption published (bus)            │
                       │        ▼  _tts_q (max 16)                     │
                       │   [guru-tts] ── Piper (onnxruntime)           │
                       └───────────────┬──────────────────────────────┘
                                       │ loop.call_soon_threadsafe(bus.publish)
                       ┌───────────────▼──────────────────────────────┐
                       │ EventBus (guru/bus.py)                        │
                       │  per-subscriber asyncio.Queue(maxsize=64)     │
                       └───────────────┬──────────────────────────────┘
                                       │ JSON over WebSocket
                       ┌───────────────▼──────────────────────────────┐
                       │ teacher.js / student.js                       │
                       └──────────────────────────────────────────────┘
                       SQLite (guru/storage.py) ◄── all stages write
```

Threads: exactly three worker threads per session (`guru-asr`, `guru-enrich`, `guru-tts`) plus the arecord reader thread and the asyncio loop. No thread-per-segment. This design is sound; the defects are inside the hand-offs.

## 2. Module responsibilities

| Module | Owns | Never does |
|---|---|---|
| `guru/audio.py` | `Segmenter` (segment mode), `WavSource`, `MicCapture` | no model calls, no storage |
| `guru/realtime.py` | `SpeechWindow`, `LocalAgreement`, `PhraseBuilder`, `LatencyRecorder` | no I/O, no threads |
| `guru/asr.py` | faster-whisper wrapper, word timestamps clamped to audio duration | no VAD, no network |
| `guru/translate.py` | Marian/NLLB/Opus/passthrough backends, number preservation | no audio |
| `guru/tts.py` | Piper synthesis + disk cache | no text ordering |
| `guru/pipeline.py` | stage threading, queues, phrase lifecycle, metrics | no HTTP |
| `guru/bus.py` | fan-out with bounded per-subscriber queues | no persistence |
| `guru/storage.py` | SQLite schema, segments, metrics, retention | no model calls |
| `guru/server/app.py` | REST/WS contracts, auth, model lifecycle | no audio processing |

## 3. Two capture modes (the most important structural fact)

`config/default.toml` sets `asr.streaming = true`. There are therefore **two different pipelines** sharing one server:

| | Segment mode (`streaming=false`) | Streaming mode (`streaming=true`, default) |
|---|---|---|
| VAD | `audio.Segmenter` emits a `Segment` after a pause | `realtime.SpeechWindow` keeps a rolling buffer |
| ASR | one `transcribe()` per segment | `transcribe_words()` on a growing window |
| Word selection | whole segment | `LocalAgreement` (confidence 0.9) |
| Utterance end | segment emitted | `_ended` flag → final decode → `_flush_stream_tail()` |
| Storage row | one per VAD segment | one per *phrase* |

The two paths have **wildly different accuracy on the same audio** — 5.64 % vs 54.1 % WER (F-03). Every historical accuracy number in `reports/` was produced with `eval_asr.py`, which drives the **segment** path directly, while the classroom runs the **streaming** path. That gap is why the regression was never seen by the benchmark suite.

## 4. Where speech can be lost — stage by stage

| # | Stage | Failure mode | Status |
|---|---|---|---|
| 1 | arecord → stdout | stderr pipe fills, arecord blocks | **F-01 (P0)** |
| 2 | arecord → reader thread | child exits, thread returns, no restart | **F-02 (P0)** |
| 3 | `submit_audio` | drops everything when `status != running` or paused | by design |
| 4 | `Segmenter.feed` | sub-frame remainder discarded, clock drifts 4 % | **F-05 (P1)** |
| 5 | `Segmenter._finish` | pre-roll re-seed overlaps the previous segment | **F-06 (P2)** |
| 6 | `SpeechWindow.feed` | correct: carries `_pending` between calls | ok |
| 7 | `SpeechWindow` cap | drops oldest beyond 15 s (intended, bounded) | ok |
| 8 | end-of-speech flush | `reset()` deletes audio that arrived during the decode | **F-03 (P0)** |
| 9 | `_worth_final_decode` | < 0.35 s un-decoded → tail never transcribed | **F-04 (P1)** |
| 10 | `LocalAgreement.commit` | holds words until stable; flushes at utterance end | ok |
| 11 | `PhraseBuilder` | closes on punctuation / 160 chars / 8 s / 1.5 s age | ok |
| 12 | `_enrich_q` (12) | overflow drops translation, caption still shows English | bounded, counted |
| 13 | `_tts_q` (16) | overflow drops audio only | bounded, counted |
| 14 | bus subscriber queue (64) | overflow drops that student's event | bounded, counted |
| 15 | WebSocket send | slow client, ping/pong keepalive | ok |

Stages 1, 2, 4, 8 and 9 are real defects. 12–14 are deliberate, observable backpressure with counters.

## 5. Data model (SQLite)

`sessions`, `segments` (id, seq, start_s, end_s, en, bn, confidence, asr_ms, translate_ms, tts_ms, total_ms, audio_path, audio_status, segment_status), `metrics`, `documents`, `document_chunks`, `quizzes`, `summaries`, `participants`. Sequences are per-session and monotonic (`storage.next_seq`), which is what makes client-side ordering and REST recovery safe. `bench_latency.py` purges its session at the end; load tests leave sessions behind until retention runs.

## 6. Contracts that must not break

| Contract | Where | Guarded by |
|---|---|---|
| `POST /api/teacher/session` → `{session, teacher_token, join_code, join_url}` | `app.py` | `tests/test_api.py` |
| `GET /api/teacher/session/{id}` with `X-Teacher-Token` | `app.py` | `tests/test_security.py` |
| `POST /api/student/join` with join code | `app.py` | `tests/test_api.py` |
| WS `/ws/student?token=` events: `classroom`, `segment`, `transcript.*`, `audio.ready`, `ping` | `bus.py`/`app.py` | `tests/test_ws.py` |
| `GET /api/student/state?after_seq=` (recovery) | `app.py` | `tests/test_api_edge.py` |
| `GET /api/student/audio/{segment_id}?token=` | `app.py` | `tests/test_ws.py` |
| `GET /api/health` shape | `app.py` | `tests/test_api.py` |
| Segment payload keeps both legacy and `*_time`/`translated_text` fields | `pipeline._segment_payload` | `tests/test_pipeline.py` |

Phase 2 changes must leave all of these intact; `checks.p2_contract_preserved` verifies the modules still run.

## 7. Concurrency hazards found by reading, then confirmed by experiment

1. `_flush_stream_tail()` mutates the window while the capture thread appends to it (both threads touch `_buf`). Python's GIL makes each op atomic but not the sequence, so a flush can interleave with a feed. The audio-loss bug (F-03) is deterministic, not merely racy — the flush always sees `_buf` as of its own execution.
2. `_ended` is a one-shot flag with no timestamp: the consumer cannot tell *which* speech-end it is handling, nor how much audio has arrived since.
3. `MicCapture._run` holds no timeout on `read()`, so `stop()` must rely on `proc.terminate()`; a device that ignores SIGTERM leaves the thread alive until process exit.
4. `_latency` is a plain dict mutated from three threads without a lock (`deque.append` is atomic under CPython, so this is a latent smell rather than a live bug).

## 8. Latency path (annotated with measured values)

```
speech ──0.6 s min audio──► first decode ──1.1 s──► commit needs agreement
      ──► phrase closes (≥12 chars or punctuation or 1.5 s age)
      ──► translate ──p50 93 ms / p95 449 ms──► caption published (bus)
      ──► TTS ──p50 59 ms / p95 556 ms──► audio.ready
```
Measured first caption: **4.40 s**; e2e caption p50 2.17 s / p95 4.73 s on a 45 s file (the difference is which phrase is measured). The dominant terms are "wait for enough audio to decode", "wait for agreement", and "wait for the phrase to close" — not model speed.

## 9. Summary

The architecture is a reasonable three-thread pipeline with bounded queues and a clean event contract. Its weak point is the boundary between the rolling audio window and the end-of-speech flush, where ownership of buffered audio is ambiguous — and that ambiguity is exactly what produces the classroom symptom the brief describes.
