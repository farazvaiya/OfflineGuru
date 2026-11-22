"""Chief Engineering Orchestrator: coordinates workflow, state, and records.

This is a real, persistent orchestrator for the engineering_team workspace.
Agent "labour" that requires external reasoning/runtimes is represented as tasks
and recorded runs; local operations (state transitions, checkpoints, reports,
DB persistence, discovery scans) are executed directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import paths
from .benchmark import BenchmarkEngine
from .checkpoint import CheckpointManager
from .claude_adapter import ClaudeCodeAdapter
from .claude_runtime import AgentExecution, SUCCESS
from .db import Database
from .evaluator import IndependentEvaluator
from .experiment_manager import ExperimentManager
from .git_manager import GitManager
from .logging_utils import get_logger
from .report import generate_decision_report, generate_integration_report
from .state_machine import WorkflowState
from .task_manager import TaskManager


class Orchestrator:
    def __init__(self, db_path: Path | None = None,
                 workflow_path: Path | None = None) -> None:
        # Resolve every state path at call time. Importing DB_PATH here would pin
        # the path that happened to be current when this module was first imported
        # (collection order, a previous test, a relocated workspace) and let a
        # scratch run write into the live database.
        paths.ensure_workspace()
        self.db_path = Path(db_path) if db_path else paths.db_path()
        self.workflow_path = Path(workflow_path) if workflow_path else paths.workflow_path()
        self.db = Database(self.db_path)
        self.state = WorkflowState.load(self.workflow_path)
        self.tasks = TaskManager(self.db)
        self.experiments = ExperimentManager(self.db)
        self.checkpoints = CheckpointManager()
        self.git = GitManager()
        self.adapter = ClaudeCodeAdapter(self.db)
        self.runtime = AgentExecution(self.db)
        self.adapter.attach_runtime(self.runtime)
        self.bench = BenchmarkEngine(self.db)
        self.evaluator = IndependentEvaluator(self.db)
        self.reports_dir = Path(paths.TEAM_ROOT) / "reports"
        self.log = get_logger()

    def status(self) -> dict[str, Any]:
        open_tasks = self.tasks.list(status="open")
        problems = self.db.list_problems()
        latest_ckpt = self.db.latest_checkpoint()
        return {
            "state": self.state.state,
            "previous_state": self.state.previous_state,
            "problem_id": self.state.problem_id,
            "selected_candidate": self.state.selected_candidate,
            "updated_at": self.state.updated_at,
            "open_tasks": len(open_tasks),
            "problems": len(problems),
            "latest_checkpoint": latest_ckpt["id"] if latest_ckpt else None,
            "git": self.git.status(),
            "db": str(self.db_path),
        }

    def start(self) -> dict[str, Any]:
        if self.state.state == "IDLE":
            self._transition("PROBLEM_DISCOVERED", "start: begin discovery cycle", "orchestrator")
        return self.status()

    def pause(self) -> dict[str, Any]:
        self.state.pause("user requested pause", "user")
        self.db.record_workflow_event(self.state.previous_state, "PAUSED", "pause", "user")
        return self.status()

    def resume(self) -> dict[str, Any]:
        prev = self.state.previous_state
        self.state.resume("user")
        self.db.record_workflow_event("PAUSED", self.state.state, "resume", "user")
        return {"resumed_to": self.state.state, "from": prev, **self.status()}

    def stop(self) -> dict[str, Any]:
        # Stop means halt progress safely (pause if running), not data loss.
        if self.state.state != "PAUSED":
            try:
                self.pause()
            except Exception:
                pass
        return self.status()

    def _transition(self, to_state: str, reason: str, actor: str, evidence: list[Any] | None = None) -> dict[str, Any]:
        from_state = self.state.state
        event = self.state.transition(to_state, reason, actor, evidence=evidence)
        self.db.record_workflow_event(from_state, to_state, reason, actor, evidence)
        self.log.info("transition %s -> %s: %s", from_state, to_state, reason)
        return event

    def discover_problem(self, title: str, description: str, severity: str = "P2", evidence: list[Any] | None = None) -> str:
        pid = self.db.create_problem(title=title, description=description, severity=severity, evidence=evidence)
        if self.state.state == "IDLE":
            self._transition("PROBLEM_DISCOVERED", f"problem discovered: {pid}", "problem-discovery")
        self.state.problem_id = pid
        self.state.save(self.workflow_path)
        self.tasks.add(f"Root cause analysis: {title}", "root-cause-analysis", phase="ROOT_CAUSE_ANALYSIS", problem_id=pid)
        return pid

    def run_basic_repo_inspection(self) -> dict[str, Any]:
        """Gather baseline inspection facts (real, local)."""
        facts: dict[str, Any] = {"git": self.git.status()}
        # quick counts
        try:
            guru_files = list(Path("guru").rglob("*.py")) if Path("guru").exists() else []
            facts["guru_py_files"] = len(guru_files)
        except Exception:
            facts["guru_py_files"] = None
        try:
            tests = list(Path("tests").glob("test_*.py")) if Path("tests").exists() else []
            facts["test_files"] = len(tests)
        except Exception:
            facts["test_files"] = None
        # existing harness presence
        facts["engineering_harness_exists"] = Path("engineering_harness").is_dir()
        facts["engineering_team_exists"] = Path("engineering_team").is_dir()
        return facts

    def complete_task(self, task_id: str, result: dict[str, Any] | None = None) -> bool:
        return self.tasks.complete(task_id, result=result)

    def fail_task(self, task_id: str, result: dict[str, Any] | None = None) -> bool:
        return self.tasks.fail(task_id, result=result)

    # -- real agent execution --------------------------------------------------
    def execute_agent(self, agent: str, task_id: str, track: str | None = None,
                      problem_description: str = "",
                      evidence_refs: list[Any] | None = None,
                      deliverables: list[str] | None = None,
                      constraints: list[str] | None = None,
                      timeout_s: float | None = None,
                      workspace_mode: str | None = None) -> dict[str, Any]:
        """Execute one specialist agent through the real Claude Code runtime.

        Flow (per integration contract): select agent -> create execution record
        (the runtime does this) -> prepare isolated workspace -> invoke runtime
        -> collect result -> validate artifacts -> record evidence -> decide
        continuation. A FAILED/TIMEOUT/AUTH/NOT_CONFIGURED run NEVER marks the
        task complete and NEVER advances the workflow state.
        """
        task_row = None
        for t in self.tasks.list():
            if t["id"] == task_id:
                task_row = t
                break
        if task_row is None:
            raise SystemExit(f"no such task {task_id}")

        result = self.adapter.run(
            agent=agent,
            task={"id": task_id, "title": task_row.get("title"),
                  "phase": task_row.get("phase")},
            problem_id=(task_row.get("problem_id") if isinstance(task_row.get("problem_id"), str)
                        else None),
            role_key=agent,
            problem_description=problem_description,
            evidence_refs=evidence_refs or [],
            deliverables=deliverables or [],
            constraints=constraints or [],
            track=track,
            timeout_s=timeout_s,
            workspace_mode=workspace_mode,
        )
        # adapter returns AgentRunResult; normalise to a dict for the rest
        # of the flow (output carries the runtime metadata).
        if hasattr(result, "output"):
            run_output = dict(result.output or {})
            run_status = result.status
            run_error = result.error
            run_id = result.run_id
        else:  # defensive: a dict result from a future runtime
            run_output = dict(result)
            run_status = run_output.get("status")
            run_error = run_output.get("error")
            run_id = run_output.get("run_id")

        # artifact validation: every claimed artifact must exist inside the
        # run workspace; missing ones are recorded, never silently trusted.
        ws = Path(run_output.get("workspace")) if run_output.get("workspace") else None
        missing = []
        if ws is not None:
            for a in run_output.get("artifacts", []):
                if not Path(a).is_file() or not str(Path(a).resolve()).startswith(str(ws.resolve())):
                    missing.append(a)
        if missing:
            run_output["artifact_validation"] = {"missing": missing}

        # evidence: workflow event ties the run to the transition log
        self.db.record_workflow_event(
            self.state.state, self.state.state,
            f"agent run {run_id} -> {run_status}",
            actor=agent, evidence=[run_id])

        # continuation decision: only SUCCESS may mark the task done, and only
        # the human-facing workflow (task done command) does that; here we just
        # report. Failed runs leave the task open.
        can_continue = run_status == SUCCESS
        return {"run_id": run_id, "status": run_status, "error": run_error,
                "output": run_output, "artifacts_valid": not missing,
                "missing_artifacts": missing, "can_continue": can_continue}

    def create_checkpoint(self, label: str, kind: str = "manual") -> dict[str, Any]:
        manifest = self.checkpoints.create(label=label, kind=kind)
        self.db.add_checkpoint(label=label, path=str(self.checkpoints.root / manifest["id"]), kind=kind,
                               source_digest=manifest.get("files") and next(iter(manifest["files"].values()), None))
        return manifest

    def record_baseline(self, problem_id: str, baseline: dict[str, Any]) -> None:
        self.db.update_problem(problem_id, baseline_json=json.dumps(baseline), status="baseline_recorded")
        if self.state.state == "ROOT_CAUSE_ANALYSIS":
            self._transition("BASELINE_CREATED", "baseline recorded", "benchmark-engineer")

    def record_root_cause(self, problem_id: str, root_cause: str, evidence: list[Any] | None = None) -> None:
        self.db.update_problem(problem_id, root_cause=root_cause, evidence_json=json.dumps(evidence or []), status="root_cause_identified")
        if self.state.state in {"PROBLEM_DISCOVERED", "IDLE"}:
            if self.state.state == "IDLE":
                self._transition("PROBLEM_DISCOVERED", "problem recorded before RCA", "problem-discovery")
            self._transition("ROOT_CAUSE_ANALYSIS", "root cause recorded", "root-cause-analysis")

    # -- dual track / evidence phases ----------------------------------------
    def start_dual_track(self) -> dict[str, Any]:
        if self.state.state != "BASELINE_CREATED":
            raise ValueError(f"Dual track requires BASELINE_CREATED (state={self.state.state})")
        self._transition("DUAL_TRACK_RUNNING", "Track A and Track B start from the same baseline",
                         "chief-orchestrator")
        return {"state": self.state.state}

    def record_benchmarks(self, problem_id: str, candidates: dict[str, dict[str, Any]],
                          dataset: str | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
        """Record one benchmark per candidate from the same dataset/configuration."""
        if self.state.state != "DUAL_TRACK_RUNNING":
            raise ValueError(f"Benchmarking requires DUAL_TRACK_RUNNING (state={self.state.state})")
        recorded: dict[str, Any] = {}
        for track, results in candidates.items():
            experiment = self.db.query_one(
                "SELECT id FROM experiments WHERE problem_id=? AND track=? ORDER BY created_at DESC LIMIT 1",
                (problem_id, track),
            )
            br = self.bench.record(
                problem_id=problem_id,
                experiment_id=experiment["id"] if experiment else None,
                dataset=dataset,
                config={"dataset": dataset, **dict(config or {})},
                results=results,
                raw={"track": track, "results": results},
            )
            recorded[track] = br.id
        self._transition("BENCHMARKING", f"benchmarks recorded for {sorted(recorded)}", "benchmark-engineer")
        return recorded

    def run_evaluation(self, problem_id: str, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.state.state != "BENCHMARKING":
            raise ValueError(f"Evaluation requires BENCHMARKING (state={self.state.state})")
        payload = self.evaluator.evaluate_problem(problem_id, baseline=baseline)
        self._transition("INDEPENDENT_EVALUATION", "independent evaluation recorded", "independent-evaluator")
        return payload

    def prepare_decision_report(self, problem_id: str, recommendation: str = "",
                                uncertainties: list[str] | None = None,
                                benchmark: dict[str, Any] | None = None,
                                evaluation: dict[str, Any] | None = None) -> Path:
        problem = self.db.query_one("SELECT * FROM problems WHERE id=?", (problem_id,)) or {"id": problem_id}
        exps = self.db.query("SELECT * FROM experiments WHERE problem_id=? ORDER BY created_at", (problem_id,))
        track_a = next((e for e in exps if e.get("track") == "A"), None)
        track_b = next((e for e in exps if e.get("track") == "B"), None)
        hybrid = next((e for e in exps if e.get("track") == "HYBRID"), None)
        path = generate_decision_report(
            problem=problem,
            root_cause=problem.get("root_cause") or "",
            track_a=track_a,
            track_b=track_b,
            hybrid=hybrid,
            benchmark=benchmark or {},
            evaluation=evaluation or {"status": "pending"},
            recommendation=recommendation,
            uncertainties=uncertainties or [],
            out_path=self.reports_dir / "DECISION_REPORT.md",
        )
        if self.state.state in {"INDEPENDENT_EVALUATION", "BENCHMARKING", "DUAL_TRACK_RUNNING"}:
            self._transition("WAITING_FOR_HUMAN_DECISION", "decision report prepared", "independent-evaluator")
        elif self.state.state == "PHYSICAL_TEST_REQUIRED":
            self._transition("WAITING_FOR_HUMAN_DECISION", "decision report prepared after physical test", "independent-evaluator")
        return path

    # -- human decisions -----------------------------------------------------
    def _require_decision_state(self) -> None:
        if self.state.state not in {"WAITING_FOR_HUMAN_DECISION", "REJECTED", "PHYSICAL_TEST_REQUIRED"}:
            raise ValueError(f"Decision commands only valid when awaiting/reviewing decision (state={self.state.state})")

    def approve(self, choice: str) -> dict[str, Any]:
        choice = choice.upper()
        if choice not in {"A", "B", "HYBRID"}:
            raise ValueError("choice must be A, B, or HYBRID")
        self._require_decision_state()
        self.db.record_decision(self.state.problem_id, f"APPROVE_{choice}", choice, decided_by="human")
        self.state.selected_candidate = choice
        self.state.save(self.workflow_path)
        self._transition("APPROVED", f"human approved {choice}", "human")
        return {"approved": choice, "state": self.state.state}

    def reject(self) -> dict[str, Any]:
        self._require_decision_state()
        self.db.record_decision(self.state.problem_id, "REJECT_BOTH", "REJECT", decided_by="human")
        self.state.selected_candidate = None
        self.state.save(self.workflow_path)
        self._transition("REJECTED", "human rejected both candidates", "human")
        return {"rejected": True, "state": self.state.state}

    def request_tests(self, note: str = "more tests requested") -> dict[str, Any]:
        self._require_decision_state()
        self.db.record_decision(self.state.problem_id, "REQUEST_MORE_TESTS", "MORE_TESTS", decided_by="human", note=note)
        # Go back to the evidence phase: both tracks re-run from the same baseline.
        self._transition("DUAL_TRACK_RUNNING", note, "human")
        return {"requested": "more_tests", "state": self.state.state}

    def integrate_approved(self, dry_run: bool = False) -> dict[str, Any]:
        if self.state.state != "APPROVED":
            raise ValueError("Integration requires APPROVED state (explicit human approval)")
        if not self.state.selected_candidate:
            raise ValueError("No selected candidate recorded")
        ckpt = self.create_checkpoint(f"pre-integration-{self.state.problem_id}-{self.state.selected_candidate}", kind="pre-integration")
        result: dict[str, Any] = {
            "problem_id": self.state.problem_id,
            "selected": self.state.selected_candidate,
            "checkpoint": ckpt["id"],
            "dry_run": dry_run,
            "status": "not_executed",
            "note": "No auto-merge performed. Integration must apply the approved experiment's isolated changes to the protected baseline under human-authorized process.",
        }
        if dry_run:
            generate_integration_report(result, out_path=self.reports_dir / "INTEGRATION_REPORT.md")
            return result
        # Enter integration/regression states to record workflow honestly; actual merge is environment/approval specific.
        import json as _json
        import time
        import uuid
        self._transition("INTEGRATION", "begin integration of approved candidate", "integration-engineer")
        self.db.execute(
            "INSERT INTO integration_history(id,problem_id,experiment_id,action,status,checkpoint_id,details_json,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex[:12], self.state.problem_id, None, "integrate", "started", ckpt["id"], _json.dumps(result, default=str), time.time()),
        )
        result["status"] = "awaiting_manual_merge"
        result["next_steps"] = [
            "Apply approved candidate diff from its isolated worktree/branch.",
            "Run full regression suite and relevant benchmarks.",
            "Compare integrated result with baseline and record INTEGRATION_REPORT.md.",
        ]
        self._transition("REGRESSION_TEST", "integration recorded; regression required", "qa-regression")
        generate_integration_report(result, out_path=self.reports_dir / "INTEGRATION_REPORT.md")
        return result

    def close(self) -> None:
        self.db.close()
