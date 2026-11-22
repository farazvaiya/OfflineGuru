"""Workspace provisioning for agent runs.

Three distinct zones are kept strictly separate:

  source/      a per-run snapshot of the project's source files.
               - "inspect" mode: frozen read-only after seeding (chmod 0444/0555)
               - "edit" mode:    writable *copy*; the real tree is never touched
  artifacts/   everything the agent produces. Retained with the run.
  (project)    the real checkout: never mounted, never the cwd of an agent.

The snapshot is an explicit allowlist of source directories/files, not a copy of
the repository root: virtualenvs, models, data, harness state, logs, checkpoints
and caches are never included, so an agent cannot read the live database, prior
checkpoint archives or unrelated user files through its workspace.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import paths as _paths

# Only these top-level entries are snapshotted into a workspace.
SOURCE_ALLOWLIST: tuple[str, ...] = (
    "guru",
    "scripts",
    "tests",
    "config",
    "docs",
    "engineering_team",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "README.md",
    "Makefile",
    "LICENSES.md",
)

# Never snapshot these directory names, wherever they appear.
SKIP_DIR_NAMES = frozenset({
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "node_modules", "state", "logs", "checkpoints", "data",
    "models", ".tmp", "agent_workspaces",
})

SKIP_FILE_SUFFIXES = (".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log", ".onnx", ".bin")
MAX_FILE_BYTES = 2 * 1024 * 1024

INSPECT = "inspect"
EDIT = "edit"

# Roles whose work is analysis/review get a frozen source snapshot; roles that
# implement or test get a writable candidate copy.
READ_ONLY_ROLES = frozenset({
    "chief-orchestrator",
    "problem-discovery",
    "root-cause-analysis",
    "opensource-research-track-b",
    "independent-evaluator",
})

METADATA_NAME = ".workspace.json"
ARTIFACTS_DIR = "artifacts"
SOURCE_DIR = "source"


def mode_for_role(agent: str, requested: str | None = None) -> str:
    if requested in (INSPECT, EDIT):
        return requested
    if os.environ.get("TEAM_WORKSPACE_MODE") in (INSPECT, EDIT):
        return os.environ["TEAM_WORKSPACE_MODE"]
    return INSPECT if (agent or "").strip().lower() in READ_ONLY_ROLES else EDIT


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_source_files(repo_root: Path) -> Iterable[Path]:
    for entry in SOURCE_ALLOWLIST:
        p = repo_root / entry
        if not p.exists():
            continue
        if p.is_file():
            if not p.is_symlink():
                yield p
            continue
        for dirpath, dirnames, filenames in os.walk(p, followlinks=False):
            d = Path(dirpath)
            dirnames[:] = sorted(n for n in dirnames if n not in SKIP_DIR_NAMES)
            for name in sorted(filenames):
                f = d / name
                if f.is_symlink() or name.endswith(SKIP_FILE_SUFFIXES):
                    continue
                try:
                    if f.stat().st_size > MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                yield f


def digest_tree(root: Path) -> str:
    """Content digest of a seeded source tree, stable across runs."""
    h = hashlib.sha256()
    if not root.is_dir():
        return ""
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        h.update(str(p.relative_to(root)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:32]


@dataclass
class WorkspaceSpec:
    run_dir: Path
    mode: str
    source_dir: Path
    artifacts_dir: Path
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def metadata_path(self) -> Path:
        return self.run_dir / METADATA_NAME


class WorkspaceProvisioner:
    """Create an isolated, self-describing workspace for one agent run."""

    def __init__(self, repo_root: Path | None = None) -> None:
        self._repo_root = Path(repo_root) if repo_root else None

    @property
    def repo_root(self) -> Path:
        return self._repo_root or _paths.REPO_ROOT

    def seed(self, run_dir: Path, mode: str = EDIT, *, track: str | None = None) -> WorkspaceSpec:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        source_dir = run_dir / SOURCE_DIR
        artifacts_dir = run_dir / ARTIFACTS_DIR
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        if source_dir.exists():
            shutil.rmtree(source_dir)
        source_dir.mkdir(parents=True)

        files: dict[str, str] = {}
        for f in _iter_source_files(self.repo_root):
            rel = f.relative_to(self.repo_root)
            dest = source_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            files[str(rel)] = sha256_file(dest)

        if mode == INSPECT:
            _freeze(source_dir)

        spec = WorkspaceSpec(
            run_dir=run_dir, mode=mode, source_dir=source_dir, artifacts_dir=artifacts_dir,
            manifest={
                "run_dir": str(run_dir),
                "mode": mode,
                "track": track,
                "repo_root": str(self.repo_root),
                "source_digest": digest_tree(source_dir),
                "files": files,
                "artifacts_dir": str(artifacts_dir),
            },
        )
        spec.metadata_path.write_text(json.dumps(spec.manifest, indent=2, sort_keys=True) + "\n",
                                      encoding="utf-8")
        return spec

    def source_changes(self, spec: WorkspaceSpec) -> list[dict[str, str]]:
        """Files under source/ whose content differs from the seeded snapshot."""
        baseline = spec.manifest.get("files", {})
        current: dict[str, str] = {}
        if spec.source_dir.is_dir():
            for p in spec.source_dir.rglob("*"):
                if p.is_file():
                    current[str(p.relative_to(spec.source_dir))] = sha256_file(p)
        changes = []
        for rel, before in sorted(baseline.items()):
            after = current.get(rel)
            if after is None:
                changes.append({"path": rel, "change": "deleted", "before": before, "after": None})
            elif after != before:
                changes.append({"path": rel, "change": "modified", "before": before, "after": after})
        for rel in sorted(set(current) - set(baseline)):
            changes.append({"path": rel, "change": "added", "before": None, "after": current[rel]})
        return changes


def _freeze(root: Path) -> None:
    """Make a seeded tree readable but not writable (POSIX; best effort elsewhere)."""
    if os.name == "nt":  # pragma: no cover - documented limitation
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                os.chmod(Path(dirpath) / name, 0o444)
            except OSError:
                pass
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        try:
            os.chmod(dirpath, 0o555)
        except OSError:
            pass


def thaw(root: Path) -> None:
    """Restore write permission on a frozen tree (cleanup/archival only)."""
    if os.name == "nt":  # pragma: no cover
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        try:
            os.chmod(dirpath, 0o755)
        except OSError:
            pass
        for name in filenames:
            try:
                os.chmod(Path(dirpath) / name, 0o644)
            except OSError:
                pass


def list_artifacts(spec: WorkspaceSpec) -> list[str]:
    if not spec.artifacts_dir.is_dir():
        return []
    return [str(p) for p in sorted(spec.artifacts_dir.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts]
