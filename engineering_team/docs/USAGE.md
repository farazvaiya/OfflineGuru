# Usage Guide

## Installation
No separate install step beyond the repository's Python environment. Run commands from the Offline Guru repo root:

```bash
python3 -m engineering_team status
```

The harness creates its workspace directories and `state/engineering_team.db` (with migrations) on first use.

## Basic workflow

1. **Inspect repository**
   ```bash
   python3 -m engineering_team inspect
   ```
2. **Start / discover**
   ```bash
   python3 -m engineering_team start
   python3 -m engineering_team problem --title "..." --description "..." --severity P1
   ```
3. **Create checkpoints before changes**
   ```bash
   python3 -m engineering_team checkpoint -m "pre-change baseline"
   ```
4. **Progress through phases** by recording root cause, baseline, experiments (Track A/B/HYBRID), benchmarks, and evaluations via the orchestrator/managers (programmatic) or agent runs; generate decision report when evaluation is ready.
5. **Human decision** (only when `WAITING_FOR_HUMAN_DECISION`):
   ```bash
   python3 -m engineering_team report
   python3 -m engineering_team approve A   # or B / HYBRID
   python3 -m engineering_team reject
   python3 -m engineering_team request-tests --note "..."
   ```
6. **Integration (approved only)**: `python3 -m engineering_team integrate --dry-run` records intent; actual merge is manual/explicit, followed by regression tests and integration report.
7. **Pause/Resume/Stop**: `pause`, `resume`, `stop` (safe halt). State persists across restarts.

## Programmatic use
```python
from engineering_team.harness.orchestrator import Orchestrator
o = Orchestrator()
print(o.status())
pid = o.discover_problem("Example issue", "details", severity="P2")
o.create_checkpoint("before investigation")
# record root cause, baseline, experiments, benchmarks, evaluation...
# o.prepare_decision_report(pid, recommendation="...", uncertainties=[...])
o.close()
```

## Reports and artifacts
- Decision report: `engineering_team/reports/DECISION_REPORT.md`
- Integration report: `engineering_team/reports/INTEGRATION_REPORT.md`
- Evaluations: `engineering_team/evaluations/`
- Benchmarks: `engineering_team/benchmarks/`
- Logs: `engineering_team/logs/harness.log`

## Physical testing
For microphone/real devices/hotspot/thermal validation, follow a written procedure, collect human observations, submit results as evidence, and mark the workflow `PHYSICAL_TEST_REQUIRED` until evidence is recorded. Never fabricate results.
