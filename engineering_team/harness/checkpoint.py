"""Checkpoint and rollback for the harness.

Creates tarball snapshots of key source/config areas and can restore them.
Does not touch user data stores outside declared source paths unless explicitly requested.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import paths as _paths
from .paths import REPO_ROOT, ensure_workspace

DEFAULT_SOURCE_PATHS = ["guru", "config", "scripts", "tests", "engineering_team", "engineering_harness", "docs", "README.md", "pyproject.toml", "Makefile", "requirements.txt", "requirements-dev.txt"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# Harness state is never part of a source checkpoint: archiving it would capture
# the live database, previous checkpoint archives and run logs, growing each
# snapshot and leaking problem text into every tarball.
EXCLUDED_PREFIXES = (
    "engineering_team/checkpoints",
    "engineering_team/state",
    "engineering_team/logs",
    "engineering_team/__pycache__",
    ".tmp",
)


def _is_excluded(rel: str) -> bool:
    return any(rel == p or rel.startswith(p + "/") for p in EXCLUDED_PREFIXES)


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    name = info.name
    if "__pycache__" in name or name.endswith((".pyc", ".pyo")):
        return None
    if "/.git/" in name or name.startswith(".git/"):
        return None
    if "/data/" in name or name.startswith("data/"):
        return None
    if _is_excluded(name):
        return None
    return info


class CheckpointManager:
    def __init__(self, repo_root: Path = REPO_ROOT) -> None:
        ensure_workspace()
        self.repo_root = repo_root
        self.root = Path(_paths.TEAM_ROOT) / "checkpoints"
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, label: str, kind: str = "manual", paths: list[str] | None = None) -> dict[str, Any]:
        paths = paths or DEFAULT_SOURCE_PATHS
        cid = time.strftime("%Y%m%d-%H%M%S") + "-" + hashlib.sha256((label + str(time.time())).encode()).hexdigest()[:6]
        dest = self.root / cid
        dest.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, Any] = {
            "id": cid,
            "label": label,
            "kind": kind,
            "created_at": _now_iso(),
            "paths": paths,
            "files": {},
        }
        tar_path = dest / "source.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            for rel in paths:
                p = self.repo_root / rel
                if not p.exists():
                    continue
                tar.add(p, arcname=rel, filter=_tar_filter)
                if p.is_file():
                    manifest["files"][rel] = _sha256(p)
                else:
                    for f in p.rglob("*"):
                        rel = str(f.relative_to(self.repo_root))
                        if (f.is_file() and "__pycache__" not in f.parts
                                and ".git" not in f.parts and not _is_excluded(rel)):
                            manifest["files"][rel] = _sha256(f)
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return manifest

    def list(self) -> list[dict[str, Any]]:
        out = []
        for d in sorted(self.root.iterdir()):
            m = d / "manifest.json"
            if m.is_file():
                try:
                    out.append(json.loads(m.read_text(encoding="utf-8")))
                except Exception:
                    pass
        return out

    def restore(self, checkpoint_id: str, yes: bool = False) -> dict[str, Any]:
        dest = self.root / checkpoint_id
        tar_path = dest / "source.tar.gz"
        if not tar_path.is_file():
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_id}")
        if not yes:
            raise ValueError("Restore is destructive to tracked source paths; pass yes=True to confirm")
        safety = self.create(f"pre-restore-of-{checkpoint_id}", kind="auto")
        # Extract over repo root; tar contains relative paths captured at create time.
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(self.repo_root, filter="data")
        return {"restored": checkpoint_id, "safety_checkpoint": safety["id"]}
