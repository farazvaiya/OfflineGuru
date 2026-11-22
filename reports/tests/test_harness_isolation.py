"""Regression tests for P0.1: state paths must be resolved at call time.

The original defect: `orchestrator.py` did `from .paths import DB_PATH`, binding
the production database path when the module was first imported. A test that
patched `paths.DB_PATH` afterwards still wrote to `engineering_team/state/`,
and the workflow test polluted the live DB with demo problems.

These tests deliberately import the orchestrator at module scope *before*
patching, reproducing the collection order that triggered the bug.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

# Import first, patch later: this is the ordering that used to defeat isolation.
from engineering_team.harness.orchestrator import Orchestrator
from engineering_team.harness.checkpoint import CheckpointManager
from engineering_team.harness.report import generate_decision_report

REPO = Path(__file__).resolve().parents[1]
LIVE_DB = REPO / "engineering_team" / "state" / "engineering_team.db"


def _live_counts() -> dict[str, int] | None:
    """Read-only snapshot of the production DB (None when absent)."""
    if not LIVE_DB.exists():
        return None
    conn = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    try:
        tables = ("problems", "tasks", "decisions", "agent_runs", "checkpoints", "workflow_events")
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    finally:
        conn.close()


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    from engineering_team.harness import paths

    team = tmp_path / "engineering_team"
    monkeypatch.setattr(paths, "TEAM_ROOT", team)
    monkeypatch.setattr(paths, "DB_PATH", team / "state" / "engineering_team.db")
    monkeypatch.setattr(paths, "WORKFLOW_PATH", team / "state" / "workflow.json")
    monkeypatch.setattr(paths, "LOG_PATH", team / "logs" / "harness.log")
    monkeypatch.setattr(paths, "DECISION_REPORT_PATH", team / "reports" / "DECISION_REPORT.md")
    paths.ensure_workspace()
    return paths, team


def _fake_claude(tmp_path, monkeypatch) -> Path:
    """A fake CLI that answers --version and writes one artifact into its cwd."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    cli = bindir / "claude"
    cli.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, pathlib\n"
        "if '--version' in sys.argv:\n"
        "    print('fake claude 9.9.9')\n"
        "    sys.exit(0)\n"
        "pathlib.Path('agent-report.md').write_text('delivered\\n')\n"
        "print(json.dumps({'result': 'ok', 'type': 'result'}))\n"
    )
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{__import__('os').environ['PATH']}")
    return cli


def test_patched_paths_apply_after_import(isolated):
    """The exact D1 reproduction: patch after import, then construct."""
    from engineering_team.harness import paths

    _, team = isolated
    orch = Orchestrator()
    try:
        assert orch.db_path == team / "state" / "engineering_team.db"
        assert orch.db.path == team / "state" / "engineering_team.db"
        assert orch.workflow_path == team / "state" / "workflow.json"
        # and the DB that was actually opened lives in the temp tree
        assert orch.db.query_one("SELECT value FROM meta WHERE key='schema_version'") is not None
    finally:
        orch.close()


def test_orchestrator_never_writes_live_db(isolated):
    before = _live_counts()
    orch = Orchestrator()
    try:
        pid = orch.discover_problem("isolation probe", "must not reach production")
        orch.record_root_cause(pid, "probe")
        assert orch.db.query_one("SELECT id FROM problems WHERE id=?", (pid,)) is not None
    finally:
        orch.close()
    assert _live_counts() == before


def test_agent_run_paths_follow_team_root(isolated, tmp_path, monkeypatch):
    from engineering_team.harness import paths

    _, team = isolated
    _fake_claude(tmp_path, monkeypatch)
    live_ws = paths.REPO_ROOT / "engineering_team" / "state" / "agent_workspaces"
    live_logs = paths.REPO_ROOT / "engineering_team" / "logs" / "agent_runs"
    ws_before = len(list(live_ws.glob("*/*"))) if live_ws.is_dir() else 0
    logs_before = len(list(live_logs.glob("*.log"))) if live_logs.is_dir() else 0

    orch = Orchestrator()
    try:
        tid = orch.tasks.add("write a report", "qa-regression", phase="ROOT_CAUSE_ANALYSIS")
        res = orch.execute_agent("qa-regression", tid, problem_description="probe",
                                 deliverables=["agent-report.md"])
        assert res["status"] == "SUCCESS", res
        out = res["output"]
        ws = Path(out["workspace"])
        assert ws.is_relative_to(team / "state" / "agent_workspaces"), ws
        assert (ws / "agent-report.md").is_file()
        assert res["artifacts_valid"] is True
        log_file = team / "logs" / "agent_runs" / f"{res['run_id']}.log"
        assert log_file.is_file()
        assert json.loads(log_file.read_text())["run_id"] == res["run_id"]
    finally:
        orch.close()

    # production tree untouched
    assert (len(list(live_ws.glob("*/*"))) if live_ws.is_dir() else 0) == ws_before
    assert (len(list(live_logs.glob("*.log"))) if live_logs.is_dir() else 0) == logs_before


def test_report_default_path_follows_team_root(isolated):
    _, team = isolated
    p = generate_decision_report(
        problem={"id": "p1", "title": "t", "description": "d"},
        root_cause="", track_a=None, track_b=None, hybrid=None,
        benchmark={}, evaluation={}, recommendation="",
    )
    assert p == team / "reports" / "DECISION_REPORT.md"
    assert p.is_file()


def test_checkpoint_excludes_harness_state(isolated, tmp_path):
    _, team = isolated
    repo = tmp_path / "repo"
    (repo / "guru").mkdir(parents=True)
    (repo / "guru" / "x.py").write_text("print(1)\n")
    (repo / "engineering_team" / "harness").mkdir(parents=True)
    (repo / "engineering_team" / "harness" / "db.py").write_text("# source\n")
    (repo / "engineering_team" / "state").mkdir(parents=True)
    (repo / "engineering_team" / "state" / "engineering_team.db").write_text("db")
    (repo / "engineering_team" / "checkpoints").mkdir(parents=True)
    (repo / "engineering_team" / "checkpoints" / "old" / "source.tar.gz").parent.mkdir()
    (repo / "engineering_team" / "checkpoints" / "old" / "source.tar.gz").write_text("x")

    cm = CheckpointManager(repo_root=repo)
    cm.root = team / "checkpoints"
    cm.root.mkdir(parents=True, exist_ok=True)
    manifest = cm.create("probe", paths=["guru", "engineering_team"])

    assert "guru/x.py" in manifest["files"]
    assert "engineering_team/harness/db.py" in manifest["files"]
    assert not any("/state/" in name or "/checkpoints/" in name for name in manifest["files"])

    import tarfile
    with tarfile.open(cm.root / manifest["id"] / "source.tar.gz") as tar:
        names = tar.getnames()
    assert not any("engineering_team/state" in n or "engineering_team/checkpoints" in n for n in names)


def test_cli_report_uses_relocated_root(tmp_path):
    """End-to-end: a subprocess with ENGINEERING_TEAM_ROOT must not report the live path."""
    import subprocess
    import sys

    team = tmp_path / "team"
    env = {**__import__("os").environ, "ENGINEERING_TEAM_ROOT": str(team), "TMPDIR": str(tmp_path)}
    r = subprocess.run([sys.executable, "-m", "engineering_team", "report"],
                       cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["decision_report"] == str(team / "reports" / "DECISION_REPORT.md")
    assert data["db"] == str(team / "state" / "engineering_team.db")
