import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def run_cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "engineering_team", *args],
        cwd=str(cwd or REPO),
        capture_output=True,
        text=True,
        check=False,
    )


def test_db_migrations_and_basic_ops(tmp_path):
    from engineering_team.harness.db import Database
    db = Database(tmp_path / "t.db")
    assert db.query_one("SELECT value FROM meta WHERE key='schema_version'")["value"] == "1"
    pid = db.create_problem("p", "d", "P2")
    assert pid
    tid = db.create_task("t", "owner", "IDLE", problem_id=pid)
    assert tid
    db.update_task(tid, status="done")
    tasks = db.list_tasks(status="done")
    assert any(t["id"] == tid for t in tasks)
    db.close()


def test_state_machine_transitions_and_approval_gate(tmp_path, monkeypatch):
    import engineering_team.harness.state_machine as sm
    from engineering_team.harness import paths
    wf = tmp_path / "engineering_team" / "state" / "workflow.json"
    monkeypatch.setattr(paths, "TEAM_ROOT", tmp_path / "engineering_team")
    monkeypatch.setattr(paths, "WORKFLOW_PATH", wf)
    paths.ensure_workspace()
    ws = sm.WorkflowState.load(wf)
    assert ws.state == "IDLE"
    ws.transition("PROBLEM_DISCOVERED", "x", "t")
    # cannot go to INTEGRATION without APPROVED
    ws2 = sm.WorkflowState.load(wf)
    ws2.state = "BASELINE_CREATED"; ws2.save(wf)
    ws2 = sm.WorkflowState.load(wf)
    ok, msg = ws2.can_transition("INTEGRATION")
    assert not ok
    assert "Approval" in msg or "APPROVED" in msg
    # pause/resume
    ws2.transition("DUAL_TRACK_RUNNING", "y", "t")
    assert sm.WorkflowState.load(wf).state == "DUAL_TRACK_RUNNING"
    ws3 = sm.WorkflowState.load(wf)
    ws3.pause("pause")
    assert sm.WorkflowState.load(wf).state == "PAUSED"
    ws4 = sm.WorkflowState.load(wf)
    ws4.resume()
    assert sm.WorkflowState.load(wf).state == "DUAL_TRACK_RUNNING"


def test_checkpoint_create_and_manifest(tmp_path):
    from engineering_team.harness.checkpoint import CheckpointManager
    # operate in temp repo-like dir
    (tmp_path / "guru").mkdir()
    (tmp_path / "guru" / "x.py").write_text("print(1)\n")
    cm = CheckpointManager(repo_root=tmp_path)
    # point checkpoint root into tmp
    cm.root = tmp_path / "ck"
    cm.root.mkdir()
    man = cm.create("test", kind="manual", paths=["guru"])
    assert (Path(cm.root) / man["id"] / "source.tar.gz").exists()
    assert (Path(cm.root) / man["id"] / "manifest.json").exists()
    assert "guru/x.py" in man["files"]


def test_cli_status_and_inspect():
    r = run_cli("status")
    assert r.returncode == 0, r.stderr + r.stdout
    data = json.loads(r.stdout)
    assert "state" in data and "db" in data
    r2 = run_cli("inspect")
    assert r2.returncode == 0, r2.stderr + r2.stdout
    data2 = json.loads(r2.stdout)
    assert "guru_py_files" in data2


def test_cli_decision_gate_enforced(tmp_path, monkeypatch):
    # approve should fail when not in waiting state on a clean workspace context is global repo,
    # but global state is IDLE so it must fail cleanly
    r = run_cli("approve", "A")
    assert r.returncode != 0
    assert "Decision" in r.stderr or "awaiting" in r.stderr or "error" in r.stderr.lower()


def test_benchmark_and_evaluator(tmp_path, monkeypatch):
    from engineering_team.harness import paths
    monkeypatch.setattr(paths, "TEAM_ROOT", tmp_path / "engineering_team")
    paths.ensure_workspace()
    from engineering_team.harness.benchmark import BenchmarkEngine
    from engineering_team.harness.db import Database
    from engineering_team.harness.evaluator import IndependentEvaluator
    db = Database(tmp_path / "e.db")
    be = BenchmarkEngine(db)
    br = be.record(None, None, "ds", {"a":1}, {"latency_ms": 100, "wer": 0.1})
    assert br.id
    delta = BenchmarkEngine.compare({"latency_ms": 120}, {"latency_ms": 100})
    assert delta["deltas"]["latency_ms"]["delta"] == -20
    ev = IndependentEvaluator(db)
    payload = ev.evaluate_problem("nope")
    assert "limitations" in payload and "verdict" in payload
    db.close()
