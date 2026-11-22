# Claude Runtime Integration Design

**Date**: 2026-11-15
**Task**: Connect `engineering_team` orchestration to a real Claude Code CLI runtime.
**Scope**: runtime adapter + orchestrator integration + tests + docs. No production
(`guru/`) changes, no `engineering_harness/` changes, no auto-integration, no
bypassed approval gates.

---

## 1. Current architecture (verified by inspection)

| Component | File | State |
|---|---|---|
| Adapter | `harness/claude_adapter.py` | Records runs in `agent_runs` table; `run()` returns `pending_external` when no handler registered; local `register()` handlers exist but none are registered for Claude execution |
| Orchestrator | `harness/orchestrator.py:42` | Instantiates `ClaudeCodeAdapter(self.db)` but never invokes it; `run_loop()` drives gates from disk artifacts only |
| Agent roles | `agents/AGENTS.md` | 9 roles defined (orchestrator, discovery, RCA, Track A, Track B, integration, benchmark, evaluator, QA) — textual definitions only, no executable wiring |
| State machine | `harness/state_machine.py` | Complete; approval gate intact (INTEGRATION only from APPROVED) |
| DB | `harness/db.py` | `agent_runs(id,agent,task_id,problem_id,status,started_at,finished_at,output_json,error)` — schema sufficient for execution metadata |
| CLI | `__main__.py` | No agent-execution command exists |

**Missing connection**: nothing translates "run agent X on task Y" into an actual
Claude Code subprocess invocation with role definition, workspace, timeout, and
evidence capture.

## 2. Installed runtime (verified, not assumed)

- Executable: `/home/faraz/.npm-global/bin/claude` (`which claude` → exit 0)
- Version: `2.1.325 (Claude Code)`
- Non-interactive interface: `claude -p <prompt>` (`--print`), per `--help`
- Output formats: `--output-format text|json|stream-json`
- Controls: `--max-turns`, `--model`, `--add-dir`, `--allowedTools`,
  `--disallowedTools`, `--session-id`, `--resume`
- Auth: environment has `ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_BASE_URL` set (names
  only; values never read or logged by this system)

## 3. Proposed execution flow

```
Orchestrator.execute_agent(agent_name, task, problem, workspace, timeout)
  1. resolve role definition from agents/AGENTS.md section (parsed, not duplicated)
  2. build prompt: role + task + problem description + evidence refs +
     deliverables + constraints (incl. "read-only; no approval; no merge")
  3. prepare isolated workspace (Phase 6 snapshot pattern; single-writer flock)
  4. record agent_runs row status=running, started_at
  5. subprocess.run(argv, no shell, timeout, cwd=workspace, env=filtered)
     argv = [claude, "-p", prompt, "--output-format", "json",
             "--max-turns", N, "--model", M, "--add-dir", workspace]
     permissions: default (no --allow-dangerously-skip-permissions)
  6. capture stdout/stderr/exit code/duration
  7. classify: 0+parseable json → SUCCESS; timeout → TIMEOUT (SIGKILL after grace);
     non-zero → FAILED with stderr tail; auth-shaped stderr → AUTH_REQUIRED;
     missing executable → NOT_CONFIGURED
  8. persist full metadata + output (bounded 64 KiB) to agent_runs + logs/
  9. return AgentRunResult; orchestrator NEVER marks a task done on
     FAILED/TIMEOUT/NOT_CONFIGURED/AUTH_REQUIRED
  10. evaluator separately inspects artifacts (existing IndependentEvaluator)
```

## 4. Status vocabulary (explicit, per task)

`SUCCESS, FAILED, TIMEOUT, NOT_CONFIGURED, AUTH_REQUIRED, CANCELLED`
plus legacy `pending_external` (only when execution is genuinely delegated
elsewhere) and `running`/`failed` from the original adapter contract.

## 5. Security boundaries

- **Least privilege**: no permission-bypass flags ever; default permission mode.
- **Secrets**: env filtered to a whitelist (PATH, HOME, ANTHROPIC_*); no secret
  values read, copied, or logged; only presence recorded.
- **Workspace containment**: agent gets `--add-dir <workspace>` only; argv-only
  execution (no shell); no Write/Edit tools for production paths (prompt-level
  constraint + read-only deliverable instructions).
- **No self-approval**: adapter cannot write `signoffs.json`; approval gate
  untouched.
- **No auto-integration**: adapter output never triggers transitions.
- **Untrusted content**: agent findings are data (DB rows), never executed as
  instructions; orchestrator parses only its own argv.

## 6. Failure handling

| Failure | Detection | Result status | Orchestrator action |
|---|---|---|---|
| executable missing | `shutil.which` at construction | NOT_CONFIGURED | block task, log |
| auth missing/invalid | stderr pattern (`auth`/`credential`/`login`/`API key`) | AUTH_REQUIRED | block task, request human |
| timeout | `subprocess.TimeoutExpired` | TIMEOUT | kill process group, record |
| non-zero exit | returncode | FAILED | record stderr tail |
| unparseable output | json parse failure on `--output-format json` | FAILED | record stdout tail |
| cancelled | KeyboardInterrupt path | CANCELLED | record |

In every non-SUCCESS case the task is NOT marked complete; workflow stays at
its current state-machine position.

## 7. Workspace isolation (no git in this repo)

- Unique dir per run: `state/agent_workspaces/<run_id>/`
- Source digest before and after execution (evidence.py pattern)
- `flock` single-writer lock on the workspace dir
- Track A and B get separate workspace roots → simultaneous modification
  impossible by construction
- Artifact path validation: only paths inside the workspace are accepted as
  deliverables

## 8. Test strategy

| Test | Type | Method |
|---|---|---|
| executable discovery | unit | monkeypatched `shutil.which` |
| successful real execution | unit (mock) | fake `claude` script on PATH printing valid JSON |
| missing CLI | unit | empty PATH |
| auth failure | unit | fake CLI writing auth-shaped stderr, exit 1 |
| timeout | unit | fake CLI sleeping; timeout_s=0.5 |
| non-zero exit | unit | fake CLI exit 3 |
| invalid output | unit | fake CLI printing non-JSON |
| role loading | unit | parse agents/AGENTS.md sections |
| workspace isolation | unit | digests differ between A/B workspaces |
| execution logging | unit | log file exists with run metadata |
| DB persistence | unit | agent_runs row fields complete |
| orchestrator integration | unit | execute_agent returns real result; failure does not advance state |
| concurrent execution protection | unit | flock conflict refused |
| failure recovery | unit | after FAILED, workflow state unchanged, task open |
| no auto production integration | unit | signoffs/decision tables unchanged after runs |
| real CLI smoke | **real** | only if `claude` installed+authed; labelled REAL; minimal `echo` task |

Mock tests and real execution are never conflated: the fake-CLI tests are
labelled MOCK; the smoke test is labelled REAL and skips itself when the CLI
is absent/unauthenticated.

## 9. Rollback / compatibility

- `ClaudeCodeAdapter` keeps its public interface (`register`, `run`) — the
  existing `pending_external` path remains for unconfigured agents.
- New runtime is an additive class (`ClaudeCodeRuntime`) used by the adapter
  when configured; orchestrator integration is a new method, no existing
  transition logic changed.
- DB schema unchanged (existing `agent_runs` columns suffice; output_json
  carries structured metadata).

## 10. Explicit non-goals

- No full autonomous workflow launch (task instruction §11).
- No production integration, no merges, no human-approval bypass.
- No fabricated execution evidence.
