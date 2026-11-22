# Remediation Phase Report — P0

Date: 2026-11-17
Basis: `reports/DEEP_RUNTIME_AUDIT.md` (D1–D14) and `reports/REMEDIATION_BASELINE.md`.
Scope executed: Phase 0 baseline, P0.1 (DB binding), P0.2 (source access), P0.3 (permission boundary).
Constraint kept throughout: **no real LLM invocation**, no production data deletion, no production DB/state modification.

## 1. Result in one paragraph

The three P0 defects are fixed and regression-tested. State paths are resolved at call time, so a relocated or test workspace can no longer write to, or archive, the production database or the live log/workspace tree. Agents now receive a per-run, allowlisted source snapshot (`source/`), a dedicated artifact directory (`artifacts/`), and (for read-only roles) a frozen snapshot enforced by filesystem permissions rather than prompt text. Every run now carries explicit `--permission-mode` / `--allowedTools` for its role, a sanitized environment with a private `HOME`, and an OS-level `bwrap` sandbox that makes the real project tree and the user's home invisible while keeping only the workspace writable. `@pytest.mark.real` is registered and excluded by default, so the ordinary suite can no longer spend money or hang on the network.

**Verification: `pytest tests` → 367 passed, exit 0.** Production DB counts and workflow state are identical before and after the work (problems 21, tasks 21, decisions 30, checkpoints 10, agent_runs 0, workflow_events 221, state `PROBLEM_DISCOVERED`); live workspace count (15) and run-log count (168) did not grow after the fixes. No real agent execution is claimed.

## 2. P0.1 — Database path binding (D1, D2, D12)

### What was wrong
`orchestrator.py` imported `DB_PATH` by value at module import, so any later relocation (test fixture, scratch workspace, a different `ENGINEERING_TEAM_ROOT`) was ignored. The workflow test therefore wrote 20 demo problems, 30 decisions, 221 events and 10 `/tmp` checkpoint rows into the live DB. `claude_runtime` had the same class of bug for `workspace_root`/`log_dir` (D12), so even an isolated run leaked logs and workspaces into the live tree. Checkpoints also archived `engineering_team/state`, `checkpoints/` and `logs/` (D8), which both bloated archives and captured the live database.

### What changed
| File | Change |
|---|---|
| `harness/paths.py` | Added call-time resolvers: `team_root()`, `db_path()`, `workflow_path()`, `decisions_path()`, `signoffs_path()`, `log_path()`, `decision_report_path()`, `integration_report_path()`, `agent_workspace_root()`, `agent_log_dir()`, `agents_md_path()`. Import-time constants kept only as deprecated aliases. |
| `harness/orchestrator.py` | Removed `from .paths import DB_PATH`; `__init__` now calls `paths.db_path()` / `paths.workflow_path()` at construction and `paths.ensure_workspace()`. |
| `harness/claude_runtime.py` | `workspace_root` / `log_dir` / `agents_md` are properties resolved on use; role definitions stay with the package (source), state follows `TEAM_ROOT`. |
| `harness/report.py` | Default report paths resolve at call time (`out_path=None`). |
| `harness/logging_utils.py` | File handler rebinds when the resolved log path changes (no appending to the previous tree). |
| `__main__.py` | `report` resolves `paths.decision_report_path()` per invocation. |
| `harness/checkpoint.py` | New `EXCLUDED_PREFIXES` / `_is_excluded`: `engineering_team/{state,checkpoints,logs}`, `.tmp`, caches. Applied to both the tarball filter and the manifest walk. |

### Evidence
- `tests/test_harness_isolation.py` (6 tests): imports the orchestrator **before** patching (reproducing the old collection order), then proves the patched path is used, that a full discover/RCA cycle adds zero rows to the live DB, that agent runs place workspace + log under `TEAM_ROOT`, that report defaults follow `TEAM_ROOT`, that checkpoints exclude state, and that a CLI subprocess with `ENGINEERING_TEAM_ROOT` reports the relocated DB/report paths.
- Decisive regression proved manually: `pytest tests/test_engineering_team_workflow.py` **with no isolation env** → 2 passed and live DB counts unchanged (`LIVE_DB_UNCHANGED`, diff empty). This is the exact run that previously created the 20 demo problems.
- Live DB check after the full suite: unchanged counts, `agent_runs 0`, workflow still `PROBLEM_DISCOVERED`.

**Classification: Verified fixed.** D1, D2's cause, D8 and D12 are closed. The already-polluted rows themselves are **not** deleted (destructive; requires explicit human approval) — they are now inert rather than growing.

## 3. P0.2 — Real source access with isolation (D4)

### What was wrong
`_workspace()` created an empty directory and the repository was never copied or mounted. Every role was blind: 15 workspaces contained only `.writer.lock`, and 0 artifacts had ever been produced.

### What changed
New `harness/workspace.py`:

- `SOURCE_ALLOWLIST` = `guru`, `scripts`, `tests`, `config`, `docs`, `engineering_team`, `pyproject.toml`, `requirements*.txt`, `README.md`, `Makefile`, `LICENSES.md`.
- `SKIP_DIR_NAMES` removes `.git`, `.venv`, `state`, `logs`, `checkpoints`, `data`, `models`, `__pycache__`, `.tmp`, `node_modules`, etc.; files > 2 MiB and `.db/.onnx/.bin/.log` are skipped, symlinks are not followed.
- Per run: `ws/source/` (snapshot), `ws/artifacts/` (deliverables), `ws/.workspace.json` (mode, source digest, per-file SHA-256), `.writer.lock`.
- Modes: `inspect` freezes `source/` (files `0444`, dirs `0555`) so read-only roles cannot edit even if a tool were allowed; `edit` gives implementation/test roles a writable **copy**. Selected per role (`READ_ONLY_ROLES`), overridable via `workspace_mode=` or `TEAM_WORKSPACE_MODE`.
- UI/CLI: `run-agent --workspace-mode {inspect,edit}`; run output now includes `workspace_mode`, `source_snapshot` (source dir, digest, file count) and `source_changes` (added/modified/deleted vs the seeded hashes).
- The three zones are explicit: `source/` (snapshot), `artifacts/` (candidate output), real checkout (never mounted; integration stays manual/approved).

### Evidence
`tests/test_harness_workspace.py` (5 tests):
1. snapshot contains `guru/asr.py`, `tests/conftest.py`, `pyproject.toml`, harness source; excludes `engineering_team/state|logs|checkpoints`, `.venv`, `models`; metadata digest matches the tree;
2. editing a copy leaves the real `guru/asr.py` hash unchanged and is reported as `modified`;
3. `inspect` mode raises `PermissionError` on write attempts to source (artifact dir stays writable);
4. track A and track B copies are independent;
5. through the orchestrator, a fake agent reads `source/guru/asr.py`, writes `artifacts/proof.json` (read_source=true, bytes>0), `source_changes == []`, and the snapshot metadata matches the DB row.

**Classification: Verified fixed.** An agent can now read the intended source; candidate edits cannot reach the real tree; artifacts are retained and traceable on disk and in `agent_runs.output_json`.

## 4. P0.3 — Permission boundary (D6, D7, S1–S3, S10)

### What was wrong
`claude_runtime` passed no `--allowedTools` and no `--permission-mode`; containment was prompt text; `HOME` was inherited (SSH keys, dotfiles); `agent_runtime._safe_args` used a string prefix check (`/tmp/ws_evil` passed for `/tmp/ws`); two runtimes competed and the safer one was dead code.

### What changed
New `harness/permissions.py` — the single boundary:

1. **Role tool policy** (`ROLE_POLICIES`): read-only roles (`chief-orchestrator`, `problem-discovery`, `root-cause-analysis`, `opensource-research-track-b`, `independent-evaluator`, plus AGENTS.md letters A/B/C/H) get `Read,Grep,Glob` in `plan` mode; implementation roles get `Edit,Write` in `acceptEdits`; test/benchmark roles additionally get `Bash(.venv/bin/python -m pytest:*)`. Unknown roles default to least privilege. No code path can emit `--dangerously-skip-permissions`.
2. **Environment** (`build_agent_env`): explicit allowlist (`PATH`, locale, `ANTHROPIC_*`, Bedrock/Vertex flags); deny-prefixes for `AWS_/GCP_/AZURE_/SSH_/GITHUB_/GH_/DOCKER_/DATABASE_/TEAM_/OPENAI_/HF_` etc.; `HOME` is always a private `ws/.home` with `CLAUDE_CONFIG_DIR=ws/.home/.claude`, so user credentials and dotfiles are not the agent's home.
3. **Filesystem sandbox** (`plan_sandbox`): when `bwrap` is present, the agent runs in a user namespace with `--unshare-all`, `/home` replaced by an empty tmpfs (only the CLI install prefix is re-bound read-only), `/usr,/bin,/sbin,/lib,/lib64,/etc` read-only, `/tmp` fresh, and **only the workspace writable**. Modes: `auto` (default; used when the probe passes, otherwise recorded as disabled), `required` (refuse with `NOT_CONFIGURED` if unavailable), `off` (explicit, recorded). Every run output now carries `sandbox` and `permissions` objects.
4. **One containment primitive** (`check_within`): `Path.resolve()` + `is_relative_to`, used by the generic runtime too. Sibling-prefix and symlink escapes are rejected.

### Evidence
`tests/test_harness_permissions.py` (12 tests):
- read-only roles have no Write/Edit and `plan`; implementing roles have them; unknown role is read-only; no bypass flag for any role;
- env: `AWS_SECRET_ACCESS_KEY`, `GITHUB_TOKEN`, `DATABASE_URL`, `SSH_AUTH_SOCK`, `OPENAI_API_KEY`, `TEAM_*` are absent; `ANTHROPIC_API_KEY` present; `HOME` is `ws/.home`;
- containment: sibling `ws_evil` rejected, symlink to outside rejected, inside path accepted; `agent_runtime._safe_args` rejects the sibling prefix (bug fixed);
- sandbox: probe hides `~/.ssh`, workspace is writable, `HOME` is inside the workspace; `required` mode end-to-end through `Orchestrator.execute_agent` succeeds with `sandbox.enabled is True` and `filesystem == "bwrap"`;
- end-to-end argv: `--permission-mode plan`, `--allowedTools` contains `Read`, not `Write`, and never `--dangerously-skip-permissions`.

Independent manual check: the real `/home/faraz/.npm-global/bin/claude --version` runs inside the sandbox (2.1.326) while `~/.ssh` and the project tree are not visible. **No prompt was sent.**

**Classification: Verified fixed** for tool scoping, env sanitization, containment and sandbox mechanics. **Not verified**: an actual agent completing a task under these permissions (would require a billed/networked LLM run).

## 5. Additional defect closed

**D10** — `pyproject.toml` now registers the `real` marker and sets `addopts = "-q -m 'not real'"`. The ordinary suite no longer invokes the CLI; `pytest -m real` is the explicit opt-in. This also satisfies mission rule 8.

```
367 passed, exit 0
```

(367 = previous 345 + 6 isolation + 5 workspace + 12 permissions − 1 real test now deselected.)

## 6. Decisions and rationale

| Decision | Why |
|---|---|
| Call-time resolvers instead of passing paths through every constructor | Smallest change that fixes the whole class of bug (DB, workflow, reports, logs, workspaces, checkpoints) and keeps existing APIs working. |
| Copy an allowlist rather than mount the repo or use git worktrees | There is no git repository; a worktree is impossible. A filtered copy also cannot expose the live DB, models, venv or checkpoint archives. |
| Freeze `source/` for read-only roles with chmod | Makes "read-only" enforced by the kernel, not by prompt text; artifacts remain writable in the same run. |
| Keep `agent_runtime.py` but mark it deprecated and route its containment through `check_within` | Deleting it would break 40 physical-test tests; sharing the primitive removes the divergent (buggy) implementation while leaving one documented agent runtime. |
| `bwrap` with `auto` default, `required` available | The boundary should be real where the kernel allows it; the mode is always recorded, so a downgrade is visible rather than silent, and CI can demand `required`. |
| Private `HOME` instead of inheriting it | Credentials must come from `ANTHROPIC_*` env vars; user dotfiles and SSH keys are not agent state. This may turn a subscription-auth run into `AUTH_REQUIRED` — an honest failure, not a weakened control. |
| Do not delete the 20 demo problems / 10 dangling checkpoints | Destructive; mission rules 4–5 and the authorization boundary require explicit human approval. They are inert now. |

## 7. Files touched

Changed: `engineering_team/harness/{paths,orchestrator,claude_runtime,report,logging_utils,checkpoint,agent_runtime}.py`, `engineering_team/__main__.py`, `pyproject.toml`.
Added: `engineering_team/harness/{workspace,permissions}.py`, `tests/test_harness_isolation.py`, `tests/test_harness_workspace.py`, `tests/test_harness_permissions.py`, this report and `REMEDIATION_BASELINE.md`.

## 8. Remaining risks and limitations

1. **No real agent execution was performed or claimed.** The runtime, sandbox and permissions are exercised with fake CLIs only; the one authenticated path (`claude --version` inside bwrap) proves the sandbox is compatible with the binary, not that an agent can finish a task.
2. **bwrap is a host dependency.** Without it, `auto` runs unsandboxed and records `sandbox.enabled=false`; only `required` refuses. Windows has no equivalent here.
3. **Integration remains unimplemented.** `source_changes` now provides a reviewable candidate diff, but nothing applies it; `integrate_approved` still records intent only.
4. **Tasks still do not transition after a run** (D3). A successful run leaves the task `open`; that is P1 work, not P0.
5. **Polluted production data still exists** (20 demo problems, 21 open tasks, 10 dangling checkpoints, `workflow.json` pointing at `PROBLEM_DISCOVERED`). Cleanup needs explicit approval.
6. **`physical/` remains a parallel subsystem** with its own DB and duplicate decision schema.
7. The pasted mission brief was truncated inside P0.3 ("…unrelated project"), so P0.3 was implemented to that evident intent (credentials, env, user files, unrelated project paths); anything that followed in the brief was not available and is not claimed.

## 9. Suggested next step (not started)

P1: make a run advance state — on `SUCCESS` mark the task done, persist the artifact/`source_changes` evidence, emit a workflow event; on `FAILED/TIMEOUT/AUTH_REQUIRED` mark it failed with the reason. Then add a real test/benchmark runner that executes in the workspace and refuses to record caller-supplied numbers.
