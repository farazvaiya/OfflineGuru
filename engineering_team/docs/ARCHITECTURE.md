# Engineering Team Harness — Architecture

## Goals
Provide a persistent, auditable, human-gated engineering workflow for improving Offline Guru:
inspect → discover → root-cause → baseline → dual-track (A original / B open-source) → benchmark → independent evaluation → human decision → integrate approved → regression/verification → next cycle.

## Components

### 1. Persistence layer
- **SQLite (`state/engineering_team.db`)**: problems, tasks, experiments, benchmarks, evaluations, decisions, integration_history, agent_runs, checkpoints, workflow_events. Schema managed by explicit migrations (`harness/db.py`).
- **Workflow state (`state/workflow.json`)**: validated state machine with atomic writes and transition history for fast resume.
- **Filesystem artifacts**: reports, benchmark/eval JSON, checkpoints (tar.gz + manifest), logs, datasets.

### 2. State machine (`harness/state_machine.py`)
States: `IDLE, PROBLEM_DISCOVERED, ROOT_CAUSE_ANALYSIS, BASELINE_CREATED, DUAL_TRACK_RUNNING, BENCHMARKING, INDEPENDENT_EVALUATION, WAITING_FOR_HUMAN_DECISION, APPROVED, REJECTED, INTEGRATION, REGRESSION_TEST, PHYSICAL_TEST_REQUIRED, COMPLETED, FAILED, PAUSED`.
- Validated transitions only; no silent skipping of approval/evaluation gates.
- **Approval gate:** `INTEGRATION` allowed only from `APPROVED`.
- **Pause/Resume:** `PAUSED` stores previous state and resumes to it.

### 3. Orchestrator (`harness/orchestrator.py`)
Chief coordinator: loads state/DB, assigns/records tasks, manages checkpoints, prepares decision/integration reports, enforces decision-state rules, records workflow events and agent runs. Sequential by default for 7GB RAM target.

### 4. Managers
- **TaskManager:** task CRUD/status in DB.
- **ExperimentManager:** Track A/B/HYBRID experiments, branches/worktrees, implementation/test records.
- **CheckpointManager:** tarball snapshots with manifests/hashes; explicit rollback with safety checkpoint.
- **GitManager:** branch/worktree helpers; detects non-git repos and falls back to snapshot isolation.
- **BenchmarkEngine:** records configs, environment, results, raw artifacts; computes metric deltas.
- **IndependentEvaluator:** compares recorded evidence across tracks/baseline, records limitations and verdicts (not trust-on-claim).
- **ClaudeCodeAdapter:** pluggable execution/recording interface for agent labor.

### 5. Reporting (`harness/report.py`)
- `DECISION_REPORT.md`: problem, root cause, Track A/B/(Hybrid), methodology, results (accuracy/latency/CPU-RAM/reliability), risks, evaluator findings, recommendation, uncertainties, human commands.
- `INTEGRATION_REPORT.md`: integration outcome, checkpoint, verification/regression status, next steps/failures.

### 6. CLI (`python -m engineering_team`)
Commands: `start/status/pause/resume/stop/report/decisions/approve A|B|HYBRID/reject/request-tests` plus operational helpers (`inspect`, `problem`, `checkpoint`, `integrate --dry-run`). All validate current workflow state where applicable.

### 7. Agents (`agents/AGENTS.md`, `config/team.toml`)
Nine roles: Chief Orchestrator, Problem Discovery, Root Cause Analysis, Track A Original, Track B Open-source Research, Integration Engineer, Benchmark Engineer, Independent Evaluator, QA/Regression. Config encodes constraints (i5-7300U/7GB/no GPU/offline/30 students/bn↔en) and safety policy.

## Data flow (per problem cycle)
1. Discovery records problem+evidence → `PROBLEM_DISCOVERED`.
2. RCA records diagnosis → `ROOT_CAUSE_ANALYSIS` → baseline captured → `BASELINE_CREATED`.
3. Track A/B experiments in isolation → `DUAL_TRACK_RUNNING`.
4. Benchmarks (equivalent conditions) → `BENCHMARKING`.
5. Independent evaluation → `INDEPENDENT_EVALUATION`.
6. Decision report generated → `WAITING_FOR_HUMAN_DECISION` (or `PHYSICAL_TEST_REQUIRED` if human evidence needed).
7. Human: approve/reject/request-tests. Approve → `APPROVED` → checkpoint → `INTEGRATION` → `REGRESSION_TEST` → `COMPLETED` (new baseline) or `FAILED` with rollback.

## Safety invariants
- No integration without explicit human approval.
- No fabricated physical tests or unrun test claims.
- Checkpoints before significant/integration changes; rollback path preserved.
- External code treated as untrusted; no secret commits.
- Sequential/low-concurrency execution on constrained hardware.
