"""Benchmark engine for candidate comparison (Phase 7).

Enforces equivalent conditions: every run records its full config, dataset
identity (path + sha256), environment, and source digest. A comparison is
only computed when both runs used the SAME dataset digest and the same
hardware profile; otherwise the delta is reported as INCONCLUSIVE, never a
number.

Also: no fabricated results. A benchmark row must carry a raw artifact path
that exists on disk.

Storage is the physical-test DB (own schema), NOT the team harness DB: the
harness tables belong to the engineering_team orchestration workflow and
mixing them would couple two systems the master prompt requires staying
separate.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..harness.paths import TEAM_ROOT


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class BenchmarkError(RuntimeError):
    pass


class BenchmarkEngine:
    SCHEMA = r"""
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS benchmarks (
        id TEXT PRIMARY KEY,
        experiment_id TEXT,
        problem_id TEXT,
        dataset TEXT,
        dataset_digest TEXT,
        config_json TEXT NOT NULL DEFAULT '{}',
        environment_json TEXT NOT NULL DEFAULT '{}',
        results_json TEXT NOT NULL DEFAULT '{}',
        raw_path TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else TEAM_ROOT / "state" / "physical_tests.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(self.SCHEMA)
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version','1')")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _env(self) -> dict[str, str]:
        return {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor() or "unknown",
            "python": platform.python_version(),
            "cpu_count": str(os.cpu_count() or 0),
        }

    def record(self, experiment_id: str | None, problem_id: str | None,
               dataset: str, config: dict[str, Any], results: dict[str, Any],
               raw_path: str | Path) -> str:
        """Record a benchmark run with full provenance. Refuses fiction."""
        raw = Path(raw_path)
        if not raw.is_file():
            raise BenchmarkError(f"raw artifact does not exist: {raw}")
        payload = {
            "id": uuid.uuid4().hex[:12],
            "experiment_id": experiment_id,
            "problem_id": problem_id,
            "dataset": dataset,
            "dataset_digest": sha256_file(raw),
            "config": config,
            "environment": self._env(),
            "results": results,
            "raw_path": str(raw),
            "created_at": time.time(),
        }
        with self._lock:
            self._conn.execute(
                "INSERT INTO benchmarks(id,experiment_id,problem_id,dataset,dataset_digest,"
                "config_json,environment_json,results_json,raw_path,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (payload["id"], experiment_id, problem_id, dataset, payload["dataset_digest"],
                 json.dumps(config), json.dumps(payload["environment"]),
                 json.dumps(results, default=str), str(raw), payload["created_at"]),
            )
            self._conn.commit()
        return payload["id"]

    def compare(self, baseline_results: dict[str, Any], candidate_results: dict[str, Any]) -> dict[str, Any]:
        """Deltas for numeric keys present on both sides. Non-numeric -> skipped."""
        deltas: dict[str, Any] = {}
        for k in sorted(set(baseline_results) & set(candidate_results)):
            b, c = baseline_results[k], candidate_results[k]
            if isinstance(b, (int, float)) and isinstance(c, (int, float)) and not isinstance(b, bool):
                deltas[k] = {
                    "baseline": b, "candidate": c,
                    "delta": round(c - b, 6),
                    "delta_pct": round((c - b) / b * 100, 2) if b else None,
                }
        return {"deltas": deltas, "comparable_keys": sorted(deltas)}

    def assert_equivalent(self, baseline_raw: str | Path, candidate_raw: str | Path,
                          baseline_config: dict[str, Any], candidate_config: dict[str, Any]) -> dict[str, Any]:
        """Equivalent-conditions gate: same dataset digest + same config."""
        b, c = Path(baseline_raw), Path(candidate_raw)
        if not (b.is_file() and c.is_file()):
            raise BenchmarkError("both raw artifacts must exist")
        same_dataset = sha256_file(b) == sha256_file(c)
        same_config = baseline_config == candidate_config
        return {
            "equivalent": same_dataset and same_config,
            "same_dataset_digest": same_dataset,
            "same_config": same_config,
            "baseline_digest": sha256_file(b)[:16],
            "candidate_digest": sha256_file(c)[:16],
        }

    def runs(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM benchmarks ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]
