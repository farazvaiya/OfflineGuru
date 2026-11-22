# Phased plan and status

Status is deliberately blunt: ✅ verified by a command in this repository,
◐ partially done with the gap named, ⬜ not done.

## Phase 1 — Feasibility ✅

| Task | Status | Evidence |
|---|---|---|
| Inspect hardware, OS, disk, mic | ✅ | i5-7300U, 4 threads, 7.1 GB RAM, no GPU; mic enumerated and captured |
| Compare offline ASR candidates | ✅ | tiny/base/small measured for WER, latency, RTF, RAM (`docs/MODEL_SELECTION.md`) |
| Compare translation candidates | ✅ | Marian vs NLLB measured on 12 educational sentences |
| Compare TTS candidates | ✅ | Piper bn voice measured; espeak/MMS/browser rejected with reasons |
| Licenses checked | ✅ | `LICENSES.md`; project relicensed GPL-3.0 for Piper |
| Latency/RAM budget set | ✅ | ~800 MB steady state; 1–3 s target derived from measured RTF |

## Phase 2 — Minimum viable pipeline ✅

| Task | Status | Evidence |
|---|---|---|
| Microphone capture | ✅ | `arecord` 3 s capture verified; `MicCapture` start/stop on the live server |
| Streaming segmentation | ✅ | `Segmenter` + 7 unit tests (pause cut, force cut, noise rejection, flush) |
| Offline ASR | ✅ | faster-whisper base, local files only |
| EN→BN translation | ✅ | Marian CT2 int8, ~0.22 s/sentence |
| FastAPI + WebSocket delivery | ✅ | live server test: 8 segments delivered, e2e p50 1.66 s |
| Student browser UI with Bangla captions | ✅ | page served, JS syntax checked, live events received by WebSocket client |
| Bangla audio on student device | ✅ | WAV generated, cached, served 200 `audio/wav` (4.1 s, 22.05 kHz) |
| Works with Internet disconnected | ✅ | `scripts/verify_offline.py` socket guard: zero external connections |

## Phase 3 — Educational intelligence ✅ (scoped, deterministic)

| Task | Status | Evidence |
|---|---|---|
| Transcript storage | ✅ | SQLite schema + tests |
| Summary generation | ✅ | grounded extractive + translated; 7 sentences / 8 terms on the demo |
| Teacher-reviewed quiz | ✅ | draft → approved gate enforced by API and tested |
| Evidence links | ✅ | every question stores segment id, timestamp, quote |
| Student answers + grading | ✅ | MCQ exact, short-answer rubric with `needs_review` band |
| Explanations and review pointers | ✅ | Bangla feedback with lecture timestamp |
| Topic-level progress | ✅ | only after 3 attempts (`study_recommendations`) |
| LLM-based generation | ⬜ **deliberately not done** | would compete with live ASR and risks fabrication; see `docs/MODEL_SELECTION.md` |

## Phase 4 — Classroom reliability ✅

| Task | Status | Evidence |
|---|---|---|
| Session management | ✅ | create/start/pause/resume/stop/end, teacher token |
| Multiple students | ✅ | 30 clients, 10/10 segments each, 0 drops |
| Reconnection | ✅ | client backoff + REST state merge; WS keepalive |
| Bounded queues / backpressure | ✅ | queue caps, drop-oldest, drop counters, overload test |
| Privacy controls | ✅ | per-student scoping tests, purge, no raw audio stored |
| Graceful model failure | ✅ | health reports `loaded:false`; 503 on start; captions survive TTS failure |
| Student cap | ✅ | `max_students` enforced (429) and tested |

## Phase 5 — Evaluation ◐

| Task | Status | Gap |
|---|---|---|
| Repeatable ASR/translation/E2E measurements | ✅ | `docs/EVALUATION.md`, artifacts in `docs/results/` |
| 1/5/10/20/30 client capacity | ✅ | application layer on one laptop |
| Real classroom recording + accents | ⬜ | no consenting class; WER is on synthetic clean speech |
| 30 physical phones over the hotspot radio | ⬜ | needs on-site test; script is provided |
| Human review of summaries/quizzes | ◐ | manual inspection during development, not a blinded study |
| 45-minute thermal soak | ⬜ | health metrics are in place to measure it |

## Next steps, in order of value

1. **Field trial** in one classroom: record 20 minutes with consent, compute WER,
   run the 30-phone load test over the hotspot, and log thermals.
2. **Translation quality**: evaluate a fine-tuned Marian or a distilled NLLB
   variant that fits the 3 s budget; keep the evidence-linked pipeline unchanged.
3. **Teacher correction loop**: let corrected transcript segments feed back into
   a per-session glossary that biases ASR hotwords and MT terminology.
4. **Summary/quiz review study**: have teachers rate drafts for usefulness and
   factual fidelity against the transcript.
