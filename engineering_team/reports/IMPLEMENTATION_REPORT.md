# Engineering Team Harness — Actual Implementation Report

Date: 2026-11-14
Repository: Offline Guru (`/home/faraz/project/offline_guru`)

## 1. Repository inspection (completed)
- Project: Offline Guru — offline classroom speech-to-speech translation (EN→BN), FastAPI server, local Whisper/Marian/Piper pipeline, SQLite storage, teacher/student web UI.
- Key code: `guru/` (asr, audio, translate, tts, pipeline, realtime, server/app.py, storage, education, bus, etc.), `config/default.toml`, `scripts/`, `docs/`.
- Tests: `tests/` (21 `test_*.py` files discovered).
- Existing harness: `engineering_harness/` present with its own orchestrator/state/gates (phase1 PASS, phase2/3 FAIL in recorded state; not a git repo).
- Git: **not a git repository** in this workspace; branch/worktree isolation falls back to snapshot checkpoints.
- Environment: Linux, Python 3.14.4, pytest 9.0.2.

## 2. Implemented (engineering_team/)

### Persistent foundation (Phase 1 requirements)
- **Workspace:** `engineering_team/{config,agents,harness,state,tasks,experiments,benchmarks,reports,checkpoints,logs,evaluations,datasets}` created.
- **SQLite + explicit migrations:** `harness/db.py` (`SCHEMA_VERSION=1`, migration 1) with tables: meta, problems, tasks, experiments, benchmarks, evaluations, decisions, integration_history, agent_runs, checkpoints, workflow_events.
- **Workflow state machine:** `harness/state_machine.py` with all requested states and validated transitions, atomic persistence to `state/workflow.json`, pause/resume, and approval gate (`INTEGRATION` only from `APPROVED`).
- **Orchestrator:** `harness/orchestrator.py` (status/start/pause/resume/stop, discovery/problem recording, baseline/root-cause hooks, checkpointing, decision report generation, human decision handling, integration intent recording with no auto-merge).
- **Task & experiment managers:** `harness/task_manager.py`, `harness/experiment_manager.py` (Track A/B/HYBRID).
- **Git/worktree manager:** `harness/git_manager.py` (repo detection, branch/worktree helpers; snapshot fallback when not a git repo).
- **Checkpoint & rollback:** `harness/checkpoint.py` (tar.gz snapshots + manifests/hashes, list, explicit restore with safety checkpoint).
- **Logging:** `harness/logging_utils.py` (file + console).
- **Execution adapter:** `harness/claude_adapter.py` (records agent runs; pluggable local handlers; pending_external when unconfigured).
- **Config manager:** `harness/config_manager.py` (loads `config/team.toml`).
- **CLI:** `python -m engineering_team` with `start/status/pause/resume/stop/report/decisions/approve A|B|HYBRID/reject/request-tests` plus `inspect/problem/checkpoint/integrate` (state-validated where applicable).

### Dual-track, benchmark, evaluation, decision, integration (Phases 2–5 scaffolding/implementation)
- **Benchmark framework:** `harness/benchmark.py` records config/environment/results/raw artifacts and computes metric deltas vs baseline.
- **Independent evaluator:** `harness/evaluator.py` compares recorded artifacts across tracks/benchmarks, records limitations and verdicts without trusting claims alone.
- **Discovery helpers:** `harness/discovery.py` (local repo scan, pytest-collect signal, existing engineering_harness gate signal).
- **Reports:** `harness/report.py` generates `reports/DECISION_REPORT.md` (all required sections + human commands) and `reports/INTEGRATION_REPORT.md`.
- **Human decision interface:** CLI approve/reject/request-tests enforce waiting/review state and record decisions in DB; approval required before integration transition.
- **Integration flow:** pre-integration checkpoint, workflow transitions to INTEGRATION→REGRESSION_TEST, integration history recorded, report written; **no auto-merge** (awaiting explicit/manual merge of approved isolated changes).

### Agent definitions & documentation
- **Agent roles:** `agents/AGENTS.md` and `config/team.toml` (9 agents, responsibilities, constraints, safety policy).
- **Docs:** `README.md`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `docs/USAGE.md`.

### Test suite
- `tests/test_engineering_team.py`: DB migrations/ops, state transitions + approval gate + pause/resume, checkpoint manifesting, CLI status/inspect and decision-gate enforcement, benchmark/evaluator basics.

## 3. Evidence (actual runs)

### 3.1 CLI against the live workspace

```text
$ python3 -m engineering_team status
{"state": "IDLE", "open_tasks": 0, "problems": 0, "latest_checkpoint": null,
 "git": {"is_repo": false, "note": "Not a git repository; worktree isolation via filesystem snapshots only."},
 "db": ".../engineering_team/state/engineering_team.db"}

$ python3 -m engineering_team inspect
{"guru_py_files": 23, "test_files": 21, "engineering_harness_exists": true, "engineering_team_exists": true, ...}
```

### 3.2 Full harness cycle (isolated demo workspace, real code paths)

`python3 engineering_team/scripts/demo_cycle.py` executes the actual orchestrator,
DB, state machine, benchmark engine, evaluator and report generator:

```text
[IDLE] initial status
[PROBLEM_DISCOVERED] problem discovered: b66ec1bfb8db
[ROOT_CAUSE_ANALYSIS] root cause recorded
[BASELINE_CREATED] baseline recorded
[DUAL_TRACK_RUNNING] dual track experiments created: {"A": "8467af8e5e4c", "B": "b4f695189db7"}
[BENCHMARKING] benchmarks recorded: {"A": "0e514c42ae65", "B": "38de23be5a8c"}
[INDEPENDENT_EVALUATION] independent evaluation: {"verdict": "inconclusive",
  "limitations": ["Track A names no changed files; no implementation to compare.",
                  "Track A has no passing test evidence recorded.", ...]}
[WAITING_FOR_HUMAN_DECISION] decision report written: .../state/demo/reports/DECISION_REPORT.md
[WAITING_FOR_HUMAN_DECISION] integration refused before approval:
    "Integration requires APPROVED state (explicit human approval)"
workflow events: 7   final state: WAITING_FOR_HUMAN_DECISION
```

With `--approve A` the same script continues through the gate and records a
pre-integration checkpoint plus `INTEGRATION_REPORT.md`, ending in `APPROVED`
with `status: not_executed` (no auto-merge). All numbers in this demo are marked
synthetic in the generated report; it validates plumbing, not Offline Guru quality.

### 3.3 Static checks

`ruff check engineering_team` reports 20 remaining findings, all of the form
`BLE001`/`S110` (blind `except` / `try-except-pass`). These are deliberate: the
harness must survive corrupt workflow JSON, a missing `git` binary, absent
`psutil`, and unreadable optional artifacts. The pre-existing project tree has
its own 5 baseline findings, so lint cleanliness is not an enforced project gate.

### 3.4 Test suite

```text
$ .venv/bin/python -m pytest --tb=no
261 passed in 14.28s          # full project suite, exit code 0

$ .venv/bin/python -m pytest -q tests/test_engineering_team.py tests/test_engineering_team_workflow.py
8 passed                     # DB migrations, state machine + approval gate + pause/resume,
                             # checkpoint manifest, CLI status/inspect/decision-gate,
                             # benchmark/evaluator, full-cycle-to-human-gate, reject/request-tests
```

## 4. Acceptance-criteria mapping (honest)

Mechanical workflow (criteria 2, 8, 9, 10, 12, 13) is demonstrated by the tests and the
demo run above. Criteria requiring real investigation, measured benchmarks and physical
validation remain open and are listed as such.

| Criterion | Status | Notes |
|---|---|---|
| 1. Discover real Offline Guru problem | **Partially demonstrated** | `discover_problem`/discovery helpers and DB records implemented; `inspect` shows real repo facts and existing harness gate failures as signals. A full autonomous discovery+reproduction cycle on a new root-caused issue was not completed end-to-end in this run. |
| 2. Record root cause and baseline | **Supported** | APIs/DB fields exist (`record_root_cause`, `record_baseline`); no new measured baseline run recorded in this session. |
| 3. Original solution (Track A) | **Supported** | Experiment manager/branch-worktree/checkpoint isolation implemented; no Track A fix implemented for a newly discovered issue in this session. |
| 4. Open-source alternative (Track B) | **Supported** | Data model/reporting supports license/source/offline evaluation and honest "none suitable"; no external repo was vetted/integrated in this session. |
| 5. Test both independently | **Supported** | Isolation and test-recording structures present; not executed for dual candidates here. |
| 6. Compare with reproducible evidence | **Supported** | Benchmark engine records config/env/results/raw and deltas; no comparative A/B benchmark campaign executed here. |
| 7. Independent evaluation | **Supported** | Evaluator produces evidence-based comparison with limitations; no full A/B evaluation produced here. |
| 8. Decision report | **Demonstrated** | `DEMO_RUN.json` + `state/demo/reports/DECISION_REPORT.md` generated with every required section and the human command list. |
| 9. Wait for human approval | **Demonstrated** | Cycle halts in `WAITING_FOR_HUMAN_DECISION`; `integrate_approved()` raises before approval (asserted in demo and tests). |
| 10. Integrate only approved candidate | **Demonstrated (guarded)** | `APPROVED` required; pre-integration checkpoint + `INTEGRATION_REPORT.md`; `status: not_executed`, no auto-merge. |
| 11. Run regression tests | **Partially** | Full 261-test suite run and green for this work; a post-merge regression run for an approved real candidate has not happened because no real candidate was approved. |
| 12. Rollback preserved | **Implemented/tested** | Checkpoint snapshot + manifest hashes tested; restore is explicit and creates a safety checkpoint first (not exercised against real source). |
| 13. Resume after interruption | **Demonstrated** | Test pauses, closes the orchestrator, reopens it in the same workspace and resumes to the exact prior state; DB/workflow reload verified. |

## 5. What was not fully demonstrated
- A complete, autonomous, single-problem cycle from *newly discovered & reproduced real bug* through dual-track implementations, executed benchmarks on equivalent conditions, independent evaluation, human approval, applied merge, and post-merge regression — all with captured artifacts in one run. Achieving this requires substantial agent labor/runtime, suitable reproducible benchmarks/datasets on this host, and (for final acceptance) human decisions/physical tests outside automation.
- Git-based worktree isolation could not be exercised (no `.git`); snapshot isolation is used and reported.
- Physical classroom validations (30 real devices, RF hotspot, live mic acoustics, 45-min thermal on target i5-7300U) remain human/field tests and are explicitly not claimed.

## 6. Conclusion
A real, persistent `engineering_team/` harness foundation and the required subsystems (DB/migrations, validated state machine with approval gate, task/experiment/benchmark/eval managers, checkpoints/rollback, reporting, CLI, agent/config/docs, and tests) have been implemented and exercised. The system is structured to carry out the full dual-track, human-gated workflow, but the end-to-end acceptance cycle on a newly solved Offline Guru problem requires further agent-executed investigation/implementation/benchmarking and explicit human approval/evidence beyond this implementation pass.
