# Physical Test Monitoring — Implementation Report

**Date**: 2026-11-14
**Status**: Phases 0–9 implemented and tested; all physical scenarios NOT_EXECUTED (no hardware); production code untouched.

## What was built (all under `engineering_team/`)

| Phase | Module | Tests |
|---|---|---|
| 0 | PHYSICAL_TEST_MONITORING_DESIGN.md, TELEMETRY_INVENTORY.md, MONITORING_GAP_ANALYSIS.md, IMPLEMENTATION_ROADMAP.md | doc review |
| 1 | `physical/sessions.py` — lifecycle state machine, conditions, feedback, events; CLI `physical-test {start,list,status,pause,resume,stop,report,feedback,condition,export}` | 7 tests |
| 2 | `physical/collectors.py` — 8 collectors, bounded loop, independent failure, missing≠zero | 7 tests |
| 3 | `physical/telemetry.py` + `physical/dashboard.py` — migration-1 DB, loopback dashboard, JSON/HTML honesty rules | 5 tests |
| 4 | `physical/validator.py` — WER/CER/chrF + 7 checks; outcomes PASS/FAIL/INCONCLUSIVE/NOT_EXECUTED | 4 tests |
| 5 | `harness/agent_runtime.py` — real subprocess runtime, recursive-Claude refusal, path containment, RLIMIT, timeout, bounded capture | 7 tests |
| 6 | `physical/isolation.py` — snapshot + digest + flock single-writer + mutation detection | 3 tests |
| 7 | `physical/benchmark.py` — provenance, fiction refusal, equivalent-conditions gate, deltas | 3 tests |
| 8 | `physical/approval.py` — human decision table, gated integration, rollback checkpoint | 1 test (multi-assert) |
| 9 | `physical/history.py` — material-condition comparability, regression detection, gap-driven next-test plan | 3 tests |

**Total**: `tests/test_physical_monitoring.py` — 40 tests, all passing.

## Verified live behaviour

- CLI lifecycle end-to-end: start → condition → feedback → pause → resume →
  stop → report → export (evidence JSON on disk); illegal transition refused.
- Collectors against a stub `/api/health`: all 8 ok; missing metrics stay
  missing; unreachable app → error status; capture-error inference works.
- Dashboard serves live telemetry over loopback; `tts_cache_hits=None` renders
  as unavailable, not 0; page states delivery ≠ audible playback.
- Validator: all four outcomes exercised; WER/CER/chrF verified against
  hand-computed values.
- Agent runtime: real execution, nonzero exit, timeout kill, path-escape
  refusal, recursive-Claude refusal, honest `pending_external` when no command.
- Isolation: Track A/B independent, mutation detected, second writer refused.
- Benchmarks: missing raw artifact refused; equivalence gate rejects
  different data or config.
- Approval gate: integration impossible without APPROVE_*; apply=True never
  touches the live repo; rollback restores the candidate; REJECT_BOTH re-locks.
- History: materially different sessions → INVALID_CONDITIONS, no claim.

## Not done / limitations (full list in PHYSICAL_TEST_MONITORING_LIMITATIONS.md)

- Zero physical scenarios executed (no mic, no devices, no target hardware here).
- No end-to-end run against the live app (collectors verified against a stub;
  app not started during this session).
- Production telemetry gaps (capture liveness, tail-decode counter, TTS cache,
  WS reconnects) remain app-side limitations — monitoring labels them, does not
  fake them, and recommends (not implements) counters.
- R-1 thread race (feed/rebase) documented as separate follow-up, unfixed.

## Preservation checks

- `guru/`, `config/`, `engineering_harness/`: no modifications.
- Existing suite: 272 tests green before this work; re-run below after it.
- No commits/merges (not a git repo); no integration; F-01/F-03/F-04 records
  unchanged by this system.

## How to run

```bash
.venv/bin/python -m engineering_team physical-test start --expected-students 30
.venv/bin/python -m engineering_team physical-test status
.venv/bin/python -m engineering_team physical-test feedback "heard clearly" --category playback
.venv/bin/python -m engineering_team physical-test export
.venv/bin/python -m pytest tests/test_physical_monitoring.py -q
```
