# Claude Runtime Usage Guide

**Date**: 2026-11-15
**Module**: `engineering_team/harness/claude_runtime.py` + `claude_adapter.py` + `Orchestrator.execute_agent`

## 1. Configure the runtime

The runtime auto-discovers `claude` on PATH. Override with:

```bash
export TEAM_CLAUDE_CLI=/path/to/claude     # explicit executable
export TEAM_CLAUDE_MODEL=sonnet            # optional --model
```

Verify availability (evidence, not assumption):

```bash
.venv/bin/python -c "
from engineering_team.harness.claude_runtime import AgentExecution
from engineering_team.harness.db import Database
print(AgentExecution(Database('/tmp/probe.db')).available())"
# {'available': True, 'executable': '/home/faraz/.npm-global/bin/claude',
#  'version': '2.1.325 (Claude Code)', 'exit_code': 0}
```

If `available` is False, every run is recorded as `NOT_CONFIGURED` — nothing
is fabricated.

## 2. Execute one specialist agent

```bash
cd /home/faraz/project/offline_guru

# create a task first (or use an existing one)
.venv/bin/python -m engineering_team problem --title "probe" --severity P3
# note the problem id, then add a task via Python (TaskManager) or use an
# existing task id:

.venv/bin/python -m engineering_team run-agent qa-regression \
    --task-id T3 \
    --problem "Verify the streaming flush path" \
    --evidence "engineering_harness/artifacts/stream_flush_after_fix.json" \
    --deliverables "validation_notes.md" \
    --constraints "read-only" "no approval" "no merge" \
    --timeout 120
```

The command prints the structured result:

```json
{"run_id": "...", "status": "SUCCESS|FAILED|TIMEOUT|AUTH_REQUIRED|NOT_CONFIGURED|CANCELLED",
 "error": null, "artifacts_valid": true, "missing_artifacts": [], "can_continue": true,
 "output": {"exit_code": 0, "elapsed_s": 9.2, "workspace": "...",
            "digest_before": "...", "digest_after": "...", "artifacts": [...]}}
```

Role names (from `agents/AGENTS.md` + `config/team.toml`): `chief-orchestrator`,
`problem-discovery`, `root-cause-analysis`, `solution-track-a`,
`opensource-research-track-b`, `integration-engineer`, `benchmark-engineer`,
`independent-evaluator`, `qa-regression` (or section letters A–I).

## 3. Inspect execution history

```bash
.venv/bin/python -c "
from engineering_team.harness.db import Database
db = Database('state/engineering_team.db')
for r in db.query('SELECT id,agent,status,started_at,finished_at,error FROM agent_runs ORDER BY started_at DESC LIMIT 10'):
    print(r['id'], r['agent'], r['status'], r['error'] or '')
"
```

Per-run logs: `engineering_team/logs/agent_runs/<run_id>.log` (full metadata,
bounded output tails).

## 4. Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| `NOT_CONFIGURED` | `claude` not on PATH / wrong `TEAM_CLAUDE_CLI` | fix path; re-run availability probe |
| `AUTH_REQUIRED` | CLI auth expired/missing (stderr matched auth signatures) | human re-authenticates the CLI; never store credentials here |
| `TIMEOUT` | `--max-turns` too high or task too big for `timeout_s` | raise `--timeout` (default 300 s) or narrow the task |
| `FAILED` + "not valid CLI json" | CLI printed non-JSON (e.g. interactive prompt) | ensure `-p` print mode is used (the adapter sets it); check `stdout_tail` in the run log |
| `FAILED` + "workspace ... locked" | another run holds the single-writer lock | wait for the other run; locks are per-workspace |
| role `KeyError` | unknown agent name | use a name from §2 or a section letter A–I |

## 5. Disable the runtime safely

The runtime is opt-in per call: `Orchestrator.execute_agent` uses it only when
invoked. To disable entirely:

```bash
unset TEAM_CLAUDE_CLI TEAM_CLAUDE_MODEL   # if set
# and simply do not call run-agent / execute_agent
```

Without an executable, runs are recorded `NOT_CONFIGURED` and the workflow
state machine is unaffected; the legacy `pending_external` path remains for
genuinely delegated work. No state rollback is needed — the runtime never
modifies workflow state, signoffs, decisions, or production files.

## 6. Boundaries (unchanged)

- Human approval gates (state machine, signoffs) are untouched.
- Track A/B workspaces are separate; concurrent runs are flock-protected.
- No permission-bypass flags; env whitelisted; secrets never read or logged.
