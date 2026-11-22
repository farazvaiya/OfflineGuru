# Phase 1 — Repository Audit

**Date:** 2026-11-10
**Project:** Offline Guru (`/home/faraz/project/offline_guru`)
**Auditor:** research-agent, verified by qa-agent
**Method:** full read of `guru/`, `tests/`, `scripts/`, `config/`, `docs/`, plus execution of every benchmark the project ships.

---

## 1. Version control state

| Item | Value |
|---|---|
| Git repository | **absent** — `git status` returns "not a git repository" at the project root |
| Working tree | no VCS metadata, so no branch, no commit, no diff baseline |
| Implication | Git-based checkpoints and "best verified commit" are **not available**; the harness uses content-addressed source snapshots instead (see `checkpoints/`) |
| Untracked user data | `data/guru.db`, `data/audio/`, `data/demo/`, `models/` (~3.5 GB of model weights) |

`git init` was deliberately **not** run: it would create a repository containing a 45 MB WAV and rewrite the user's workflow. Recorded as an approved-scope decision, not a silent one.

## 2. What the system is

A single-host, offline classroom translation server: teacher speaks (or replays a WAV), a FastAPI server transcribes English, translates to Bangla, synthesises Bangla speech, and fans out captions plus audio to up to 40 student WebSocket clients joined by a code.

## 3. Code inventory

| Area | File | Lines |
|---|---|---|
| HTTP/WS server | `guru/server/app.py` | 1092 |
| Pipeline orchestration | `guru/pipeline.py` | 930 |
| Persistence | `guru/storage.py` | 798 |
| Streaming primitives | `guru/realtime.py` | 530 |
| Opus benchmark support | `guru/opus_benchmark.py` | 419 |
| Education (quiz/summary/grading) | `guru/education/*.py` | 1182 |
| Translation | `guru/translate.py` | 330 |
| Config | `guru/config.py` | 257 |
| Audio capture/VAD | `guru/audio.py` | 256 |
| Documents | `guru/education/documents.py` | 248 |
| Networking/hotspot | `guru/net.py` | 164 |
| ASR | `guru/asr.py` | 156 |
| TTS | `guru/tts.py` | 123 |
| Log redaction | `guru/logsafe.py` | 123 |
| Event bus | `guru/bus.py` | 94 |
| **Total Python** | | **6761** |

Front end: vanilla JS/HTML/CSS (`teacher.html`, `teacher.js`, `student.html`, `student.js`, `style.css`) with no build step.

Tests: 18 files under `tests/`, **239 tests**, all passing at audit time.

## 4. Runtime dependencies (declared)

`fastapi 0.149.0`, `uvicorn 0.54.0`, `websockets 17.3`, `faster-whisper 1.2.1`, `ctranslate2 4.8.3`, `transformers 5.20.2`, `numpy 2.5.4`, `soundfile 0.14.0`, `psutil 7.2.2`, `piper-tts 1.8.0`, `onnxruntime 1.31.0`, `pypdf 6.21.4`. Python 3.14.7 in `.venv`.

No runtime dependency reaches the network. `scripts/verify_offline.py` exists to prove that; it is covered by the suite.

## 5. Model assets present (offline)

| Model | Size | Role |
|---|---|---|
| `models/whisper-base` | 142 MB | default ASR, int8 |
| `models/whisper-small` | 464 MB | alternative ASR |
| `models/whisper-tiny` | 75 MB | low-CPU fallback |
| `models/marian-en-bn-ct2` | 80 MB | default translator |
| `models/nllb-600m-ct2-int8` | 618 MB | higher-quality translator |
| `models/opus-en-bn-ct2`, `opus-bn-en-ct2` | 659 / 376 MB | experimental MT |
| `models/piper/bn_BD-google-medium.onnx` | 134 MB | Bangla TTS |

## 6. Benchmarks shipped by the project

| Script | Measures | Writes |
|---|---|---|
| `scripts/eval_asr.py` | WER vs a reference transcript | JSON |
| `scripts/bench_latency.py` | full pipeline e2e / per-stage / CPU / RSS | JSON |
| `scripts/bench_realtime.py` | first caption, first audio, chunk-size sweep | JSON |
| `scripts/loadtest.py` | N loopback WebSocket students | JSON |
| `scripts/eval_translation_quality.py` | chrF vs author-written reference | JSON + MD |
| `scripts/bench_asr_upload.py` | ASR under document-extraction contention | JSON |
| `scripts/browser_qa.py` | headless UI flows | JSON |

This is unusually good benchmark coverage for the maturity of the code, and it let Phase 1 reproduce baselines quickly. The weakness is **coupling between scripts and the assumptions they encode**, which is exactly how F-07 (wrong reference) survived review.

## 7. Reproduced baselines (2026-11-10, this host)

| Metric | Value | Artifact |
|---|---|---|
| Test suite | 239 passed, 0 failed | `baselines/pytest_baseline.json` |
| In-domain WER (segment mode) | **5.64 %** | `baselines/asr_wer_baseline.json` |
| In-domain WER (streaming, shipped default) | **54.1 %** | `artifacts/pipeline_baseline_streaming.json` |
| Segments / speech (segment mode) | 37 / 144.4 s | `artifacts/pipeline_baseline_segment.json` |
| Segments / speech (streaming) | 55 / 73.9 s | `artifacts/pipeline_baseline_streaming.json` |
| e2e caption p50 / p95 (45 s file) | 2169 / 4725 ms | `baselines/latency_baseline.json` |
| First caption (streaming) | 4402 ms | `artifacts/pipeline_baseline_streaming.json` |
| Translation chrF | 0.5923 (26 sentences) | `baselines/translation_quality_baseline.json` |
| Translation latency p50 / max | 135.3 / 401.1 ms | same |
| TTS RTF | 0.11–0.13 | this report §8 |
| 30-student loopback | 15/15 segments, 0 drops, 0 errors | `baselines/loadtest_30_baseline.json` |
| Peak RSS during streaming | 367 MB (ASR only), ~800 MB (all models) | artifacts |
| CPU mean during streaming | 60.4 % | artifacts |

The historical figures in `reports/` (2.95 s p50, 16.02 s p95, 818.5 MB peak, 80.6 % CPU) are broadly consistent with what was reproduced here; the load-test p95 (15.3 s) reproduces the historical tail almost exactly.

## 8. Discrepancies between the brief and the tree

| Brief claim | What the tree shows |
|---|---|
| "Science lecture WER 5.6 %" | Reproduced exactly, **but only in segment mode**; the default (`asr.streaming=true`) scores 54.1 % |
| "Emma Watson WER 95.6 %" | **Artifact.** The Emma audio was scored against the photosynthesis transcript (F-07) |
| "Zero reported drops at 30 clients" | Reproduced: 0 socket errors, 0 bus drops, 0 queue drops |
| "CPU mean 80.6 %" | Reproduced 60.4 % for ASR-only streaming; 80 %+ when translation+TTS run concurrently |
| "Approximately 7.1 GB RAM" | 7.27 GB total but only **2.3 GB available** during audit; 1.7 GB swap already in use |

## 9. Security posture (initial read)

Good: teacher token required, join codes single-use-ish, log redaction module, path-traversal protections in document handling, rate limits on join, retention sweeper, no network inference.

Gaps that the security agent will re-check in Phase 2:

* `MicCapture` launches `arecord` with no `start_new_session`, so a stopped session can orphan the child.
* Capture failure is invisible to `/api/health` (F-02) — an availability problem, not a disclosure.
* No secret files are read by harness code; `.env` and `.autobpid/memory.db` are excluded by policy.

## 10. Harness readiness

| Capability | Status |
|---|---|
| Persistent state machine | implemented, atomic writes, self-tested |
| Evidence stamping (source digest) | implemented; stale artifacts are rejected by gates |
| Checkpoints / rollback | implemented as gzip source snapshots + sha256 manifest |
| Git checkpoints | **BLOCKED** — no repository |
| Retry limits (5 attempts, 3 cycles) | implemented in state, enforced by runner |
| Gate evaluation | 19 criteria across 3 phases, all backed by artifacts |

## 11. Conclusion

The application is real, coherent and better tested than most code at this stage, and its own benchmarks mostly reproduce. The defects are not in the model quality: they are in the seams — an unread stderr pipe, a window reset that races speech resumption, a segmenter that drops the remainder of each chunk, and an evaluation script that allowed a mismatched reference to produce a headline number. Phase 1 findings F-01…F-09 record each with evidence.
