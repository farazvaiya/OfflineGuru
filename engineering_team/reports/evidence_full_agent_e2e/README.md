# Evidence: controlled full-team harness run (SIMULATED)

**NOT a real LLM run.** These artifacts come from a disposable `engineering_team` workspace executed on 2026-11-17 with a **fake `claude` executable** that printed `{"type":"result","result":"simulated agent output"}` and wrote `artifacts/agent_note.txt`. No paid/network-backed model inference was performed.

Origin: `/tmp/et_full_verify_j40uygs2/engineering_team_state` (`ENGINEERING_TEAM_ROOT`), copied here so the evidence survives temp cleanup.

## Contents

| Path | What it shows |
|---|---|
| `engineering_team.db` | 3 `agent_runs` (solution-track-a, opensource-research-track-b, qa-regression, all SUCCESS), 4 tasks (all still `open`), 1 problem, 2 experiments, 2 benchmarks, 1 evaluation, 10 `workflow_events` |
| `workflow.json` | Final workflow state `WAITING_FOR_HUMAN_DECISION` with full transition history |
| `DECISION_REPORT.md` | Generated decision report (evaluator verdict `inconclusive`; limitations recorded) |
| `harness.log` | Transition log for the run |
| `agent_runs/*.log` | Per-run execution records: workspace paths, `role_source`, permission set, workspace mode, source snapshot digest/file count, artifacts, `sandbox.mode=off` |
| `workspaces/*/artifacts/agent_note.txt` | Artifacts produced inside the isolated Track A, Track B and shared QA workspaces |

## Key inferences

- Harness execution path (orchestrator → adapter → runtime → workspace → DB/logs/artifacts) works when a runtime executable is supplied.
- Role policy differentiation is enforced: Track A = `edit` + Edit/Write; Track B = `inspect` + read-only tools; QA = `edit` + constrained pytest Bash.
- Track A and Track B received separate workspace roots.
- Agent SUCCESS did **not** complete the tasks, and no downstream phase consumed the agent artifacts — downstream benchmark/evaluation/report steps used programmatically recorded data.
