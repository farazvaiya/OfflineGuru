# Engineering Team Agents

This document defines the specialized agents in the Offline Guru Autonomous AI Engineering Harness.
Agents are roles with explicit inputs/outputs; execution may be performed by a human, an external coding agent runtime, or registered local handlers via `harness.claude_adapter.ClaudeCodeAdapter`. Every run is recorded in `state/engineering_team.db` (`agent_runs`).

## A. Chief Engineering Orchestrator
- **Responsibilities:** manage workflow/state machine, assign and track tasks, prevent conflicts, persist progress, resume after interruption, prepare final/decision reports.
- **Primary tools:** `harness.orchestrator.Orchestrator`, `harness.state_machine`, DB/task/experiment managers, checkpoints.
- **Outputs:** workflow transitions, task assignments, decision/integration reports.

## B. Problem Discovery Agent
- **Responsibilities:** inspect source, logs, tests, benchmarks; find bugs/perf/accuracy/architecture issues; reproduce; record evidence; assign severity/priority.
- **Inputs:** repo tree, test results, logs, metrics, existing reports.
- **Outputs:** `problems` records with evidence and severity, discovery tasks.

## C. Root Cause Analysis Agent
- **Responsibilities:** investigate causes (not symptoms), examine code paths/deps, compare expected vs actual, produce reproducible diagnosis.
- **Rule:** do not implement a fix before root cause is sufficiently understood.
- **Outputs:** `root_cause`, evidence, hypotheses/experiments.

## D. Original Solution Agent — Track A
- **Responsibilities:** design and implement an original engineering solution in an isolated worktree/branch; add tests; record decisions; optimize for i5-7300U/7GB/no-GPU/offline constraints.
- **Constraint:** do not depend on an external GitHub project as the primary solution.
- **Outputs:** experiment (track A), implementation notes, test results.

## E. Open-source Research Agent — Track B
- **Responsibilities:** search/evaluate relevant OSS projects (source, docs, license, maintenance, issues, CPU/RAM, offline fit); test suitability; prepare integration proposal.
- **Rules:** never recommend from README/popularity alone; honestly report if no suitable project exists.
- **Outputs:** experiment (track B) with license/compatibility analysis and integration plan.

## F. Integration Engineer
- **Responsibilities:** integrate candidates into isolated experimental branches/worktrees, resolve deps, preserve interfaces, document complexity, avoid unnecessary rewrites.
- **Outputs:** integrated candidate branches/worktrees and notes.

## G. Benchmark Engineer
- **Responsibilities:** reproducible benchmarks under equivalent conditions (same dataset/tests/hardware/config/methods); record latency/accuracy/CPU/RAM/reliability; compare with baseline; save raw results/configs.
- **Outputs:** benchmark records/artifacts.

## H. Independent Evaluator
- **Responsibilities:** independently verify evidence, inspect tests, ensure fair comparison, detect misleading metrics/regressions, evaluate maintainability/compatibility, produce neutral comparison.
- **Rule:** must not simply accept implementation claims.
- **Outputs:** evaluation records and decision-report inputs.

## I. QA and Regression Agent
- **Responsibilities:** run unit/integration/e2e tests, check existing Offline Guru features and API compatibility, detect regressions, verify selected solution improves baseline.
- **Outputs:** QA results, regression reports, integration verification.

## Coordination & safety
- Low concurrency (default sequential) for 7GB RAM target.
- Isolated worktrees/checkpoints; no auto-merge; human approval required before protected integration.
- Physical/user-observed tests are requested via structured procedures and recorded as evidence; never fabricated.
