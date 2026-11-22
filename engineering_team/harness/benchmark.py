"""Benchmark framework: record configs, results, and comparisons."""

from __future__ import annotations

import json
import platform
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import paths
from .db import Database
from .paths import ensure_workspace


@dataclass
class BenchmarkResult:
    id: str
    problem_id: str | None
    experiment_id: str | None
    dataset: str | None
    config: dict[str, Any]
    results: dict[str, Any]
    raw_path: str | None
    created_at: float
    environment: dict[str, Any]


class BenchmarkEngine:
    def __init__(self, db: Database) -> None:
        ensure_workspace()
        self.db = db
        self.out_dir = Path(paths.TEAM_ROOT) / "benchmarks"
        self.out_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def environment() -> dict[str, Any]:
        return {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": __import__("os").cpu_count(),
        }

    def record(self, problem_id: str | None, experiment_id: str | None, dataset: str | None,
               config: dict[str, Any], results: dict[str, Any], raw: Any | None = None) -> BenchmarkResult:
        bid = uuid.uuid4().hex[:12]
        created = time.time()
        raw_path = None
        if raw is not None:
            raw_path = str(self.out_dir / f"{bid}_raw.json")
            Path(raw_path).write_text(json.dumps(raw, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        env = self.environment()
        payload = {
            "id": bid, "problem_id": problem_id, "experiment_id": experiment_id,
            "dataset": dataset, "config": config, "results": results,
            "raw_path": raw_path, "created_at": created, "environment": env,
        }
        self.db.execute(
            "INSERT INTO benchmarks(id,experiment_id,problem_id,dataset,config_json,results_json,raw_path,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (bid, experiment_id, problem_id, dataset, json.dumps(config), json.dumps(results, default=str), raw_path, created),
        )
        (self.out_dir / f"{bid}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return BenchmarkResult(**payload)

    @staticmethod
    def compare(baseline: dict[str, Any], candidate: dict[str, Any], metric_keys: list[str] | None = None) -> dict[str, Any]:
        """Compute simple deltas for numeric metrics present in both dicts."""
        keys = metric_keys or sorted(set(baseline.keys()) & set(candidate.keys()))
        deltas = {}
        for k in keys:
            b, c = baseline.get(k), candidate.get(k)
            if isinstance(b, (int, float)) and isinstance(c, (int, float)):
                deltas[k] = {"baseline": b, "candidate": c, "delta": c - b}
        return {"metrics": keys, "deltas": deltas}
