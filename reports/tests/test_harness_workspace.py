"""Tests for P0.2: agents get isolated source access.

Proves:
  - a seeded workspace contains the intended project source
  - harness state (live DB, logs, checkpoints, venv, models) is not exposed
  - candidate edits stay inside the copy; the real tree is unchanged
  - inspect mode is enforced read-only, not prompt-only
  - two candidate workspaces do not share state
  - an agent can read source and write an artifact through the orchestrator
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from engineering_team.harness.workspace import (
    ARTIFACTS_DIR,
    EDIT,
    INSPECT,
    METADATA_NAME,
    WorkspaceProvisioner,
    digest_tree,
    thaw,
)

REPO = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_seed_contains_source_and_excludes_state(tmp_path):
    spec = WorkspaceProvisioner(repo_root=REPO).seed(tmp_path / "run1", mode=EDIT)

    assert (spec.source_dir / "guru" / "asr.py").is_file()
    assert (spec.source_dir / "tests" / "conftest.py").is_file()
    assert (spec.source_dir / "pyproject.toml").is_file()
    assert (spec.source_dir / "engineering_team" / "harness" / "db.py").is_file()
    # harness state and heavy/private trees are never exposed
    assert not (spec.source_dir / "engineering_team" / "state").exists()
    assert not (spec.source_dir / "engineering_team" / "logs").exists()
    assert not (spec.source_dir / "engineering_team" / "checkpoints").exists()
    assert not (spec.source_dir / ".venv").exists()
    assert not (spec.source_dir / "models").exists()
    # metadata is self-describing and matches the seeded tree
    meta = json.loads((spec.run_dir / METADATA_NAME).read_text())
    assert meta["mode"] == EDIT
    assert meta["source_digest"] == digest_tree(spec.source_dir)
    assert meta["files"]["guru/asr.py"]
    assert spec.artifacts_dir.is_dir()


def test_edit_mode_changes_are_isolated(tmp_path):
    original = REPO / "guru" / "asr.py"
    before = _sha(original)
    prov = WorkspaceProvisioner(repo_root=REPO)
    spec = prov.seed(tmp_path / "candidate-a", mode=EDIT)
    copy = spec.source_dir / "guru" / "asr.py"
    copy.write_text(copy.read_text() + "\n# candidate edit\n")

    assert _sha(original) == before, "candidate edit leaked into the real source tree"
    changes = prov.source_changes(spec)
    assert [c["path"] for c in changes] == ["guru/asr.py"]
    assert changes[0]["change"] == "modified"
    assert changes[0]["before"] != changes[0]["after"]


def test_inspect_mode_is_read_only(tmp_path):
    prov = WorkspaceProvisioner(repo_root=REPO)
    spec = prov.seed(tmp_path / "frozen", mode=INSPECT)
    target = spec.source_dir / "guru" / "asr.py"
    try:
        assert target.is_file()
        with pytest.raises(PermissionError):
            target.open("a").write("x")
        with pytest.raises(PermissionError):
            (spec.source_dir / "newfile.py").write_text("x")
        # artifacts remain writable in the same run
        (spec.artifacts_dir / "note.md").write_text("ok")
        assert prov.source_changes(spec) == []
    finally:
        thaw(spec.source_dir)


def test_candidate_workspaces_are_independent(tmp_path):
    prov = WorkspaceProvisioner(repo_root=REPO)
    a = prov.seed(tmp_path / "track-A", mode=EDIT)
    b = prov.seed(tmp_path / "track-B", mode=EDIT)
    assert a.source_dir != b.source_dir
    f = "guru/config.py"
    (a.source_dir / f).write_text("# only candidate A\n")
    assert (b.source_dir / f).read_text() != "# only candidate A\n"
    assert prov.source_changes(b) == []


def _fake_agent_cli(tmp_path, monkeypatch) -> Path:
    """Reads source/, writes artifacts/proof.json — a real (if fake) agent."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    cli = bindir / "claude"
    cli.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, pathlib\n"
        "if '--version' in sys.argv:\n"
        "    print('fake claude 9.9.9'); sys.exit(0)\n"
        "src = pathlib.Path('source/guru/asr.py')\n"
        "out = pathlib.Path('artifacts/proof.json')\n"
        "out.parent.mkdir(parents=True, exist_ok=True)\n"
        "out.write_text(json.dumps({'read_source': src.is_file(),\n"
        "                           'bytes': src.stat().st_size if src.is_file() else 0}))\n"
        "print(json.dumps({'result': 'ok', 'type': 'result'}))\n"
    )
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
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
    return paths, team


def test_orchestrated_agent_reads_source_and_writes_artifact(isolated, tmp_path, monkeypatch):
    from engineering_team.harness.orchestrator import Orchestrator

    _, team = isolated
    _fake_agent_cli(tmp_path, monkeypatch)
    orch = Orchestrator()
    try:
        tid = orch.tasks.add("inspect asr", "root-cause-analysis", phase="ROOT_CAUSE_ANALYSIS")
        res = orch.execute_agent("root-cause-analysis", tid, problem_description="probe")
        assert res["status"] == "SUCCESS", res
        out = res["output"]
        assert out["workspace_mode"] == INSPECT
        snapshot = out["source_snapshot"]
        assert snapshot["file_count"] > 50
        ws = Path(out["workspace"])
        proof = ws / ARTIFACTS_DIR / "proof.json"
        assert proof.is_file()
        payload = json.loads(proof.read_text())
        assert payload["read_source"] is True and payload["bytes"] > 0
        assert out["source_changes"] == []
        assert any(p.endswith("proof.json") for p in out["artifacts"])
        # traceable on disk and in the DB
        meta = json.loads((ws / METADATA_NAME).read_text())
        assert meta["source_digest"] == snapshot["source_digest"]
        row = orch.db.query_one("SELECT output_json FROM agent_runs WHERE id=?", (res["run_id"],))
        assert json.loads(row["output_json"])["source_snapshot"]["file_count"] == snapshot["file_count"]
    finally:
        orch.close()
        for p in (team / "state" / "agent_workspaces").rglob("source"):
            thaw(p)
