"""Git branch and worktree management for isolated experiments.

The host project may not be a git repository. In that case, the manager operates
in "snapshot mode": it records the limitation and uses filesystem checkpoints for
isolation metadata while refusing destructive git operations.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import REPO_ROOT, TEAM_ROOT


@dataclass
class CmdResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    cmd: list[str]


class GitManager:
    def __init__(self, repo_root: Path = REPO_ROOT) -> None:
        self.repo_root = repo_root
        self.worktrees_dir = TEAM_ROOT / "experiments" / "worktrees"
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)

    def _run(self, args: list[str], cwd: Path | None = None) -> CmdResult:
        cmd = ["git", *args]
        try:
            p = subprocess.run(cmd, cwd=str(cwd or self.repo_root), capture_output=True, text=True, check=False)
        except Exception as e:
            return CmdResult(False, 127, "", str(e), cmd)
        return CmdResult(p.returncode == 0, p.returncode, p.stdout.strip(), p.stderr.strip(), cmd)

    def is_repo(self) -> bool:
        return self._run(["rev-parse", "--is-inside-work-tree"]).ok

    def status(self) -> dict[str, Any]:
        if not self.is_repo():
            return {"is_repo": False, "note": "Not a git repository; worktree isolation via filesystem snapshots only."}
        r = self._run(["status", "--porcelain"])
        branch = self._run(["rev-parse", "--abbrev-ref", "HEAD"])
        return {
            "is_repo": True,
            "branch": branch.stdout if branch.ok else None,
            "dirty": bool(r.stdout),
            "porcelain": r.stdout.splitlines() if r.stdout else [],
        }

    def current_commit(self) -> str | None:
        r = self._run(["rev-parse", "HEAD"])
        return r.stdout if r.ok else None

    def create_experiment_branch(self, name: str, base: str | None = None) -> CmdResult:
        if not self.is_repo():
            return CmdResult(False, 1, "", "not a git repo", ["git", "branch"])
        args = ["branch", name] + ([base] if base else [])
        return self._run(args)

    def add_worktree(self, branch: str, path: Path) -> CmdResult:
        if not self.is_repo():
            return CmdResult(False, 1, "", "not a git repo", ["git", "worktree"])
        path.parent.mkdir(parents=True, exist_ok=True)
        return self._run(["worktree", "add", str(path), branch])

    def remove_worktree(self, path: Path, force: bool = False) -> CmdResult:
        if not self.is_repo():
            return CmdResult(False, 1, "", "not a git repo", ["git", "worktree"])
        args = ["worktree", "remove", str(path)]
        if force:
            args.append("--force")
        return self._run(args)
