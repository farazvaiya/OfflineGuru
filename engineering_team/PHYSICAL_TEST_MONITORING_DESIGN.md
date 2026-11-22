# Physical Test Monitoring Design

**Date**: 2026-11-14
**Phase**: 0 — architecture before implementation
**Scope**: all new work goes in `engineering_team/`. `engineering_harness/` and `guru/` are NOT modified.

---

## 1. Goal

A working system (not a dashboard mockup) that lets a human operator run
physical classroom tests of Offline Guru while AI agents continuously collect
evidence, monitor health, diagnose failures, develop Track A/B candidates,
and recommend improvements — with the human as the only approval gate.

## 2. Architecture

```
┌────────────────────────────────────────────────────────────┐
│ Offline Guru app (guru/) — UNCHANGED                        │
│  /api/health  /api/teacher/...  EventBus  storage.metrics   │
└───────────────┬────────────────────────────────────────────┘
                │ read-only HTTP polling (bounded, async)
                ▼
┌────────────────────────────────────────────────────────────┐
│ engineering_team/ monitoring (NEW)                          │
│  session_controller  →  collectors (8)  →  telemetry DB     │
│       │                     │                    │          │
│       ▼                     ▼                    ▼          │
│  CLI (physical-test)   validator          agent runtime     │
│       │                (PASS/FAIL/        (subprocess       │
│       ▼                 INCONCLUSIVE/      w/ timeout,      │
│  dashboard (local)     NOT_EXECUTED)       bounded output)  │
└────────────────────────────────────────────────────────────┘
```

## 3. Integration contract (read-only)

The monitoring system NEVER writes into the app's DB or pipeline. It polls:

- `GET /api/health` → pipeline health, models, resources (CPU/RAM/disk/load),
  network, bus subscribers, metric summaries, warnings
- `GET /api/teacher/session/{id}` → session state, segments
- `GET /api/teacher/session/{id}/segments?after_seq=N` → incremental transcript
- Student WS events → observed via `/api/health` bus stats + segment queries
  (delivery ≠ audible playback; that distinction is mandatory in evidence)

Why read-only: production rule 3.1 — monitoring must not block or alter real
audio processing, and no new model copies may be loaded.

## 4. Storage

New SQLite DB: `engineering_team/state/physical_tests.db`, versioned
migrations following the existing `harness/db.py` convention (SCHEMA_VERSION,
numbered MIGRATIONS, WAL, RLock). Tables:

- `physical_sessions` — lifecycle, conditions, human observations
- `test_conditions` — per-session environment metadata
- `telemetry_events` — collector events (bounded payloads)
- `metric_snapshots` — periodic /api/health samples
- `error_events` — failures/warnings with source component
- `student_events` — connect/disconnect/reconnect/ack (NOT playback proof)
- `human_feedback` — operator observations (required for physical claims)
- `test_summaries` — per-session rollups
- `validation_results` — PASS/FAIL/INCONCLUSIVE/NOT_EXECUTED + evidence refs
- `agent_findings` — agent outputs with confidence + evidence refs

## 5. Agent runtime

`ClaudeCodeAdapter.run()` currently returns `pending_external` when no handler
is registered. Phase 5 adds a real subprocess runtime:
- explicitly configured executable path (env var, default `claude`)
- availability check (executable exists + `--version` works)
- argv list (no shell), timeout, bounded stdout/stderr capture
- run_id, agent identity, task id, evidence refs, exit status
- NO recursive Claude launches; agents run read-only or isolated-workspace
  commands only; internet-requiring runtimes are labelled as such

## 6. Lifecycle (Phase 8 gate)

`physical-test approve A|B|HYBRID|REJECT|MORE_TESTS` writes a decision row +
creates a checkpoint. Integration applies only after that row exists; every
integration records source digest + rollback checkpoint.

## 7. Physical-test honesty rules

- Server-side delivery events are labelled `delivered`, never `heard`.
- Audible playback requires `human_feedback` or explicit student-side ack.
- No mic/room/acoustics in this container → those scenarios stay
  `NOT_EXECUTED` until a human runs them on target hardware.

## 8. Dependency order

Phase 0 docs → Phase 1 sessions+CLI → Phase 2 collectors → Phase 3 DB+
dashboard → Phase 4 validator → Phase 5 agent runtime → Phase 6 tracks →
Phase 7 benchmarks/eval → Phase 8 approval gate → Phase 9 history.

Each phase: inspect → plan → implement → test → run suite → inspect diff →
update docs → report. No phase starts before its predecessor's tests pass.
