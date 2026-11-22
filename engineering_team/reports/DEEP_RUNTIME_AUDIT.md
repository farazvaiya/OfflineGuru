# Offline Guru Engineering Harness — Deep Runtime Audit

Date: 2026-11-17
Scope: `engineering_team/` (harness, agents, config, state, logs) plus the `physical/` subsystem it shares a DB directory with.
Mode: **read-only**. No source file, config, DB row, task, problem, candidate or integration was created, modified, approved, rejected, deleted or executed. No LLM call was made by this audit.

## 0. Method and evidence base

| Evidence source | How obtained | Read-only? |
|---|---|---|
| 32 harness/agent modules (~2,655 lines) | full read | yes |
| 10 `physical/` modules (~1,950 lines) | full read | yes |
| Live DB `state/engineering_team.db` | `sqlite3` URI `mode=ro` | yes |
| 158 run logs in `logs/agent_runs/` | text parse | yes |
| `state/workflow.json`, `state/agent_workspaces/**`, `checkpoints/**` | ls/read | yes |
| CLI surface | `python -m engineering_team --help` + source read | yes |
| Claude CLI presence/version/flags | `claude --version`, `claude --help` | yes (no prompt sent) |
| Import-time DB binding defect | in-memory Python assertion (`paths.DB_PATH` vs `orchestrator.DB_PATH`) | yes (no writes) |

Classification vocabulary used below:
- **Verified** — proven by source read *and* at least one of: executed code path, persisted DB/log/filesystem evidence, or an executed read-only probe.
- **Not verified** — code path exists and is syntactically complete but no retained evidence of a successful execution. Not claimed as working.
- **Missing** — no implementation exists.

**Central honesty constraint applied throughout: a configured agent role is not proof of an executable AI agent.** Every "agent" row below is graded on *runtime evidence*, not on the presence of a prompt section in `agents/AGENTS.md`.

---

## 1. Executive summary

**Verdict: the harness is a workflow-metadata manager with a fully coded, wired-up, but operationally unproven agent launcher. It cannot perform autonomous AI software development end-to-end today.**

Three facts dominate everything else:

1. **Zero agent executions have ever been persisted.** `agent_runs` in the live DB contains **0 rows** (verified, `mode=ro`). The 158 log files under `logs/agent_runs/` were all produced by pytest against stub executables, except 18 runs against the real `claude` binary — and those are **16 `CANCELLED` + 2 `TIMEOUT`, with zero `SUCCESS`**.
2. **No agent has ever produced an artifact.** All 14 directories under `state/agent_workspaces/shared/` contain exactly one file: `.writer.lock`. No source file, patch, diff or report has ever been written by an agent in this repository.
3. **The production code cannot deliver the project to an agent.** `AgentExecution._workspace()` (`harness/claude_runtime.py:165`) creates an empty per-run directory and spawns `claude` with `cwd=workspace`. The repository is never copied, checked out or mounted, and the project root is never passed via `--add-dir`. An agent therefore starts in an empty directory with no source code to read. This alone makes "AI development" impossible regardless of the LLM.

What *does* work (verified): SQLite schema/migrations, atomic workflow-state JSON, the 16-state state machine with its approval gate, tarball checkpoints, `claude` discovery and version probing, subprocess argv construction, flock-based workspace locking, agent-run log files, and Markdown report rendering.

What does *not* exist at all (missing): any test/benchmark **execution** inside the harness, any task completion transition after an agent run, any automatic dispatch of a task to its owning agent, any integration (patch application/merge), any CLI command that runs a whole cycle, crash recovery for interrupted runs, and any authentication around approvals. The `physical/` subsystem is a parallel universe with its own database and duplicate `decisions`/`integrations` tables, untouched by the orchestrator.

Net effect on the live workflow: the state file sits at `PROBLEM_DISCOVERED` with 21 `open` root-cause tasks and 20 duplicated test-generated demo problems. Nothing will ever advance it — no timer, no command, no agent.

---

## 2. Actual execution architecture

### 2.1 The one path that spawns an agent

```
python -m engineering_team run-agent --agent <role> --task <id>
  __main__.py:221                -> Orchestrator.execute_agent(...)
  harness/orchestrator.py:129    -> ClaudeCodeAdapter.run(...)      (:151)
  harness/claude_runtime.py:184  -> AgentExecution.run(...)
      :165  _workspace()      mkdir -p state/agent_workspaces/<track>/<run_id>
      :170  _acquire_lock()   flock .writer.lock
      :195  load_role_definition(AGENTS.md, role)      (:70)
      :259  argv = [exe, "-p", prompt, "--output-format", "json",
                    "--max-turns", N, "--add-dir", str(ws)]
      :48   env = {PATH, HOME, LANG, LC_ALL, CLAUDE_CONFIG_DIR, ANTHROPIC_*, TEAM_*}
             subprocess.run(..., cwd=ws, timeout=...)
      :285  parse JSON -> status SUCCESS / FAILED / TIMEOUT /
             NOT_CONFIGURED / AUTH_REQUIRED / CANCELLED
      :341  _finish() -> INSERT agent_runs + write logs/agent_runs/<run>.log
  orchestrator.py:198          -> returns {"can_continue": status==SUCCESS, ...}
```

This is the **only** execution path in the whole package. `harness/agent_runtime.py` is a second, more careful runtime (path containment via `_safe_args`, `RLIMIT_CPU`/`RLIMIT_AS`, refusal to run executables named `claude`) that is **imported by tests and docs but never by the orchestrator** — dead production code.

### 2.2 The other chain is pure metadata

`Orchestrator.run_loop()` and the state machine operate exclusively on rows and dicts: problems → tasks → experiments (track A/B) → benchmarks → evaluations → decisions → integration. Every function in that chain **accepts its result payload from the caller**:

- `BenchmarkManager.record(...)` (`harness/benchmark.py:47-48`) takes `config: dict`, `results: dict`, `raw` and stores them. It never runs a workload.
- `Evaluator.evaluate_problem()` compares previously recorded JSON; its only possible verdicts are `inconclusive` / `evidence_recorded`. It never executes pytest.
- `ReportGenerator.generate_decision_report()` (`harness/report.py`) renders 14 sections from DB rows into Markdown — called only from `Orchestrator.prepare_decision_report` (`orchestrator.py:259`), which has **no CLI command**: `__main__.py`'s `report` subcommand only prints the expected path and whether the file exists.

Consequence: every number, verdict and decision in the database was supplied by a test or a human, never measured by the harness.

### 2.3 Both runtimes disagree, and neither is sandboxed

| Property | `claude_runtime.AgentExecution` (live) | `agent_runtime.AgentRuntime` (dead) |
|---|---|---|
| Executable | `claude` / `TEAM_CLAUDE_CLI` | arbitrary argv |
| Path containment | none (advisory prompt text only) | `_safe_args` prefix check (has a `startswith` boundary bug) |
| Resource limits | none (only wall-clock timeout) | `RLIMIT_CPU`, `RLIMIT_AS` |
| Refuses `claude` | n/a | yes — which is why it cannot be the AI runtime |
| Used by orchestrator | **yes** | **no** |

### 2.4 Observed runtime evidence (real binary)

`claude` is installed: `/home/faraz/.npm-global/bin/claude`, `--version` → `2.1.325 (Claude Code)`. `--help` confirms `--allowedTools`, `--permission-mode`, `--add-dir`, `--dangerously-skip-permissions` all exist — **the harness passes none of the first three**, so headless runs get default permissions (non-interactive prompts cannot be answered, so writes are denied) and no tool allowlist.

Log census (`logs/agent_runs/`, 158 files):

| Status | Count | Origin |
|---|---|---|
| SUCCESS | 60 | all from pytest fake-CLI shims |
| FAILED | 48 | pytest shims |
| TIMEOUT | 18 | shims + **2 real** (60 s, empty stdout/stderr) |
| CANCELLED | 16 | **real** executable |
| AUTH_REQUIRED | 16 | shims |
| SUCCESS with real executable | **0** | — |

`reports/CLAUDE_RUNTIME_TEST_RESULTS.md` claims a real SUCCESS (9.2 s, $0.1028, v2.1.325). That claim is **unverifiable, not disproven**: `tests/test_claude_runtime.py::test_real_claude_cli_smoke` writes both the log directory and the DB inside a `tempfile.TemporaryDirectory()` that is deleted on exit, and its assertion accepts *any* of five statuses (`NOT_CONFIGURED`, `AUTH_REQUIRED`, `TIMEOUT`, `FAILED`, `SUCCESS`) — so a passing test proves nothing about success. No DB row or log survives to corroborate.

---

## 3. Agent-by-agent capability matrix

Roles are defined in `agents/AGENTS.md` (sections A–I) and mirrored in `config/team.toml` (9 `[[agents]]` entries). Loading is verified by `tests/test_claude_runtime.py` role-loading test; **dispatch is not automatic for any of them** — `run-agent --agent <name>` must be typed by a human, and no code maps `tasks.owner` to a dispatch.

| # | Role (AGENTS.md) | Prompt loads | Reachable via CLI | Actually executed (real) | Can see project source | Produced artifact |
|---|---|---|---|---|---|---|
| A | orchestrator/planner | Verified | Verified | Not verified (0 successes) | **No** (empty ws) | None |
| B | repository analyst | Verified | Verified | Not verified | **No** | None |
| C | root-cause analyst | Verified | Verified | Not verified | **No** | None |
| D | implementation engineer | Verified | Verified | Not verified | **No** | None |
| E | test engineer | Verified | Verified | Not verified | **No** | None |
| F | benchmark engineer | Verified | Verified | Not verified | **No** | None |
| G | QA / regression | Verified | Verified | Not verified | **No** | None |
| H | reviewer / critic | Verified | Verified | Not verified | **No** | None |
| I | integrator / release | Verified | Verified | Not verified | **No** | None |

"Can see project source = No" is verified by code (`_workspace()` creates an empty dir; argv has no repo path) and by filesystem evidence (14 workspaces, only `.writer.lock` entries). All 21 tasks in the DB are `open` and owned by `root-cause-analysis`; no task has ever been completed by any role.

---

## 4. Missing runtime components

| # | Component | Evidence | Class |
|---|---|---|---|
| 4.1 | **Source delivery to agents** — no copy/worktree/mount of the repo into the workspace | `harness/claude_runtime.py:165` `_workspace`; argv `:259` has no project `--add-dir`; workspaces contain only `.writer.lock` | Missing |
| 4.2 | **Automatic dispatch** task.owner → agent | `tasks.owner` written once in `discover_problem`; no reader anywhere | Missing |
| 4.3 | **Task state transitions after a run** (complete/fail/retry) | `orchestrator.py:129-201` returns a dict; `TaskManager` completion methods are never called | Missing |
| 4.4 | **Test execution** | no `subprocess` pytest call in harness; `Evaluator` only reads stored JSON | Missing |
| 4.5 | **Benchmark execution** | `benchmark.py:47` `record()` takes caller-supplied results | Missing |
| 4.6 | **Artifact capture / verification** | no hashing, diffing or artifact table; workspace digest is computed but only to detect "did anything change", and nothing consumes it | Missing |
| 4.7 | **Integration** (patch apply/branch/merge) | `integrate_approved()` records `"awaiting_manual_merge"` only; `integration_history` = 0 rows; no git repo exists in the project | Missing |
| 4.8 | **End-to-end cycle command** | `__main__.py` has start/status/pause/resume/stop/report/decisions/approve/reject/request-tests/physical-test/run-agent/checkpoint/inspect/problem/integrate — none runs discovery→agent→test→benchmark→evaluate→decide | Missing |
| 4.9 | **Crash recovery / run reconciliation** | no startup scan for `status='running'` rows; `agent_runs` empty so never exercised | Missing |
| 4.10 | **Concurrency control as configured** | `max_concurrent_agents = 1`, `sequential_execution = true` in `config/team.toml`; `load_team_config()` (`config_manager.py:15`) is never called | Missing (config dead) |
| 4.11 | **Repo scanning used by discovery** | `discovery.quick_repo_scan()` (`discovery.py:12`) defined, never called; `inspect` only counts paths | Missing (dead code) |
| 4.12 | **Signoff persistence** | `paths.SIGNOFFS_PATH` (`paths.py:36`) defined, never read/written | Missing |
| 4.13 | **Decision report from CLI** | `prepare_decision_report` unreachable from `__main__.py`; `reports/DECISION_REPORT.md` does not exist despite 20 demo problems reaching `WAITING_FOR_HUMAN_DECISION` | Missing |
| 4.14 | **Single LLM runtime** | two competing runtimes (`claude_runtime`, `agent_runtime`); no provider abstraction; no OpenRouter/Ollama/OpenAI/Gemini code anywhere | Partially missing |
| 4.15 | **`physical/` integration** | separate `state/physical_tests.db`, own `decisions`/`integrations` schema, never imported by orchestrator | Missing (duplication) |

**Answers to the ten required capability questions**

| Question | Answer | Class |
|---|---|---|
| Can an agent invoke an LLM? | Yes in code: argv `claude -p … --output-format json` (`claude_runtime.py:259`), binary discovered and probed (`:147`). No retained successful invocation. | Not verified |
| Can it read project files? | **No.** Workspace is empty and the repo is never mounted. | Verified (negative) |
| Can it modify an isolated workspace? | Not in practice: no tool allowlist / permission mode, so headless writes are denied; zero artifacts across 14 workspaces. | Not verified (negative) |
| Can it run pytest? | No harness code runs pytest; an agent could only do so if Bash were permitted and tests were present — neither holds. | Missing |
| Can it run benchmarks? | No. `benchmark.record()` stores dicts. | Verified (negative) |
| Are outputs persisted? | Partially: `agent_runs` rows + log files on the metadata side (0 rows live, 158 orphan log files from tmp-DB runs); agent *content* is never persisted. | Verified (partial) |
| Recovery after interruption? | `workflow.json` writes are atomic, but a killed run leaves `status='running'` forever and its workspace is not resumed. | Missing |
| Mid-run agent failure? | Recorded as `FAILED`/`TIMEOUT` in the log and row; the task stays `open` and the workflow state does not change, so the failure is invisible downstream. | Verified (handled as noise only) |
| Concurrent overwrite risk? | Low but for the wrong reason: each run gets a **unique** workspace dir (`shared/<run_id>`), so `flock` never contends and no two agents ever share a tree. Tracks A/B are separate dirs too. Parallelism is not enabled (`max_concurrent_agents` unread). | Verified |
| Is integration possible without approval? | No state-machine path reaches `INTEGRATION` except from `APPROVED` (`state_machine.py` gate); but approvals are unauthenticated shell commands and integration itself does nothing. | Verified |

---

## 5. Confirmed defects

### D1 — Tests write into the production database (Verified)
`harness/orchestrator.py:25` does `from .paths import DB_PATH` — a **value bound at import time**. `tests/test_engineering_team_workflow.py` monkeypatches `engineering_team.harness.paths.DB_PATH` in a fixture, but `tests/test_claude_runtime.py` imports `Orchestrator` at module scope, so collection binds the live path first and the fixture has no effect.

Read-only probe: after `paths.DB_PATH = tmp/x.db`, `orchestrator.DB_PATH` still equals `/home/faraz/project/offline_guru/engineering_team/state/engineering_team.db`.

Evidence in live DB: 21 problems = **20 test demos** (10× "Demo: decision gate exercise", 10× "Demo reject path") + 1 real ("Improve Bangla Speech Recognition", `960f2f495160`); 21 tasks all `open`; 30 experiments; 40 benchmarks; 30 evaluations; 30 decisions; 221 workflow events. `workflow.json` was last written by the test fixture and now points at `PROBLEM_DISCOVERED` for `960f2f495160`.

### D2 — Checkpoints in the live DB point at deleted temp directories (Verified)
`checkpoints` has 10 rows, every `path` under `/tmp/pytest-of-faraz/...`; latest `e53256d88a2b`. Those directories no longer exist, so `checkpoint restore` on any recorded checkpoint fails. Root cause is the same import-binding as D1 (`Orchestrator` builds `CheckpointManager(root=TEAM_ROOT/…)` with the temp root while writing to the live DB).

### D3 — Agent runs never advance the task (Verified)
`Orchestrator.execute_agent()` (`:129`) returns `{"can_continue": bool, ...}` and stops. No `TaskManager` completion/failure call, no state transition, no artifact row. A successful agent run leaves the workflow exactly where it was.

### D4 — `run-agent` is reachable but useless in place (Verified)
Even ignoring permissions, the agent's `cwd` is an empty directory with `--add-dir` pointing at that same empty directory. There is no code path that makes repository content available, so all nine roles are functionally blind.

### D5 — Decision report is never generated (Verified)
`prepare_decision_report` has no CLI entry (`__main__.py:105` command list). 20 problems sit at `WAITING_FOR_HUMAN_DECISION`, `reports/DECISION_REPORT.md` does not exist, and `report` merely prints a missing-path notice.

### D6 — Two divergent runtimes, one dead (Verified)
`agent_runtime.py` is imported only by tests/docs; its safety features (rlimits, containment) therefore protect nothing in production, while the live runtime has none.

### D7 — `_safe_args` prefix containment bug in dead runtime (Verified by read)
`agent_runtime.py` containment uses `str.startswith` on paths without resolving or appending a separator, so a sibling path sharing a prefix (e.g. `.../ws_evil` vs `.../ws`) passes. Latent, but it is the only containment code in the package.

### D8 — Checkpoint tarball is self-referential (Verified by read)
`checkpoint._tar_filter` excludes `.git/`, `data/`, `__pycache__` but **not** `checkpoints/` or `state/`, so every checkpoint archive can absorb prior archives and the live SQLite DB, growing without bound.

### D9 — Config is decorative (Verified)
`config/team.toml` (`max_concurrent_agents`, `sequential_execution`, 9 agent entries) is never read: `load_team_config()` has zero callers. The DB schema and the TOML can drift silently — they already have (21 tasks vs 9 configured roles).

### D10 — `@pytest.mark.real` is unregistered (Verified)
`pyproject.toml` `[tool.pytest.ini_options]` sets only `testpaths`, `addopts = "-q"`, `filterwarnings`. No `markers` entry and no `-m "not real"`. `tests/test_claude_runtime.py:326` therefore (a) emits an unknown-mark warning and (b) **spawns the real, billed Claude CLI during every full test run**, with a 120 s timeout — a cost and CI-hang hazard.

### D11 — Role/config drift and unused paths (Verified)
`paths.SIGNOFFS_PATH`, `discovery.quick_repo_scan`, `ConfigManager` are all defined and unused; `Orchestrator`'s `run_basic_repo_inspection` counts files instead of calling the scanner.

---

## 6. Security weaknesses

| # | Weakness | Evidence | Impact |
|---|---|---|---|
| S1 | **No permission scoping** — `--allowedTools` / `--permission-mode` never passed | `claude_runtime.py:259` argv | Behaviour depends on ambient CLI config; default headless mode blocks edits, so the harness silently "fails safe" today and would become permissive if the operator's `~/.claude` settings allow it |
| S2 | **No OS-level isolation** — no rlimits, no container, no namespace; the env whitelist (`:48`) still passes `HOME`, so `~/.claude`, `~/.gitconfig`, SSH agent sockets and the user's whole home are reachable | `:48`, `:259` | A prompt-injected agent inherits full user privileges. `--add-dir` only ever *widens* scope |
| S3 | **Containment is advisory text only** | the prompt instructs the agent to stay in the workspace; nothing enforces it | Any future "fix" that mounts the repo (`--add-dir REPO`) turns this into write access to production code with zero sandbox |
| S4 | **Unauthenticated approvals** | `approve`/`reject` are plain CLI subcommands; `SIGNOFFS_PATH` unused | Any local process/user that can run the CLI can approve its own candidate; no identity, no signature, no audit binding |
| S5 | **Unbounded real-LLM invocation from the default test suite** | D10 | Unreviewed spend and network egress on every `pytest tests`; also makes "tests pass" meaningless for the real runtime |
| S6 | **Evidence destroyed by design** | smoke test writes DB+logs into `TemporaryDirectory()` | No forensic trail; the project's only claimed real success cannot be audited (`reports/CLAUDE_RUNTIME_TEST_RESULTS.md`) |
| S7 | **Checkpoint self-inclusion** | D8 | Disk exhaustion (the tmpfs already filled once in this environment); archives may capture `state/engineering_team.db` with problem text |
| S8 | **Live DB polluted by tests** | D1 | Production state, IDs and `workflow.json` can be overwritten by running the test suite; 20 fake problems currently masquerade as real decisions |
| S9 | **Masked agent failure** | `execute_agent` returns `can_continue` to nobody; task stays `open` | A failing or hung agent is indistinguishable from "not yet started" in the workflow; TIMEOUTs are only visible by grepping logs |
| S10 | **Weak containment code is the only containment code** | D7 | False confidence if `agent_runtime` is ever adopted |

No secret material was found in the harness source; the runtime correctly passes `ANTHROPIC_*` through env rather than argv (avoids `ps` leakage). That one control is sound.

---

## 7. Test coverage gaps

Existing harness tests: `tests/test_claude_runtime.py` (20, of which 1 real-CLI), `tests/test_engineering_team.py` (6), `tests/test_engineering_team_workflow.py` (2), `tests/test_physical_monitoring.py` (40, covering the unused `physical/*` and dead `agent_runtime`). They are mock-heavy: 19/20 runtime tests use fake executables.

| Gap | Why it matters | Evidence that it is untested |
|---|---|---|
| No test asserts a real run **produces a file** in the workspace | This is the actual definition of "agent works"; every mock test passes with an agent that writes nothing | 14 workspaces, only `.writer.lock`; no such test exists |
| No test asserts the agent could **read** a source file | Would have caught D4 immediately | no test creates content in a workspace |
| Real-CLI smoke test accepts 5 statuses including FAILED/NOT_CONFIGURED | Cannot fail even when the runtime is broken | `test_real_claude_cli_smoke` assertion |
| Real-run evidence is written to a deleted temp dir | No retained proof, ever | `TemporaryDirectory()` in the smoke test |
| No isolation test for `Orchestrator`/DB binding | Would have caught D1, D2 | fixture monkeypatches `paths.DB_PATH` only |
| No test that a run advances task/state | D3 is invisible to the suite | `execute_agent` return value never asserted for DB side effects |
| No test for stale `running` rows / crash recovery | D-nothing exercises restart | no recovery code and no test |
| No test that benchmarks/tests are **executed** | The whole evidence chain is caller-supplied | `benchmark.record` tests pass dicts |
| No concurrent-run test | lock/vacuous-isolation issue unmeasured | one test pre-locks a workspace manually |
| `physical/` has 40 tests but is not wired to the orchestrator | High test count creates a false impression of coverage | no import from `harness/` into `physical/` |
| `agent_runtime` tested, `claude_runtime` is the production one | Coverage is on the wrong module | `test_physical_monitoring` imports `agent_runtime` |
| `real` marker unregistered / not deselected | Suite mutates real cost and produces warnings | `pyproject.toml` ini options |

---

## 8. Prioritized implementation roadmap

Ordered by "what unblocks an honest end-to-end demonstration fastest", not by elegance.

### P0 — make the harness safe to run and able to see code
1. **Kill the import-time DB binding.** `Orchestrator.__init__` should resolve `paths.DB_PATH` at call time (or accept an explicit `db_path` argument); same for `CheckpointManager`, `ReportGenerator`, `ClaudeCodeAdapter`. Acceptance: a test that patches `paths.DB_PATH` and asserts `orchestrator.db.path` is the temp file, plus zero rows added to the live DB by `pytest tests`.
2. **Quarantine the polluted state** (explicit human-approved ops step, out of scope for this audit): 20 demo problems, 21 open RCA tasks, 10 dangling checkpoints, and the `workflow.json` overwrite. Until then, no workflow claim from this DB is trustworthy.
3. **Deliver the project to the agent.** Seed each run workspace with tracked source (git worktree, or `rsync` of `guru/ scripts/ tests/ config/ pyproject.toml`) and keep the run dir under `state/agent_workspaces/`. Acceptance: an agent can read `guru/asr.py` from its cwd.
4. **Scope permissions explicitly.** Pass `--allowedTools` (Read, Edit, Write, Bash(pytest:*)) and `--permission-mode acceptEdits`; never `--dangerously-skip-permissions`. Keep the workspace as cwd, never add the repo root. Acceptance: a run can create a file, and cannot touch anything outside its workspace.
5. **Register `real` and default-deselect it.** Add `markers = ["real: billed/network tests"]` and `addopts = "-q -m 'not real'"`; gate the real test behind an env var.

### P1 — close the loop from run to evidence
6. **Make a run change state.** On `SUCCESS`: hash workspace artifacts, insert an artifact/evidence row, mark the task `done`, append a workflow event, and transition. On `FAILED`/`TIMEOUT`/`AUTH_REQUIRED`: mark the task `failed` with the reason. Acceptance: one CLI call moves a problem forward and the change is visible in `status`.
7. **Execute instead of accept.** Add `harness/test_runner.py` and `harness/bench_runner.py` that run `pytest`/benchmark scripts **in the workspace**, capture exit code, stdout/stderr digests and duration, and refuse to record a benchmark without a runner-produced result. Acceptance: `benchmarks` rows contain runner-produced stdout hashes, and a failing test blocks evaluation.
8. **Retain evidence.** Every real run writes its log and row into `engineering_team/logs` + the live DB (never a deleted temp dir). Add a `make prove-agent` target. Then re-run the claimed 9.2 s success and let it survive.
9. **Crash recovery.** On startup, reconcile `status='running'` rows older than the timeout to `FAILED (aborted)` and emit an event; add `resume-run <id>`.

### P2 — operational maturity
10. **One runtime, one provider interface.** Delete or fold `agent_runtime.py` into `claude_runtime`; put the LLM behind a small `AgentBackend` protocol so a local/OpenRouter backend can replace Claude without touching the orchestrator.
11. **Honour `team.toml`.** Read `max_concurrent_agents` / `sequential_execution` and either implement a bounded parallel scheduler with per-task workspaces or fail loudly that the config is unsupported.
12. **Real approvals.** Persist signoffs to `SIGNOFFS_PATH` with actor+timestamp+problem digest, and require one before `APPROVED`.
13. **Implement integration.** Apply the agent's diff to a branch, run the P1 test runner before/after, record to `integration_history`, provide rollback. Do not claim integration until a patch has been applied and reverted once.
14. **Reconcile `physical/`.** Either drive it from the orchestrator or mark it explicitly out of scope; its duplicate `decisions` schema will otherwise diverge from the harness's.
15. **CLI parity.** Add `new-problem`, `benchmark`, `evaluate`, `decision-report`, `cycle` subcommands so no workflow step requires a Python REPL.

### Definition of done for "the harness can do autonomous AI development"
One command, no Python REPL, no manual DB writes:
`problem → agent reads repo → agent edits workspace → pytest runs → benchmark runs → evaluation recorded → decision report written → human approves → diff applied and verified`.
Every one of those arrows is currently missing or unproven. Until then the honest description of `engineering_team/` is: **a well-schematised workflow metadata manager with an inert agent launcher and a large, mostly disconnected test suite.**
