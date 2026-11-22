# Physical Test Monitoring — Limitations

**Date**: 2026-11-14
**Scope**: what this system does NOT solve and cannot claim.

## 1. Physical environment

| Limitation | Consequence | Required remedy |
|---|---|---|
| No microphone in this dev environment | Scenario A lifecycle verified only via fake arecord; no real acoustic accuracy | Human run on i5-7300U |
| No 30 physical devices | Student-load claims are loopback simulation at best | Human run with 30 phones on hotspot |
| Target thermals unavailable | 45-minute stability is NOT_EXECUTED | Human run + instrumentation |
| Room noise/distance/accents unknown | WER from clean synthetic speech is optimistic | Human Scenario E |
| audible playback cannot be verified server-side | all playback claims need human/student evidence | student ack + operator observation |

## 2. Application telemetry gaps (documented, not fixed)

| Gap | Impact on monitoring | Status |
|---|---|---|
| No capture-liveness endpoint | capture state is INFERRED, labelled as such | limitation accepted; recommend prod counter with approval |
| No final-tail decode counter (F-04) | tail loss cannot be measured live | limitation; F-04 pending |
| No TTS cache hit/miss stats | cost attribution missing | limitation |
| No WS reconnect/latency per message | delivery latency unmeasurable | limitation |
| /api/health pulls whole payload each 1 Hz | minor CPU cost | accepted; 1 Hz cap |
| R-1 capture/ASR thread race | could duplicate/drop one 20 ms frame per race | separate finding (R1_FOLLOWUP_REPORT.md) |

## 3. Agent runtime

| Limitation | Consequence |
|---|---|
| `TEAM_AGENT_RUNTIME` unset | agents return `pending_external`; never reported as executed |
| Runtime may require internet | must be labelled; offline classroom operation needs a local runtime |
| RLIMIT support is POSIX-only | Windows best-effort; target is Linux |
| Agent workspaces are per-candidate snapshots | not git branches; no history; single-writer lock only |

## 4. Data integrity

- WER/chrF vs engineer-written references are regression signals, NOT
  semantic quality claims.
- Missing metric values are `unavailable`, never zero.
- Telemetry DB is a separate file; the app DB is read-only for the monitor.
- No raw audio is recorded by monitoring. Transcripts stay in the app DB.

## 5. Integration

- Integration is never automatic. apply=True stages a patch for the human;
  it does not touch `guru/`.
- Rollback restores the *candidate*, not a merged tree — true merge requires
  human git/copy action and is out of scope for the gate.

## 6. Test coverage

- Physical scenarios: 0 executed in this environment.
- Automated tests: cover lifecycle, collectors (stub server), DB migrations,
  validator, benchmark gate, approval gate, history comparison.
- No end-to-end run against the real running app was performed this session
  (app not started; collectors were verified against a stub).
