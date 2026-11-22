"""End-to-end workflow test for the engineering_team harness (isolated workspace).

Proves the full pipeline mechanically: discovery -> RCA -> baseline -> dual track ->
benchmarks -> independent evaluation -> decision report -> human gate -> approval ->
guarded integration -> pause/resume persistence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    from engineering_team.harness import paths
    team = tmp_path / "engineering_team"
    monkeypatch.setattr(paths, "TEAM_ROOT", team)
    monkeypatch.setattr(paths, "DB_PATH", team / "state" / "engineering_team.db")
    monkeypatch.setattr(paths, "WORKFLOW_PATH", team / "state" / "workflow.json")
    monkeypatch.setattr(paths, "DECISION_REPORT_PATH", team / "reports" / "DECISION_REPORT.md")
    monkeypatch.setattr(paths, "INTEGRATION_REPORT_PATH", team / "reports" / "INTEGRATION_REPORT.md")
    monkeypatch.setattr(paths, "LOG_PATH", team / "logs" / "harness.log")
    paths.ensure_workspace()
    from engineering_team.harness.orchestrator import Orchestrator
    return paths, team, Orchestrator


def test_full_cycle_to_human_gate(isolated):
    _paths, team, Orchestrator = isolated
    orch = Orchestrator()
    assert orch.state.state == "IDLE"

    pid = orch.discover_problem("Demo: decision gate exercise",
                                "Synthetic problem record used to prove workflow plumbing.",
                                severity="P1", evidence=["tests/test_engineering_team_workflow.py"])
    assert orch.state.state == "PROBLEM_DISCOVERED"

    orch.record_root_cause(pid, "Root cause recorded for the exercise.",
                           evidence=["tests/test_engineering_team_workflow.py"])
    assert orch.state.state == "ROOT_CAUSE_ANALYSIS"

    orch.record_baseline(pid, {"latency_ms_p95": 1880, "wer": 0.038})
    assert orch.state.state == "BASELINE_CREATED"

    # integration must be impossible before approval
    with pytest.raises(ValueError):
        orch.integrate_approved()

    orch.start_dual_track()
    assert orch.state.state == "DUAL_TRACK_RUNNING"
    tracks = orch.experiments
    exp_a = tracks.create(pid, "A", hypothesis="original approach")
    exp_b = tracks.create(pid, "B", hypothesis="open-source approach")
    assert exp_a and exp_b
    tracks.record_implementation(exp_a, {"files": ["guru/example.py"], "summary": "test stub"},
                                 status="implemented")
    tracks.record_tests(exp_a, {"passed": 3, "failed": 0}, status="tested")
    tracks.record_implementation(exp_b, {"files": ["guru/example_b.py"], "summary": "test stub"},
                                 status="implemented")
    tracks.record_tests(exp_b, {"passed": 3, "failed": 0}, status="tested")

    recorded = orch.record_benchmarks(
        pid,
        {"A": {"latency_ms_p95": 1500, "wer": 0.038},
         "B": {"latency_ms_p95": 1650, "wer": 0.039}},
        dataset="demo://synthetic", config={"clients": 30, "mode": "simulated"},
    )
    assert set(recorded) == {"A", "B"}
    assert orch.state.state == "BENCHMARKING"

    evaluation = orch.run_evaluation(pid, baseline={"latency_ms_p95": 1880, "wer": 0.038})
    assert orch.state.state == "INDEPENDENT_EVALUATION"
    assert evaluation["verdict"] == "evidence_recorded"
    assert set(evaluation["tracks"]) == {"A", "B"}

    report = orch.prepare_decision_report(
        pid,
        recommendation="Track A if the p95 improvement reproduces; Track B not integrated.",
        uncertainties=["benchmarks are synthetic plumbing checks, not real measurements"],
        benchmark={"methodology": {"note": "plumbing only"}, "results": recorded},
        evaluation={"verdict": evaluation["verdict"]},
    )
    assert orch.state.state == "WAITING_FOR_HUMAN_DECISION"
    text = report.read_text(encoding="utf-8")
    for heading in ["## 1. Problem description", "## 2. Root cause", "## 3. Track A",
                    "## 4. Track B", "## 12. Independent evaluator findings",
                    "## 13. AI recommendation", "APPROVE_HYBRID", "REJECT_BOTH",
                    "REQUEST_MORE_TESTS"]:
        assert heading in text

    # no decision yet -> integration still refused
    with pytest.raises(ValueError):
        orch.integrate_approved()

    # pause/resume survives a restart in the same workspace
    orch.pause()
    assert orch.state.state == "PAUSED"
    orch.close()
    orch2 = Orchestrator()
    assert orch2.state.state == "PAUSED"
    orch2.resume()
    assert orch2.state.state == "WAITING_FOR_HUMAN_DECISION"

    approved = orch2.approve("A")
    assert approved["state"] == "APPROVED"
    assert orch2.state.selected_candidate == "A"

    integ = orch2.integrate_approved(dry_run=True)
    assert integ["status"] == "not_executed"
    assert integ["selected"] == "A"
    assert (team / "reports" / "INTEGRATION_REPORT.md").exists()

    decisions = orch2.db.query("SELECT command, selected FROM decisions ORDER BY created_at")
    assert {"command": "APPROVE_A", "selected": "A"} in decisions
    orch2.close()


def test_reject_and_request_more_tests(isolated):
    _paths, _team, Orchestrator = isolated
    orch = Orchestrator()
    pid = orch.discover_problem("Demo reject path", "desc")
    orch.record_root_cause(pid, "cause")
    orch.record_baseline(pid, {})
    orch.start_dual_track()
    orch.experiments.create(pid, "A", hypothesis="a")
    orch.record_benchmarks(pid, {"A": {"x": 1}})
    orch.run_evaluation(pid, baseline={})
    orch.prepare_decision_report(pid)

    orch.request_tests("need a 30-client run")
    assert orch.state.state == "DUAL_TRACK_RUNNING"

    orch.record_benchmarks(pid, {"A": {"x": 2}})
    orch.run_evaluation(pid, baseline={})
    orch.prepare_decision_report(pid)
    orch.reject()
    assert orch.state.state == "REJECTED"
    assert orch.state.selected_candidate is None
    with pytest.raises(ValueError):
        orch.integrate_approved()
    orch.close()
