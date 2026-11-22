"""Tests for P0.3: one enforced permission boundary for agent runs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from engineering_team.harness.permissions import (
    build_agent_env,
    check_within,
    permission_args,
    plan_sandbox,
    policy_for,
    sandbox_works,
)

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- tool policy
def test_read_only_roles_get_no_write_tools():
    for role in ("root-cause-analysis", "independent-evaluator", "chief-orchestrator", "A", "c"):
        p = policy_for(role)
        assert p.can_edit is False
        assert p.permission_mode == "plan"
        assert "Write" not in p.tools and "Edit" not in p.tools
        assert "Read" in p.tools


def test_implementing_roles_can_edit_but_never_bypass():
    for role in ("solution-track-a", "qa-regression", "benchmark-engineer", "integration-engineer"):
        p = policy_for(role)
        assert p.can_edit is True
        assert "Write" in p.tools and "Edit" in p.tools
        assert p.permission_mode == "acceptEdits"


def test_unknown_role_is_least_privilege():
    p = policy_for("some-new-role")
    assert p.can_edit is False and p.permission_mode == "plan"


def test_permission_args_never_contain_bypass_flag():
    for role in ("root-cause-analysis", "solution-track-a", "qa-regression", "unknown"):
        args = permission_args(role)
        assert "--permission-mode" in args
        assert "--allowedTools" in args
        assert "--dangerously-skip-permissions" not in args


# ---------------------------------------------------------------- environment
def test_environment_is_allowlisted(tmp_path, monkeypatch):
    for k, v in {
        "AWS_SECRET_ACCESS_KEY": "aws", "GITHUB_TOKEN": "gh", "DATABASE_URL": "db",
        "SSH_AUTH_SOCK": "/tmp/agent.sock", "OPENAI_API_KEY": "sk",
        "ANTHROPIC_API_KEY": "anthropic", "TEAM_CLAUDE_MODEL": "x",
    }.items():
        monkeypatch.setenv(k, v)
    ws = tmp_path / "ws"
    ws.mkdir()
    env = build_agent_env(ws)

    assert env["ANTHROPIC_API_KEY"] == "anthropic"
    for leaked in ("AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "DATABASE_URL",
                   "SSH_AUTH_SOCK", "OPENAI_API_KEY", "TEAM_CLAUDE_MODEL"):
        assert leaked not in env, leaked
    assert env["HOME"] == str(ws / ".home")
    assert env["CLAUDE_CONFIG_DIR"] == str(ws / ".home" / ".claude")
    assert env["HOME"] != os.environ.get("HOME")
    assert (ws / ".home").is_dir()


# ---------------------------------------------------------------- containment
def test_check_within_rejects_prefix_siblings(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    evil = tmp_path / "ws_evil"
    evil.mkdir()
    (root / "ok.txt").write_text("x")
    (evil / "secret.txt").write_text("x")

    assert check_within(root / "ok.txt", [root]) == (root / "ok.txt").resolve()
    with pytest.raises(PermissionError):
        check_within(evil / "secret.txt", [root])
    with pytest.raises(PermissionError):
        check_within(tmp_path / "ws_evil", [root])


def test_check_within_resolves_symlinks(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = root / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(PermissionError):
        check_within(link, [root])


def test_agent_runtime_safe_args_rejects_sibling_prefix(tmp_path):
    from engineering_team.harness.agent_runtime import AgentExecution, AgentRuntimeError

    root = tmp_path / "ws"
    root.mkdir()
    sibling = tmp_path / "ws_evil"
    sibling.mkdir()
    rt = AgentExecution.__new__(AgentExecution)
    rt.allowed_roots = [root.resolve()]
    with pytest.raises(AgentRuntimeError):
        rt._safe_args([str(sibling)])
    assert rt._safe_args(["--flag", "relative-token"]) == ["--flag", "relative-token"]


# ---------------------------------------------------------------- sandbox
def test_sandbox_hides_home_and_project(tmp_path):
    ok, reason = sandbox_works()
    if not ok:
        pytest.skip(f"bwrap unavailable: {reason}")
    ws = tmp_path / "ws"
    ws.mkdir()
    plan = plan_sandbox([sys.executable, "-c",
                         "import pathlib;print(pathlib.Path.home(), "
                         "pathlib.Path('/home').joinpath(pathlib.Path.home().name, '.ssh').exists(), "
                         "pathlib.Path('writable.txt').write_text('ok'))"], ws)
    assert plan.enabled is True
    assert "--unshare-all" in plan.argv and "--tmpfs" in plan.argv

    out = subprocess.run(plan.argv, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert str(ws / ".home") in out.stdout
    assert "True" not in out.stdout.split()[1]  # ~/.ssh not visible
    assert (ws / "writable.txt").read_text() == "ok"


def test_sandbox_off_is_recorded(tmp_path):
    plan = plan_sandbox(["/bin/true"], tmp_path, mode="off")
    assert plan.enabled is False and plan.argv == ["/bin/true"]


# ---------------------------------------------------------------- end to end
def _fake_cli(tmp_path, monkeypatch) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    cli = bindir / "claude"
    cli.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys, pathlib\n"
        "if '--version' in sys.argv:\n"
        "    print('fake claude 9.9.9'); sys.exit(0)\n"
        "art = pathlib.Path('artifacts'); art.mkdir(parents=True, exist_ok=True)\n"
        "(art / 'argv.json').write_text(json.dumps(sys.argv))\n"
        "(art / 'env.json').write_text(json.dumps(dict(os.environ)))\n"
        "print(json.dumps({'result': 'ok', 'type': 'result'}))\n"
    )
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("TEAM_AGENT_SANDBOX", "off")
    return cli


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    from engineering_team.harness import paths

    team = tmp_path / "engineering_team"
    monkeypatch.setattr(paths, "TEAM_ROOT", team)
    monkeypatch.setattr(paths, "DB_PATH", team / "state" / "engineering_team.db")
    monkeypatch.setattr(paths, "WORKFLOW_PATH", team / "state" / "workflow.json")
    monkeypatch.setattr(paths, "LOG_PATH", team / "logs" / "harness.log")
    paths.ensure_workspace()
    return team


def test_run_executes_inside_sandbox_when_available(isolated, tmp_path, monkeypatch):
    """The full run path (not just plan_sandbox) works with bwrap enabled."""
    from engineering_team.harness.orchestrator import Orchestrator

    if not sandbox_works()[0]:
        pytest.skip("bwrap unavailable")
    _fake_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("TEAM_AGENT_SANDBOX", "required")
    orch = Orchestrator()
    try:
        tid = orch.tasks.add("inspect", "root-cause-analysis", phase="ROOT_CAUSE_ANALYSIS")
        res = orch.execute_agent("root-cause-analysis", tid)
        assert res["status"] == "SUCCESS", res
        out = res["output"]
        assert out["sandbox"]["enabled"] is True, out["sandbox"]
        assert out["sandbox"]["filesystem"] == "bwrap"
        ws = Path(out["workspace"])
        assert (ws / "artifacts" / "argv.json").is_file()
        child_env = json.loads((ws / "artifacts" / "env.json").read_text())
        assert child_env["HOME"] == str(ws / ".home")
        assert child_env["HOME"] != os.environ.get("HOME")
    finally:
        orch.close()


def test_run_uses_role_flags_and_sanitized_env(isolated, tmp_path, monkeypatch):
    from engineering_team.harness.orchestrator import Orchestrator

    _fake_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    orch = Orchestrator()
    try:
        tid = orch.tasks.add("inspect", "root-cause-analysis", phase="ROOT_CAUSE_ANALYSIS")
        res = orch.execute_agent("root-cause-analysis", tid)
        assert res["status"] == "SUCCESS", res
        ws = Path(res["output"]["workspace"])
        argv = json.loads((ws / "artifacts" / "argv.json").read_text())
        assert "--permission-mode" in argv and "plan" in argv
        assert "--allowedTools" in argv and "Read" in argv and "Write" not in argv
        assert "--dangerously-skip-permissions" not in argv

        child_env = json.loads((ws / "artifacts" / "env.json").read_text())
        assert "AWS_SECRET_ACCESS_KEY" not in child_env
        assert child_env["ANTHROPIC_API_KEY"] == "anthropic-key"
        assert child_env["HOME"] == str(ws / ".home")
        assert res["output"]["sandbox"]["mode"] == "off"
    finally:
        orch.close()
