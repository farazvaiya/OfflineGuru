# Claude Runtime Implementation Report

**Date**: 2026-11-15
**Task**: Connect `engineering_team` orchestration to a real Claude Code CLI runtime.
**Status**: COMPLETE — real runtime verified end-to-end; all approval gates intact.

---

## 1. What was verified before implementation (Phase A)

| Item | Finding |
|---|---|
| `claude` installed | `/home/faraz/.npm-global/bin/claude`, version **2.1.325** (real `--version` output) |
| Non-interactive mode | `-p/--print` (from installed `--help`, not assumption) |
| Output formats | `--output-format text\|json\|stream-json` |
| Controls | `--max-turns`, `--model`, `--add-dir`, `--allowedTools`, `--disallowedTools`, `--session-id` |
| Auth | env has `ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_BASE_URL` set (names only; values never read or logged) |
| Existing adapter | `claude_adapter.py` recorded runs but executed nothing (`pending_external`); orchestrator instantiated it but never invoked it |

## 2. What was implemented (Phases B–C)

### New: `harness/claude_runtime.py`
- `AgentExecution` — real subprocess runtime:
  - `discover_claude()` via `shutil.which` (no shell)
  - `available()` — version probe, evidence-backed
  - `run()` — full flow: DB row (running) → availability gate → role load →
    workspace + flock single-writer lock → prompt assembly (role + task +
    problem + evidence + deliverables + constraints) → argv-only subprocess
    (`-p`, `--output-format json`, `--max-turns`, `--add-dir`, no permission
    bypass flags) → bounded 64 KiB capture → classification
    (SUCCESS/FAILED/TIMEOUT/AUTH_REQUIRED/NOT_CONFIGURED/CANCELLED) →
    before/after source digests → artifact path validation → DB finish +
    per-run log file
  - `load_role_definition()` — parses `agents/AGENTS.md` by letter, team.toml
    alias (e.g. `qa-regression`), or normalized substring
  - env whitelist (PATH/HOME/ANTHROPIC_*/LANG); no secret values touched
- `ClaudeCodeAdapter` extended: when a runtime is attached it delegates to it
  (runtime owns the `agent_runs` row); legacy local-handler and
  `pending_external` paths unchanged.

### Modified: `harness/orchestrator.py` (additive only)
- `execute_agent(agent, task_id, ...)` — the required 8-step flow: select agent
  → execution record → isolated workspace → invoke runtime → collect result →
  validate artifacts against workspace → record workflow evidence → continuation
  decision (`can_continue` only on SUCCESS). **No state-machine transition, no
  auto task completion, no signoff writes.**
- `self.runtime`/`self.adapter.attach_runtime()` in `__init__`.

### Modified: `__main__.py`
- `run-agent` CLI command wiring `execute_agent`.

## 3. Phase E — real CLI smoke test (REAL, not mock)

Executed against the installed CLI (test `test_real_claude_cli_smoke`, plus a
direct verification run):

```
available: {'available': True, 'executable': '/home/faraz/.npm-global/bin/claude',
            'version': '2.1.325 (Claude Code)', 'exit_code': 0}
status: SUCCESS
exit_code: 0   elapsed_s: 9.2
stdout_tail: {"duration_api_ms":7203,"stop_reason":"end_turn",
              "session_id":"bab890bb-...","total_cost_usd":0.1028,
              "usage":{"input_tokens":25177,"output_tokens":109,...}}
stderr_tail: [claude-code:unrecognized_model] {"model":"auto/coding:free",...}
```

The stderr model-name warning did not affect the exit code; status classified
honestly as SUCCESS on exit 0 + parseable JSON. The smoke task was bounded
(`--max-turns 1`, read-only constraints, no tools).

## 4. Phase D — mock-based tests (labelled MOCK)

18 mock tests + 2 orchestrator integration tests + 1 real smoke test = 20 new
tests in `tests/test_claude_runtime.py`. Mocks use a fake `claude` script on
PATH; they never claim real execution.

## 5. Security boundaries honoured

- No `--allow-dangerously-skip-permissions` anywhere (grep-verified).
- No secrets read, copied, or logged; env whitelisted.
- No shell; argv list only.
- Workspace containment + flock; Track A/B get separate roots.
- Agent output treated as data; no agent text executed.
- No self-approval path; signoffs/decisions tables untouched by runs
  (asserted in `test_no_auto_production_integration`).

## 6. Compatibility

- `ClaudeCodeAdapter.run()` signature unchanged; `pending_external` preserved
  for unconfigured agents.
- `agent_runs` schema unchanged; metadata in `output_json`.
- `engineering_harness/`, `guru/`, `config/` untouched.
- Full suite: **332 passed** (312 before + 20 new), no existing test weakened.

## 7. Limitations

- The real smoke test used a trivial echo-class prompt, not an engineering
  task; the full autonomous workflow is NOT started (per task §11).
- CANCELLED status is reachable via KeyboardInterrupt; tested via patched
  subprocess, not a live Ctrl-C.
- Non-POSIX flock fallback is documented (target is Linux).
- Cost/token usage is recorded by the CLI itself; the adapter does not
  interpret it.
