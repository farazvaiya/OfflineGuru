# Implementation Roadmap

**Date**: 2026-11-14
**Status**: Phase 0 complete (docs written). Phases 1–3 authorized for
implementation; Phases 4–9 wait for prerequisite verification.

## Completed (this session)

- [x] Phase 0: repository audit (engineering_team harness, guru telemetry
      surface, storage schema, bus, adapter)
- [x] `PHYSICAL_TEST_MONITORING_DESIGN.md`
- [x] `TELEMETRY_INVENTORY.md`
- [x] `MONITORING_GAP_ANALYSIS.md`
- [x] this roadmap

## Phase plan (dependency order)

### Phase 1 — Physical test session management
- `engineering_team/physical/sessions.py`: session entity, lifecycle state
  machine (CREATED→RUNNING→PAUSED→RUNNING→STOPPED→REPORTED), conditions,
  human feedback, evidence export.
- CLI: `python3 -m engineering_team physical-test {start,status,pause,resume,
  stop,feedback,report,list}` wired into `__main__.py`.
- Persistence: `state/physical_tests.db` (migration 1 = sessions + conditions
  + feedback).
- Tests: lifecycle, invalid transitions, persistence across restart,
  concurrent ops.

### Phase 2 — Live telemetry collectors
- `engineering_team/physical/collectors.py`: 8 collectors (capture, ASR,
  translation, TTS, playback, network, system, aggregator).
- All read-only against `/api/health` + app DB; bounded queues; independent
  failure; missing → `unavailable` never 0.
- 1 Hz poll; measured overhead (Phase 2 acceptance).

### Phase 3 — Evidence DB + dashboard
- Migrations 2..N: telemetry_events, metric_snapshots, error_events,
  student_events, validation_results, agent_findings.
- `engineering_team/physical/dashboard.py`: local HTTP dashboard (separate
  port), live from telemetry DB; JSON + Markdown export; fails safe.
- Tests: migration up/down, event insert/query, dashboard data honesty.

### Phase 4 — Automated validation (prereq: Phase 3)
- Reference-based WER/CER/chrF against provided references; configurable
  thresholds; outcomes PASS/FAIL/INCONCLUSIVE/NOT_EXECUTED.

### Phase 5 — Agent runtime (prereq: Phase 4 evidence format)
- Real subprocess adapter: configured executable, availability check, argv
  list, timeout, bounded capture, run ids, resource limits. No recursion.

### Phase 6 — Track A/B in isolation (prereq: Phase 5 + confirmed finding)
- Snapshot + digest + single-writer lock (no git in this workspace).

### Phase 7 — Benchmarking + independent evaluation
- Equivalent conditions; evaluator inspects artifacts; rejects claims.

### Phase 8 — Human approval + gated integration
- Decision commands; checkpoint before integration; rollback path.

### Phase 9 — Historical comparison + improvement planning
- Session comparison only when conditions match; next-test plan.

## Risks / blockers

| Risk | Mitigation |
|---|---|
| No git → no branch isolation | snapshot + digest + single-writer lock; report limitation |
| No microphone/models in some envs | collectors poll only; scenarios marked NOT_EXECUTED |
| App not running during dev | collectors tolerate connection refused; dashboard shows UNAVAILABLE |
| Agent runtime may be absent | availability check; `pending_external` never reported as executed |
| Scope creep into guru/ | hard rule: zero production modifications; PR-free |

## Human approval gates (explicit)

- Any production code change (guru/, config default changes).
- Any external dependency adoption.
- Any physical test requiring people/devices.
- Any integration/merge/rollback.

## Definition of done per phase

1. Tests written and passing (unit + integration where applicable).
2. Full existing suite green (no weakened/deleted tests).
3. Phase completion report under `engineering_team/reports/`.
4. Limitations recorded in `PHYSICAL_TEST_MONITORING_LIMITATIONS.md`.
