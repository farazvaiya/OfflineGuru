"""Isolated candidate workspace for Track A/B development (Phase 6).

No git in this workspace, so isolation uses the same pattern as
engineering_harness checkpoints: filesystem snapshot + source digest +
single-writer lock + concurrent-mutation detection.

Contract:
  - create(track)   -> snapshot of declared source paths, unique dir
  - candidate(track)-> path the agent may write inside
  - digest(track)   -> sha256 of all snapshot files (proves isolation)
  - verify(track)   -> detects concurrent mutation (digest change by others)
  - release(track)  -> explicit single-writer lock release

Candidates NEVER overwrite each other: each gets its own directory tree.
The live tree is only touched by the human-approved integration step (Phase 8),
not here.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
import tarfile
import time
import uuid
from pathlib import Path
from typing import Any

from ..harness.paths import TEAM_ROOT

DEFAULT_SOURCE_PATHS = [
    "guru/pipeline.py", "guru/realtime.py", "guru/audio.py", "guru/asr.py",
    "guru/translate.py", "guru/tts.py", "guru/storage.py", "guru/bus.py",
    "guru/server/app.py", "config/default.toml",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class IsolationError(RuntimeError):
    pass


class IsolatedWorkspace:
    def __init__(self, repo: Path | None = None, root: Path | None = None,
                 source_paths: list[str] | None = None) -> None:
        self.repo = Path(repo) if repo else (TEAM_ROOT.parent)
        self.root = Path(root) if root else (TEAM_ROOT / "experiments" / "candidates")
        self.source_paths = source_paths or DEFAULT_SOURCE_PATHS
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, Any] = {}

    def _digest_dir(self, d: Path) -> str:
        h = hashlib.sha256()
        for p in sorted(d.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts \
                    and p.name not in ("manifest.json", ".lock"):
                h.update(str(p.relative_to(d)).encode())
                h.update(sha256_file(p).encode())
        return h.hexdigest()[:16]

    def create(self, track: str, label: str = "") -> dict[str, Any]:
        if track not in ("A", "B", "HYBRID"):
            raise IsolationError(f"bad track {track!r}")
        cid = uuid.uuid4().hex[:8]
        dest = self.root / f"{track}-{cid}"
        manifest = {
            "id": cid, "track": track, "label": label,
            "created_at": time.time(), "source_digest": None, "files": {},
        }
        dest.mkdir(parents=True, exist_ok=True)
        for rel in self.source_paths:
            src = self.repo / rel
            if not src.exists():
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if src.is_file():
                shutil.copy2(src, target)
                manifest["files"][rel] = sha256_file(src)
            else:
                shutil.copytree(src, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                for f in target.rglob("*"):
                    if f.is_file() and "__pycache__" not in f.parts:
                        manifest["files"][str(f.relative_to(dest))] = sha256_file(f)
        manifest["source_digest"] = self._digest_dir(dest)
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2))
        # single-writer lock
        lock = (dest / ".lock").open("w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise IsolationError(f"workspace {dest} already locked")
        self._locks[track] = lock
        return {"id": cid, "track": track, "path": str(dest),
                "source_digest": manifest["source_digest"], "files": len(manifest["files"])}

    def candidate(self, track: str) -> Path:
        for d in sorted(self.root.glob(f"{track}-*")):
            if (d / "manifest.json").is_file():
                return d
        raise IsolationError(f"no candidate workspace for track {track}; call create() first")

    def digest(self, track: str) -> str:
        return self._digest_dir(self.candidate(track))

    def verify(self, track: str) -> dict[str, Any]:
        """Detect concurrent mutation: compare manifest digest to current tree."""
        dest = self.candidate(track)
        manifest = json.loads((dest / "manifest.json").read_text())
        current = self._digest_dir(dest)
        return {
            "track": track,
            "manifest_digest": manifest["source_digest"],
            "current_digest": current,
            "unchanged": manifest["source_digest"] == current,
        }

    def release(self, track: str) -> None:
        lock = self._locks.pop(track, None)
        if lock is not None:
            try:
                fcntl.flock(lock, fcntl.LOCK_UN)
            except OSError:
                pass
            lock.close()
