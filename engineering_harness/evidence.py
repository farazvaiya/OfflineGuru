"""Evidence stamping shared by harness checks and harness benchmark runners.

Every artifact written here records the source digest it was produced from, so a
gate can tell the difference between "measured on the current tree" and "measured
on last week's code that happens to still be lying around". Without that, a stale
green artifact is indistinguishable from a fresh one.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HARNESS = Path(__file__).resolve().parent
REPO = HARNESS.parent
ARTIFACTS = HARNESS / "artifacts"

SOURCE_PATHS = [
    "guru",
    "tests",
    "scripts",
    "config",
    "docs",
    "Makefile",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-prep.txt",
    "README.md",
    "LICENSES.md",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iter_source_files() -> list[Path]:
    out: list[Path] = []
    for rel in SOURCE_PATHS:
        p = REPO / rel
        if not p.exists():
            continue
        if p.is_file():
            out.append(p)
        else:
            out.extend(sorted(f for f in p.rglob("*") if f.is_file() and "__pycache__" not in f.parts))
    return out


def source_digest() -> str:
    """Content digest of the source tree an artifact belongs to."""
    h = hashlib.sha256()
    for f in _iter_source_files():
        h.update(str(f.relative_to(REPO)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def source_mtime() -> float:
    return max((f.stat().st_mtime for f in _iter_source_files()), default=0.0)


def write(name: str, payload: dict[str, Any], command: str | None = None) -> Path:
    """Write artifacts/<name>.json plus an immutable history copy."""
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    doc = dict(payload)
    doc["_evidence"] = {
        "created_at": now_iso(),
        "source_digest": source_digest(),
        "source_mtime": source_mtime(),
        "command": command,
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "python": platform.python_version(),
    }
    path = ARTIFACTS / f"{name}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    hist = ARTIFACTS / "history"
    hist.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    (hist / f"{name}_{stamp}.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    return path


def load(path: Path | str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.is_absolute():
        p = HARNESS / p
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def is_current(doc: dict[str, Any] | None) -> bool:
    """True when the artifact was produced from the tree as it is right now."""
    if not doc:
        return False
    ev = doc.get("_evidence") or {}
    return ev.get("source_digest") == source_digest()
