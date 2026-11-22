# Security and Privacy

**Date**: 2026-11-14
**Scope**: the physical test monitoring system in `engineering_team/physical/`.

## Data handling

| Rule | Implementation |
|---|---|
| No raw audio recording by default | collectors read `/api/health` counters only; no audio capture anywhere in monitoring |
| Explicit opt-in for any recording | none exists; if added later it requires a human decision and a separate approval gate |
| Transcripts stay in the app DB | monitoring never copies transcript text; payload_json holds counters only |
| No student auth tokens in evidence | collectors read subscriber counts/roles, never tokens; dashboard renders no PII |
| No credentials in reports | all reports are generated from counters; secrets never enter telemetry |
| Retention | physical_tests.db is local; `physical-test export` writes per-session JSON; deletion = delete the DB file + export dir (documented, human-executed) |
| Bounded storage | telemetry events capped by the 1 Hz poll interval; dashboard keeps last 200 events in memory; DB growth is linear and small |

## Access control

- Dashboard binds to 127.0.0.1 only (loopback, no internet exposure).
- The app's own `/api/health` token requirements are unchanged; monitoring
  reads it with the same credentials the operator already uses.
- Agent runtime: path containment (allowed_roots), argv-only execution,
  no shell, recursive-Claude refusal, RLIMIT CPU/AS.

## Input validation

- Session fields: typed at create (int expected_students, bool-ish
  internet_available), free-text fields are strings; no interpolation into SQL
  (all queries parameterized).
- Validator outcomes are enum-checked (PASS/FAIL/INCONCLUSIVE/NOT_EXECUTED).
- Dashboard HTML is generated server-side from DB rows; no user HTML rendered.

## Log safety

- Collector errors are recorded with type + message; full tracebacks are not
  stored in telemetry rows.
- The dashboard never exposes the app DB path or environment variables.

## Offline operation

- All components run locally; no external calls. The only network-facing
  surface is the loopback dashboard port.
- An agent runtime requiring internet must be labelled as such in its run
  evidence; it is never assumed available offline.

## Agent prompt-injection posture

- Agent findings are recorded as evidence-referenced rows; transcript content
  or external research text is never executed as instructions.
- The runtime executes only explicitly configured commands with bounded
  output; it does not follow links or fetch content.

## Incident handling

- Collector failure → error event row + counter; loop continues.
- Dashboard failure → 500 JSON, app unaffected (separate process, read-only).
- Suspected fabricated evidence → benchmark engine refuses missing raw
  artifacts; validator refuses PASS without evidence.
