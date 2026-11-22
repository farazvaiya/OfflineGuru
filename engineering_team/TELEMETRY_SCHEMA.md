# Telemetry Schema

**Date**: 2026-11-14
**DB**: `engineering_team/state/physical_tests.db` (migration 1)
**Convention**: timestamps are Unix epoch seconds (time.time()), consistent
with the harness DB. Missing values are NULL/"unavailable", never 0.

## Tables

### physical_sessions (Phase 1)
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | 12-hex unique session id |
| status | TEXT | created/running/paused/stopped/reported |
| started_at, paused_at, stopped_at, reported_at | REAL | NULL until that transition happens |
| teacher_language, target_language | TEXT | default en/bn |
| expected_students, actual_students | INTEGER | NULL = not recorded |
| microphone_info, laptop_info, student_devices | TEXT | free text, human-supplied |
| operating_system, network_config | TEXT | |
| internet_available | INTEGER | 0/1, NULL = unknown |
| room_noise, device_distances | TEXT | |
| metadata_json | TEXT | optional structured extras |
| notes | TEXT | |
| created_at, updated_at | REAL | |

### test_conditions
`id, session_id FK, key, value, source ('human'|'system'), recorded_at`

### human_feedback
`id, session_id FK, role, category ('observation'|'playback'|...), body, recorded_at`
The ONLY table that can support audible-playback claims.

### session_events
`id, session_id FK, event (transition name), detail, recorded_at`

### telemetry_events (Phase 2/3)
| Column | Notes |
|---|---|
| id | autoincrement |
| session_id | FK-ish (no FK enforced for cross-DB safety) |
| ts | epoch |
| collector | collector name (asr, tts, capture, ...) |
| status | ok / unavailable / error |
| payload_json | the measured dict |
| reason | why status != ok |
| source | e.g. "GET /api/health session.stats" |

### metric_snapshots
`id, session_id, ts, kind, value NULL` — value NULL when unknown.

### error_events
`id, session_id, ts, component, severity, message`

### student_events
`id, session_id, ts, event, detail` — delivery-side only; never audible-playback proof.

### validation_results (Phase 4)
`id, session_id, ts, check_id, outcome (PASS/FAIL/INCONCLUSIVE/NOT_EXECUTED),
measured_json, threshold_json, evidence, notes`

### agent_findings (Phase 5)
`id, session_id, ts, agent, finding, confidence, evidence_json`

### benchmarks (Phase 7, separate schema in same file)
`id, experiment_id, problem_id, dataset, dataset_digest, config_json,
environment_json, results_json, raw_path, created_at`

### decisions / integrations (Phase 8, separate schema in same file)
`decisions: id, decision, track, note, evidence_json, decided_by, created_at`
`integrations: id, decision_id FK, action, detail, source_digest_before/after,
rollback_checkpoint, created_at`

## Event honesty contract

- `status: unavailable` + `reason` — collector ran, metric not exposed.
- `status: error` + `reason` — collector failed; counted, never fatal.
- `NOT_EXECUTED` validation outcome — check could not run (hardware/reference).
- Delivery counts are labelled `delivered`; audible playback requires
  `human_feedback`.
