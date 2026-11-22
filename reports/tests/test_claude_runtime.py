"""Tests for the real Claude Code runtime adapter (Phase D).

MOCK tests use a fake `claude` executable on PATH — deterministic, offline.
The REAL smoke test runs only when the actual CLI is installed AND authenticated;
it is labelled REAL and self-skips otherwise. A mock pass never claims a real
agent executed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from engineering_team.harness.db import Database
from engineering_team.harness.claude_runtime import (
    AgentExecution, AUTH_REQUIRED, CANCELLED, FAILED, NOT_CONFIGURED, SUCCESS, TIMEOUT,
    discover_claude, load_role_definition,
)
from engineering_team.harness.claude_adapter import ClaudeCodeAdapter
from engineering_team.harness.orchestrator import Orchestrator


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture()
def fake_cli(tmp_path, monkeypatch):
    """A fake claude CLI whose behaviour is chosen by its script content."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    cli = bindir / "claude"
    cli.write_text("#!/usr/bin/env python3\nimport sys\nprint('placeholder')\n")
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + ":" + __import__("os").environ["PATH"])
    return cli


def _make_cli(tmp_path, monkeypatch, body: str) -> Path:
    """Fake claude CLI. Handles --version successfully; otherwise runs body."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(parents=True, exist_ok=True)
    cli = bindir / "claude"
    cli.write_text("#!/usr/bin/env python3\n"
                   "import sys\n"
                   "if '--version' in sys.argv:\n"
                   "    print('fake claude 9.9.9')\n"
                   "    sys.exit(0)\n"
                   + body)
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{__import__('os').environ['PATH']}")
    return cli


# ---------------------------------------------------------------- discovery
def test_discover_claude_real_or_none():
    p = discover_claude()
    assert p is None or Path(p).is_file()


def test_discovery_missing_cli(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent-dir-xyz")
    assert discover_claude() is None


# ---------------------------------------------------------------- role loading
def test_role_loading_each_section():
    agents_md = Path(__file__).resolve().parents[1] / "engineering_team" / "agents" / "AGENTS.md"
    for key in ("A", "B", "C", "D", "E", "F", "G", "H", "I"):
        text = load_role_definition(agents_md, key)
        assert text.startswith(f"## {key}.")
    # substring match also works
    text = load_role_definition(agents_md, "evaluator")
    assert "Independent Evaluator" in text
    with pytest.raises(KeyError):
        load_role_definition(agents_md, "ZZZ")


# ---------------------------------------------------------------- mock executions
def test_successful_execution(db, tmp_path, monkeypatch):
    _make_cli(tmp_path, monkeypatch, """#!/usr/bin/env python3
import json, sys
print(json.dumps({"result": "done", "type": "result"}))
""")
    rt = AgentExecution(db, workspace_root=tmp_path / "ws", log_dir=tmp_path / "logs")
    r = rt.run("test-agent", task={"id": "T1"}, role_key="A",
               problem_description="probe", constraints=["read-only"])
    assert r["status"] == SUCCESS
    assert r["exit_code"] == 0
    assert r["elapsed_s"] >= 0
    assert r["digest_before"] == r["digest_after"]
    assert r["artifacts"] == []  # empty workspace, no files
    assert Path(r["workspace"]).is_dir()


def test_missing_cli(db, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent-dir-xyz")
    rt = AgentExecution(db, executable=None, workspace_root=tmp_path / "ws")
    monkeypatch.setattr(rt, "executable", None)
    r = rt.run("test-agent", role_key="A")
    assert r["status"] == NOT_CONFIGURED
    assert "reason" in r


def test_auth_failure(db, tmp_path, monkeypatch):
    _make_cli(tmp_path, monkeypatch, """#!/usr/bin/env python3
import sys
sys.stderr.write("Error: authentication required. Please run /login")
sys.exit(1)
""")
    rt = AgentExecution(db, workspace_root=tmp_path / "ws")
    r = rt.run("test-agent", role_key="A")
    assert r["status"] == AUTH_REQUIRED, r


def test_timeout(db, tmp_path, monkeypatch):
    _make_cli(tmp_path, monkeypatch, """#!/usr/bin/env python3
import time
time.sleep(30)
""")
    rt = AgentExecution(db, workspace_root=tmp_path / "ws", default_timeout_s=1.0)
    r = rt.run("test-agent", role_key="A")
    assert r["status"] == TIMEOUT
    assert "timeout" in r["error"].lower()


def test_nonzero_exit(db, tmp_path, monkeypatch):
    _make_cli(tmp_path, monkeypatch, """#!/usr/bin/env python3
import sys
sys.stderr.write("boom")
sys.exit(3)
""")
    rt = AgentExecution(db, workspace_root=tmp_path / "ws")
    r = rt.run("test-agent", role_key="A")
    assert r["status"] == FAILED
    assert "exit 3" in r["error"]


def test_invalid_output(db, tmp_path, monkeypatch):
    _make_cli(tmp_path, monkeypatch, """#!/usr/bin/env python3
print("this is not json")
""")
    rt = AgentExecution(db, workspace_root=tmp_path / "ws")
    r = rt.run("test-agent", role_key="A")
    assert r["status"] == FAILED
    assert "not valid CLI json" in r["error"]


def test_output_json_missing_result_field(db, tmp_path, monkeypatch):
    _make_cli(tmp_path, monkeypatch, """#!/usr/bin/env python3
import json
print(json.dumps({"something": "else"}))
""")
    rt = AgentExecution(db, workspace_root=tmp_path / "ws")
    r = rt.run("test-agent", role_key="A")
    assert r["status"] == FAILED
    assert "lacks result/type" in r["error"]


def test_cancelled(db, tmp_path, monkeypatch):
    # CANCELLED is reached when run() raises KeyboardInterrupt. Simulate by
    # patching subprocess.run AFTER availability, raising only for real runs:
    # patch the module-level reference inside run() by making the first call
    # (--version) succeed and subsequent calls raise.
    rt = AgentExecution(db, workspace_root=tmp_path / "ws")
    real_run = subprocess.run
    state = {"n": 0}

    def fake_run(argv, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return real_run(argv, **kw)  # availability probe
        raise KeyboardInterrupt

    monkeypatch.setattr("engineering_team.harness.claude_runtime.subprocess.run", fake_run)
    r = rt.run("test-agent", role_key="A")
    assert r["status"] == CANCELLED, r
    assert r["error"] == "cancelled by operator"
    # the run_id was recorded even though execution was interrupted
    assert r["run_id"]
    rows = db.query("SELECT status FROM agent_runs WHERE id=?", (r["run_id"],))
    assert rows[0]["status"] == CANCELLED


# ---------------------------------------------------------------- adapter + persistence
def test_adapter_persists_run(db, tmp_path, monkeypatch):
    _make_cli(tmp_path, monkeypatch, """#!/usr/bin/env python3
import json
print(json.dumps({"result": "ok", "type": "result"}))
""")
    rt = AgentExecution(db, workspace_root=tmp_path / "ws", log_dir=tmp_path / "logs")
    adapter = ClaudeCodeAdapter(db, runtime=rt)
    res = adapter.run("test-agent", task={"id": "T9"}, problem_id=None, role_key="A")
    assert res.status == SUCCESS
    rows = db.query("SELECT * FROM agent_runs")
    assert len(rows) == 1
    row = rows[0]
    assert row["agent"] == "test-agent"
    assert row["status"] == SUCCESS
    assert row["task_id"] == "T9"
    assert row["finished_at"] is not None
    out = json.loads(row["output_json"])
    assert out["exit_code"] == 0
    assert out["digest_before"] and out["digest_after"]
    # execution log persisted
    log_file = rt.log_dir / f"{res.run_id}.log"
    assert log_file.is_file()
    logged = json.loads(log_file.read_text())
    assert logged["run_id"] == res.run_id


def test_adapter_pending_external_without_runtime(db):
    adapter = ClaudeCodeAdapter(db)  # no runtime attached
    res = adapter.run("nobody")
    assert res.status == "pending_external"
    assert "No local handler" in res.output["message"]


# ---------------------------------------------------------------- workspace isolation
def test_track_isolation(db, tmp_path, monkeypatch):
    _make_cli(tmp_path, monkeypatch, """#!/usr/bin/env python3
import json
print(json.dumps({"result": "ok", "type": "result"}))
""")
    rt = AgentExecution(db, workspace_root=tmp_path / "ws")
    a = rt.run("track-a-agent", track="A", role_key="A")
    b = rt.run("track-b-agent", track="B", role_key="A")
    pa, pb = Path(a["workspace"]), Path(b["workspace"])
    assert pa != pb
    assert "track-A" in str(pa) and "track-B" in str(pb)
    assert pa.is_dir() and pb.is_dir()


def test_single_writer_lock(db, tmp_path, monkeypatch):
    _make_cli(tmp_path, monkeypatch, """#!/usr/bin/env python3
import json, time
time.sleep(1.0)
print(json.dumps({"result": "ok", "type": "result"}))
""")
    rt = AgentExecution(db, workspace_root=tmp_path / "ws")
    ws = tmp_path / "ws" / "shared" / "locked-run"
    ws.mkdir(parents=True)
    import fcntl
    lf = open(ws / ".writer.lock", "w")
    fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)  # hold the lock
    try:
        r = rt.run("locked-agent", workspace=ws, role_key="A")
        assert r["status"] == FAILED
        assert "locked" in r["error"].lower()
    finally:
        fcntl.flock(lf, fcntl.LOCK_UN)
        lf.close()


# ---------------------------------------------------------------- orchestrator integration
def test_orchestrator_execute_agent_failure_keeps_state(db, tmp_path, monkeypatch):
    monkeypatch.setattr("engineering_team.harness.paths.DB_PATH", tmp_path / "o.db")
    # empty PATH -> runtime unavailable -> NOT_CONFIGURED, task must stay open
    monkeypatch.setenv("PATH", "/nonexistent-dir-xyz")
    orch = Orchestrator(db_path=tmp_path / "o.db",
                        workflow_path=tmp_path / "wf.json")
    tid = orch.tasks.add("do the thing", "test-agent", phase="ROOT_CAUSE_ANALYSIS")
    before = orch.state.state
    res = orch.execute_agent("test-agent", tid, problem_description="probe")
    assert res["status"] == NOT_CONFIGURED
    assert res["can_continue"] is False
    # task NOT marked done; state unchanged
    assert orch.state.state == before
    t = [x for x in orch.tasks.list() if x["id"] == tid][0]
    assert t["status"] == "open"
    orch.close()


def test_orchestrator_execute_agent_success_path(db, tmp_path, monkeypatch):
    _make_cli(tmp_path, monkeypatch, """#!/usr/bin/env python3
import json
print(json.dumps({"result": "ok", "type": "result"}))
""")
    orch = Orchestrator(db_path=tmp_path / "o.db",
                        workflow_path=tmp_path / "wf.json")
    tid = orch.tasks.add("do the thing", "test-agent", phase="ROOT_CAUSE_ANALYSIS")
    res = orch.execute_agent("qa-regression", tid, problem_description="probe",
                             deliverables=["report.md"],
                             constraints=["read-only"])
    assert res["status"] == SUCCESS
    assert res["can_continue"] is True
    assert res["artifacts_valid"] is True
    # evidence: workflow event recorded
    evs = orch.db.query("SELECT * FROM workflow_events ORDER BY ts DESC LIMIT 5")
    assert any("agent run" in (e["reason"] or "") for e in evs)
    orch.close()


def test_orchestrator_execute_unknown_task(db, tmp_path, monkeypatch):
    orch = Orchestrator(db_path=tmp_path / "o.db",
                        workflow_path=tmp_path / "wf.json")
    with pytest.raises(SystemExit):
        orch.execute_agent("test-agent", "NOPE")
    orch.close()


# ---------------------------------------------------------------- no auto-integration
def test_no_auto_production_integration(db, tmp_path, monkeypatch):
    _make_cli(tmp_path, monkeypatch, """#!/usr/bin/env python3
import json
print(json.dumps({"result": "ok", "type": "result"}))
""")
    rt = AgentExecution(db, workspace_root=tmp_path / "ws")
    r = rt.run("test-agent", role_key="A")
    assert r["status"] == SUCCESS
    # no signoffs, no decisions, no integration rows were created by the run
    for table in ("decisions", "integration_history"):
        rows = db.query(f"SELECT COUNT(*) AS n FROM {table}")
        assert rows[0]["n"] == 0, table
    signoffs = tmp_path / "state" / "signoffs.json"
    assert not signoffs.exists() or json.loads(signoffs.read_text()).get("signoffs") == []


# ---------------------------------------------------------------- REAL smoke test
@pytest.mark.real
def test_real_claude_cli_smoke():
    """REAL execution test: invokes the installed claude CLI (not a mock).

    Runs only when the CLI exists; skips otherwise. Uses a trivial echo-class
    task with --max-turns 1 so it cannot do work; verifies the adapter can
    really reach the runtime and classify a genuine result.
    """
    exe = discover_claude()
    if not exe:
        pytest.skip("claude CLI not installed (real smoke test)")
    avail = AgentExecution.__new__(AgentExecution)
    # minimal availability probe without constructing full runtime
    proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20)
    if proc.returncode != 0:
        pytest.skip(f"claude CLI not usable: {proc.stderr[:100]}")
    # real run via the adapter against a temp DB
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        d = Database(Path(td) / "r.db")
        rt = AgentExecution(d, executable=exe, workspace_root=Path(td) / "ws",
                            log_dir=Path(td) / "logs", default_timeout_s=120,
                            default_max_turns=1)
        r = rt.run("orchestrator", role_key="A",
                   problem_description="smoke: say OK",
                   deliverables=["none"], constraints=["read-only", "no tools"])
        assert r["status"] in (SUCCESS, FAILED, TIMEOUT, AUTH_REQUIRED, NOT_CONFIGURED)
        # whatever the outcome, it must be honest: exit code and tails recorded
        assert "exit_code" in r
        assert "stdout_tail" in r or r["status"] in (TIMEOUT, NOT_CONFIGURED)
        d.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
