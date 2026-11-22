# Remediation Baseline — engineering_team

Date: 2026-11-17
Phase: 0 (baseline, read-only)
Source of truth: `engineering_team/reports/DEEP_RUNTIME_AUDIT.md` (sections 0–8), re-checked against current code.

## 1. Repository and runtime state

| Item | Value |
|---|---|
| Project root | `/home/faraz/project/offline_guru` |
| Python | `.venv/bin/python` (3.11+) |
| Git | **no repository** — no branch, no worktree, no `git status`; `git_manager` therefore always reports "not a repo" |
| Harness package | `engineering_team/` (17 modules under `harness/`, 9 under `physical/`) |
| Claude CLI | `/home/faraz/.npm-global/bin/claude`, version `2.1.325` |
| `ANTHROPIC_*` env | not set in this shell (so a real run would be `AUTH_REQUIRED`, unverified) |
| Live DB | `engineering_team/state/engineering_team.db` (schema v1, WAL) |
| Live workflow | `engineering_team/state/workflow.json` |

## 2. Initial database counts (read-only, `mode=ro`)

| Table | Rows |
|---|---|
| problems | 21 (20 `baseline_recorded` test demos + 1 `discovered` real: `960f2f495160` "Improve Bangla Speech Recognition") |
| tasks | 21 (all `open`, all owned by `root-cause-analysis`) |
| experiments | 30 (track A/B, `branch`/`worktree` NULL for all) |
| benchmarks | 40 |
| evaluations | 30 |
| decisions | 30 |
| integration_history | 0 |
| agent_runs | **0** |
| checkpoints | 10 (latest paths point at deleted `/tmp/pytest-of-faraz/...`) |
| workflow_events | 221 |

Workflow state at baseline: `PROBLEM_DISCOVERED`, `previous_state=IDLE`, `problem_id=960f2f495160`, `updated_at=2026-11-16T19:48:35+00:00`.

The 20 demo problems are named "Demo: decision gate exercise" (×10) and "Demo reject path" (×10), i.e. exactly the two tests in `tests/test_engineering_team_workflow.py`. Their presence in the live DB is the persisted symptom of D1.

## 3. Baseline test results (safe, non-billable)

Command:

```
ENGINEERING_TEAM_ROOT=$PWD/.tmp/baseline/team TMPDIR=$PWD/.tmp \
  .venv/bin/python -m pytest tests/test_engineering_team.py \
  tests/test_engineering_team_workflow.py tests/test_claude_runtime.py \
  -q -m "not real" -p no:cacheprovider
```

Result: **27 passed, exit 0**, `@pytest.mark.real` deselected (no LLM call, no network cost).
`tests/test_physical_monitoring.py` (40 tests) was **not** run at baseline: it drives a separate `physical_tests.db` subsystem that the orchestrator never imports, so it is out of scope for the P0 defects and has its own write path.

Baseline observation: even with `ENGINEERING_TEAM_ROOT` pointed at `.tmp`, the run still wrote **10 new files into `engineering_team/logs/agent_runs/`** and created a new directory under `engineering_team/state/agent_workspaces/`. Live DB counts were unchanged (verified before/after), but the live *filesystem* was not isolated. This is a defect the audit did not name — recorded below as **D12**.

## 4. Finding triage

| ID | Audit finding | Status now | Evidence / reproduction |
|---|---|---|---|
| D1 | Import-time `DB_PATH` binding lets tests write to the live DB | **Confirmed** | `orchestrator.py:25` `from .paths import DB_PATH` then `self.db_path = db_path or DB_PATH` in `__init__`. Probe: set `paths.DB_PATH` to a temp path → `orchestrator.DB_PATH` unchanged (live). Live DB contains 20 demo problems from the workflow test. |
| D2 | Live checkpoint rows point to deleted `/tmp` dirs | **Confirmed** | 10 rows; e.g. `/tmp/pytest-of-faraz/pytest-64/.../checkpoints/20261117-014532-06b561`; `/tmp` entries no longer exist. Caused by the same binding: `CheckpointManager` writes files under the temp `TEAM_ROOT` while the DB row goes to the live DB. |
| D3 | A run never completes/fails its task or advances state | **Confirmed** | `orchestrator.py:129-201` returns a dict; `TaskManager.complete/fail` have no callers. Live: 21 tasks still `open`, `agent_runs` 0. |
| D4 | Agent workspace is empty; repo never delivered | **Confirmed** | `claude_runtime.py:165` `_workspace()` only `mkdir`s; argv `:259` passes `--add-dir <empty ws>`; `find state/agent_workspaces -type f ! -name .writer.lock` → no output. |
| D5 | Decision report unreachable from CLI; none exists | **Confirmed** | `prepare_decision_report` not referenced in `__main__.py`; `engineering_team/reports/DECISION_REPORT.md` absent while 20 problems are at `WAITING_FOR_HUMAN_DECISION`. |
| D6 | Two runtimes; safer one dead | **Confirmed** | `agent_runtime` imported only by tests/docs; orchestrator uses `claude_runtime`. |
| D7 | `_safe_args` prefix containment bug | **Confirmed by read** | `agent_runtime.py` compares `str(rp).startswith(str(root))` with no separator/`is_relative_to`. |
| D8 | Checkpoints can include checkpoints/state | **Confirmed by read** | `checkpoint.py` `DEFAULT_SOURCE_PATHS` includes `engineering_team`; `_tar_filter` excludes `.git`, `data`, caches only. |
| D9 | `team.toml` never read | **Confirmed** | `grep -rn "load_team_config"` → definition only, zero call sites. `quick_repo_scan`, `SIGNOFFS_PATH` likewise unused. |
| D10 | `@pytest.mark.real` unregistered and runs by default | **Confirmed** | `pyproject.toml` has no `markers`, no `-m` filter; `tests/test_claude_runtime.py:326` is `@pytest.mark.real`. Baseline run produced the unknown-mark warning and required manual `-m "not real"`. |
| D11 | Dead config/discovery/signoff code | **Confirmed** | as D9. |
| D12 | **Runtime paths bypass `paths.TEAM_ROOT`** | **Confirmed (new)** | `claude_runtime.py:133-145` derives `agents_md`, `workspace_root`, `log_dir` from `Path(__file__).parent.parent` instead of `paths.TEAM_ROOT`; 10 log files + 1 workspace appeared in the live tree during the isolated baseline run. |
| D13 | "Real SUCCESS 9.2 s" claim | **Unverified** | `reports/CLAUDE_RUNTIME_TEST_RESULTS.md`; no surviving row/log (smoke test uses a deleted `TemporaryDirectory`), and the smoke assertion accepts any of five statuses. No attempt was made to reproduce it in Phase 0 (billable). |
| Real-LLM capability | Can an agent invoke the LLM and finish? | **Unverified** | No `ANTHROPIC_*` in shell; 0 persisted runs; 2 real TIMEOUT logs (60 s, empty output), 17 CANCELLED, 0 SUCCESS against the real binary. |
| D14 | Concurrency overwrite risk | **Confirmed low (vacuous)** | each run gets a unique `shared/<run_id>`, so `flock` never contends and no two agents share a tree; `max_concurrent_agents` is unread. |

## 5. Evidence snapshot (runtime)

Log census in `engineering_team/logs/agent_runs/` at baseline: 168 files — SUCCESS 64, FAILED 51, TIMEOUT 19, CANCELLED 17, AUTH_REQUIRED 17. Of those, runs whose `executable` was the real `/home/faraz/.npm-global/bin/claude`: **17 CANCELLED + 2 TIMEOUT (60 s)**, zero SUCCESS. All SUCCESS entries come from pytest fake-CLI shims, so they evidence plumbing, not AI execution.

Workspace directories: 15, containing no files other than `.writer.lock`.

## 6. Risks before modification

| Risk | Why it matters | Mitigation adopted |
|---|---|---|
| Any test run may append to the live DB | D1 is active; the workflow test creates problems/decisions/events on every run | Fix binding before touching runtime code; run every subsequent test with `ENGINEERING_TEAM_ROOT` + temp DB; verify live counts before/after each phase |
| Live DB already contains junk (20 demo problems, 21 tasks, 10 dangling checkpoints) | Cannot tell real from test data; `workflow.json` was overwritten by a test | **Not deleted** (destructive, needs human approval). Remediation must make future pollution impossible and leave cleanup as a separate approved operation |
| Checkpoint tarballs can absorb `checkpoints/` and `state/` | Disk growth; the environment's `/tmp` tmpfs already filled once | Filter `checkpoints/` and `state/` in P0.1/P0.2 work; use `.tmp` under the project, not `/tmp` |
| Real CLI invocation is billable/networked | Mission rule 8 forbids paying to test the suite | Keep `real` deselected; never invoke `claude` in tests |
| No git repo | Nothing to `worktree` from; checkpoint is the only rollback | Source delivery must work without git (copy of an explicit allowlist), with checkpoints as the safety net |
| `physical/` uses its own DB and duplicate `decisions` schema | Divergence risk, out of scope for P0 | Left untouched; reported, not modified |
| P0.3 brief is truncated in the request | The pasted mission ends at "…unrelated project" | Implemented to the evident intent: protect credentials/env, user files and unrelated project paths; documented assumption in the phase report |

## 7. Baseline conclusion

Confirmed: the harness is metadata-complete and runtime-inert. `agent_runs` 0, no artifacts, empty workspaces, no source delivery, no permission flags, and a DB binding that lets tests mutate production state. Unverified: any successful real-LLM execution. Not reproducible at baseline (and intentionally not attempted): the reported 9.2 s real success.

All P0 work below proceeds from this baseline with the production DB treated as **read-only data that must not change**.
