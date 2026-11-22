# Engineering Team — Autonomous AI Engineering Harness (Offline Guru)

This package implements the `engineering_team/` autonomous engineering harness requested for Offline Guru.
It provides a persistent orchestration layer (workflow state machine, SQLite state DB with migrations, tasks/experiments/benchmarks/evaluations/decisions history, checkpoints/rollback, reporting, and a CLI) designed to coordinate a dual-track (original vs open-source) improvement workflow with a mandatory human decision gate.

> **Project protection:** Offline Guru's existing features and data must be preserved. No automatic merge to a protected main branch, no deletion of features without explicit authorization, no fabricated test/benchmark claims.

## Workspace layout

```
engineering_team/
  config/        team and policy configuration
  agents/        agent role definitions
  harness/       orchestrator, DB, state machine, managers, benchmark/evaluator
  state/         workflow.json and engineering_team.db (persistent)
  tasks/         task artifacts (supplementary)
  experiments/   experiment artifacts and worktrees
  benchmarks/    benchmark records/raw results
  reports/       DECISION_REPORT.md, INTEGRATION_REPORT.md, etc.
  checkpoints/   source snapshots for rollback
  logs/          harness logs
  evaluations/   independent evaluator outputs
  datasets/      benchmark datasets
```

## Requirements

- Python >= 3.11 (project currently runs on 3.14)
- SQLite (stdlib)
- No heavy dependencies required for core harness; benchmark execution may use project deps.

## CLI

Run from repository root:

```bash
python3 -m engineering_team start
python3 -m engineering_team status
python3 -m engineering_team pause
python3 -m engineering_team resume
python3 -m engineering_team stop
python3 -m engineering_team report
python3 -m engineering_team decisions
python3 -m engineering_team approve A
python3 -m engineering_team approve B
python3 -m engineering_team approve HYBRID
python3 -m engineering_team reject
python3 -m engineering_team request-tests [--note "..."]
```

Operational helpers:

```bash
python3 -m engineering_team inspect
python3 -m engineering_team problem --title "..." [--description "..."] [--severity P1]
python3 -m engineering_team checkpoint -m "label" [--kind manual]
python3 -m engineering_team integrate --dry-run
```

## Verify the harness

```bash
python3 engineering_team/scripts/demo_cycle.py            # full cycle in an isolated demo workspace
python3 engineering_team/scripts/demo_cycle.py --approve A # same, plus the approval/integration gate
.venv/bin/python -m pytest -q tests/test_engineering_team.py tests/test_engineering_team_workflow.py
```

The demo writes `state/demo/reports/DECISION_REPORT.md` and `DEMO_RUN.json`. Its numbers
are synthetic and labelled as such — it proves the plumbing, not Offline Guru quality.

## Workflow states

`IDLE, PROBLEM_DISCOVERED, ROOT_CAUSE_ANALYSIS, BASELINE_CREATED, DUAL_TRACK_RUNNING, BENCHMARKING, INDEPENDENT_EVALUATION, WAITING_FOR_HUMAN_DECISION, APPROVED, REJECTED, INTEGRATION, REGRESSION_TEST, PHYSICAL_TEST_REQUIRED, COMPLETED, FAILED, PAUSED`

- State transitions are validated (`harness/state_machine.py`).
- `INTEGRATION` is reachable only from `APPROVED` (explicit human approval gate). No auto-merge.
- `PAUSED` resumes to the previous state; state is persisted in `state/workflow.json` and events in SQLite.

## Persistence & recovery

- **SQLite DB:** `state/engineering_team.db` with explicit migrations (`harness/db.py`, `SCHEMA_VERSION`).
- **Workflow:** `state/workflow.json` (atomic writes) + `workflow_events` table.
- **History:** problems, tasks, experiments, benchmarks, evaluations, decisions, integration_history, agent_runs, checkpoints.
- Restart-safe: re-run `status`/`resume`; persisted state is reloaded.

## Checkpoints & rollback

```python
from engineering_team.harness.orchestrator import Orchestrator
o = Orchestrator()
o.create_checkpoint("before change", kind="manual")
# restore via CheckpointManager.restore(checkpoint_id, yes=True)
```

Snapshots are tarballs of declared source/config paths under `checkpoints/` with manifests/hashes. Rollback is explicit and creates a safety checkpoint before restoring.

## Dual-track and evaluation

- **Track A:** original engineering solution (isolated). **Track B:** evaluated/integrated open-source alternative (license, maintenance, hardware/offline fit verified from source where possible). Optional **HYBRID**.
- **Benchmarking:** `harness/benchmark.py` records config, environment, results, raw artifacts and computes deltas vs baseline.
- **Independent evaluation:** `harness/evaluator.py` compares recorded artifacts/evidence (not claims) and records limitations.
- **Decision report:** `reports/DECISION_REPORT.md` generated with problem, root cause, tracks, methodology/results/comparisons, risks, evaluator findings, recommendation, uncertainties, and supported human commands.

## Physical/human testing

When real-world validation is required (mic/speaker, 30 physical devices, hotspot RF, acoustics, thermal 45-min on target hardware), the team must produce a procedure, expected observations, and a result-submission format, then wait for human evidence. Physical tests are never fabricated; simulated vs physical results must be distinguished.

## Security and safety

- Treat external repos/code as untrusted; inspect deps before install; no unreviewed remote script execution.
- No secrets/credentials committed.
- Isolated worktrees/snapshots; protected baseline; audit trail in DB/logs.
- Human approval required for irreversible integration operations.

## Known limitations

- Agent "labour" (deep research, coding, long benchmarks) depends on an execution runtime/handlers; the core harness records and coordinates it but cannot claim external work it did not run.
- This environment is not the target classroom hardware and has no microphone/30 physical devices; physical/RF/thermal validation remains `PHYSICAL_TEST_REQUIRED` evidence from humans.
- Git may be unavailable in this workspace; `git_manager` falls back to snapshot-based isolation and reports the limitation.

See `docs/` in this package for architecture and security details.
