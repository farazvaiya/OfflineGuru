# Phase 8 verification — optimisation and final verification

**Objective:** record, item by item, what Phase 8 required and what was actually
executed after this mission's changes.

| Phase 8 item | Status | Evidence |
|---|---|---|
| Full regression suite | **PASS** | 211 tests, exit 0 (`REGRESSION_TEST_REPORT.md`) |
| Ruff | **PASS** | All checks passed |
| Python compile | **PASS** | `py_compile` OK |
| JS syntax | **PASS** | `node --check` both files |
| Offline verification, zero external connections | **PASS** | `verify_offline.py --seconds 15` |
| Security regression tests | **PASS** | `tests/test_security.py` |
| Session isolation tests | **PASS** | `tests/test_api.py`, `tests/test_documents.py` |
| Upload validation tests | **PASS** | `tests/test_documents.py` |
| WebSocket tests | **PASS** | `tests/test_ws.py`, `tests/test_bus.py` |
| End-to-end latency with stage breakdown | **PASS** | `reports/results/latency.json` |
| p50 / p95 / p99 / min / max / n | **PASS** | `scripts/bench_latency.py` |
| CPU and RAM profiling | **PASS** | CPU mean 44.8 %, peak 82 %; RSS 45→785 MB (peak 878) |
| ASR under document upload (real ASR) | **PASS** | −3.6 % (no measurable slowdown), `reports/results/asr_upload_contention.json` |
| Background retention | **PASS** | live: session 200 → 404 after a 2 s sweep |
| Retrieval quality before/after | **PASS (partial)** | bn→en 0.167 → 0.667; en→bn still 0.167 |
| Neural encoder comparison | **BLOCKED** | encoder not on disk; download stalled earlier at 28/470 MB |
| Grading evaluation | **PASS (with limitation)** | 10 cases specified and measured; synonyms unsolved |
| Simulated load 1–30 clients | **PASS** | 0 drops, 0 errors, fan-out p95 18.2 ms at 30 |
| Live lecture with documents enabled | **NOT TESTED** | requires a lecture + upload concurrently |
| Browser/visual QA | **PASS** | 22 browser checks, screenshots reviewed |
| Mobile viewports | **PASS (emulated)** | 390/360/768/1366 CSS px |
| Accessibility audit | **PASS (static + DOM)** | no assistive-technology session |
| Physical hotspot, 30 devices, thermal soak | **BLOCKED** | no hardware |
| Final reports and guides | **PASS** | this `reports/` directory + the three guides |

## Changes made to reach this state

| Area | Change |
|---|---|
| Retention | `_retention_loop` background worker, `retention_interval_s` config, 7 tests |
| Retrieval | Bangla-side chunk indexing at upload (`text_bn`), bilingual scoring, migration, 7 tests |
| Grading | Bengali inflection folding, word-order-tolerant rubric matching, matched-term language, 10 tests |
| Frontend | 640 px responsive pass, focus-visible ring, live regions, reduced-motion, Arabic typo fix |
| Tooling | `scripts/bench_latency.py`, `scripts/bench_asr_upload.py`, `scripts/browser_qa.py`, bilingual rows in `scripts/eval_retrieval.py` |

## Deliberately not done

- No LLM or agent layer was added: the product is deterministic by design and the
  hardware cannot host a useful model alongside live ASR.
- No new network dependency, no cloud API, no telemetry.
- No relaxation of auth, validation, rate limits or the teacher approval gate.
- No test was deleted, skipped or weakened.
