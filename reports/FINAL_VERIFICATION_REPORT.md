# Final verification report

**Date:** 2026-11-02
**Scope:** completion of Phase 6 and Phase 8 on the existing `offline_guru`
repository. No component was rebuilt, no architecture was replaced.

## 1. Acceptance matrix

Status vocabulary: **PASS** = implemented and verified here · **PARTIAL** =
implemented, only partly verified · **BLOCKED** = cannot be verified without an
external requirement · **FAIL** = verified and failed · **NOT STARTED** = nothing
exists.

| Area | Required evidence | Status | Actual evidence |
|---|---|---|---|
| Teacher dashboard | Functional workflow tests | **PASS** | 22-check browser run: create, code, QR, upload, summary, quiz rendered (`reports/results/browser_qa.json`) |
| Student dashboard | Functional workflow tests | **PASS** | real join, title, approved quiz 958 chars, summary 161 chars |
| Browser QA | Actual browser verification | **PASS** | Firefox + geckodriver, 22/22 checks, 10 screenshots reviewed |
| Mobile responsiveness | Viewport/browser evidence | **PASS (emulated)** | 390/360/768/1366 CSS px; `scrollWidth == viewport` in every case |
| Accessibility | Audit evidence | **PARTIAL** | static + DOM verified; **no screen-reader session** (none installed) |
| WebSocket recovery | Automated integration tests | **PASS (tests)** | `test_ws.py`, `test_bus.py`, `after_seq` catch-up; server restart not simulated |
| Regression suite | Actual test output | **PASS** | **211 passed**, exit 0 |
| End-to-end latency | Measured benchmark | **PASS** | ASR p50 1355.6 ms, MT 179.9 ms, e2e p50 1532.0 ms / p95 3897.1 ms |
| CPU/RAM | Actual profiling results | **PASS** | CPU mean 44.8 % (max 82 %), RSS 45→785 MB, peak 878 MB |
| ASR + upload contention | Concurrent real workload | **PASS** | real Whisper + 150-slide PPTX: **−3.6 %** |
| Retention | Background cleanup tests | **PASS** | 7 unit tests + live 200→404 sweep |
| Retrieval | Before/after benchmark | **PASS (partial)** | bn→en 0.167 → **0.667**; en→bn still 0.167; encoder BLOCKED |
| Grading | Evaluation dataset results | **PASS (partial)** | 10 specified cases measured; synonyms still unsolved |
| Simulated load | Load-test results | **PASS** | 1–30 clients, 0 drops, 0 errors |
| Physical hotspot | Real device evidence | **BLOCKED** | no phones / no AP control on this host |
| 30-device capacity | 30 physical devices | **BLOCKED** | application layer only (30 simulated) |
| Thermal stability | Sustained hardware test | **BLOCKED** | no 45-minute real lesson |
| Documentation | Completed and reviewed files | **PASS** | `reports/`, `INSTALLATION_GUIDE.md`, `TEACHER_GUIDE.md`, `STUDENT_GUIDE.md`, README updated |

## 2. Defects found and fixed

| # | Defect | Fix | Regression test |
|---|---|---|---|
| 1 | Expired sessions were only cleaned at startup | background retention loop with configurable interval, cancellable | `tests/test_retention.py` |
| 2 | Bangla queries could not reach English slides (top-1 0.167) | Bangla-side indexing at upload + bilingual scoring | `tests/test_bilingual_retrieval.py` |
| 3 | Bangla inflections and reversed word order broke rubric matching | suffix folding + word-set matching | `tests/test_grading_eval.py` |
| 4 | Arabic `حالة` in Bengali teacher help text | replaced with `অবস্থা` | visual check |
| 5 | No visible keyboard focus, captions silent to screen readers, toasts unannounced | `:focus-visible`, `aria-live`/`role=log`, `role=status` | DOM assertions in browser run |
| 6 | Desktop-only layout below 640 px (overflow risk) | dedicated small-screen CSS pass | 390/360 px overflow checks |

Below the acceptance table, `matched_terms` used to report the English rubric
label even when the Bangla term matched; fixed in `grading.py`.

## 3. Measurements actually produced (not inherited)

| Measurement | Value | Command |
|---|---|---|
| Tests | 211 passed | `.venv/bin/python -m pytest -q` |
| Lint / compile / JS | clean | ruff, py_compile, node --check |
| Offline proof | PASS, zero external connections | `scripts/verify_offline.py --seconds 15` |
| Caption latency (file input) | e2e p50 1.53 s, p95 3.90 s | `scripts/bench_latency.py` |
| CPU / RAM | 44.8 % mean, 878 MB peak | same |
| ASR under upload | −3.6 % | `scripts/bench_asr_upload.py` |
| Load 1–30 | 0 drops, fan-out p95 18.2 ms | `scripts/loadtest.py` |
| bn→en retrieval | 0.167 → 0.667 top-1 | `scripts/eval_retrieval.py` |
| Browser checks | 22 passed | `scripts/browser_qa.py` |

## 4. Conclusion A — SOFTWARE COMPLETION

Phase 6 is **functionally complete and verified in a real browser** for every
control that does not need a microphone or a phone: session creation, join, upload,
summary, quiz, approval, student rendering, viewport layout, accessibility
semantics. The two API-level gaps are audio playback in a browser and transcript
editing in a browser (both covered by tests, neither clicked through).

Phase 8 is **complete except for three items**: the neural encoder comparison
(BLOCKED on download), a live lecture with documents enabled (not run), and any
physical/thermal verification (BLOCKED on hardware). Everything else — regression,
latency, profiling, contention, retention, retrieval, grading, load, browser,
mobile, accessibility, documentation — has been executed on this machine and the
raw artefacts are in `reports/results/`.

## 5. Conclusion B — REAL CLASSROOM READINESS

**Nothing about physical classroom behaviour has been verified.** No phone has
connected to this hotspot, no radio carried 30 clients, no human teacher spoke
into this microphone for a lesson, and no 45-minute thermal soak was run. The
30-client and latency numbers are application-layer/loopback or file-replay
measurements and must not be read as classroom capacity.

The readiness question is therefore open, and it is open for one reason: the
missing evidence needs hardware, not code.

## 6. Exact remaining human actions

1. Run `reports/PHYSICAL_DEVICE_TEST_PLAN.md` stage A (2 phones), then B (10), then
   C (30), recording the template each time.
2. Record one real teacher for 10 minutes and score WER:
   `.venv/bin/python scripts/eval_asr.py --wav <rec> --text <reference.txt>`.
3. Run one 45-minute lesson with documents enabled, watching `/api/health`
   (`cpu_percent`, `load1`, `e2e_ms`) every minute.
4. Score a small set of real student answers to validate the `needs_review` band.
5. Decide the green button contrast question (white on `#2ea043` ≈ 3.7:1) — it
   needs a colour decision, not code.
6. Retry the multilingual encoder on a fast link if the retrieval numbers matter
   more than the extra ~470 MB / ~1 s per query.

## 7. Reproduce this verification

```bash
cd offline_guru
.venv/bin/python -m pytest -q
.venv/bin/ruff check --no-cache guru scripts tests
.venv/bin/python -m py_compile guru/*.py guru/education/*.py guru/server/*.py scripts/*.py
node --check guru/server/static/teacher.js && node --check guru/server/static/student.js
.venv/bin/python scripts/verify_offline.py --seconds 15

export GURU_STORAGE__DB_PATH=/tmp/bench/guru.db GURU_STORAGE__DATA_DIR=/tmp/bench
.venv/bin/python scripts/bench_latency.py
.venv/bin/python scripts/bench_asr_upload.py --repeats 3
.venv/bin/python scripts/eval_retrieval.py

GURU_STORAGE__DB_PATH=/tmp/load/guru.db .venv/bin/python -m uvicorn guru.server.app:app --port 8010 &
.venv/bin/python scripts/loadtest.py --base http://127.0.0.1:8010 --clients 30

GURU_STORAGE__DB_PATH=/tmp/qa/guru.db .venv/bin/python -m uvicorn guru.server.app:app --port 8011 &
.venv/bin/python scripts/browser_qa.py --server http://127.0.0.1:8011
```
