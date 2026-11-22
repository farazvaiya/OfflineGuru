"""Independent evaluator: verify evidence and produce neutral comparisons."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from . import paths
from .benchmark import BenchmarkEngine
from .db import Database
from .paths import ensure_workspace


class IndependentEvaluator:
    def __init__(self, db: Database) -> None:
        ensure_workspace()
        self.db = db
        self.bench = BenchmarkEngine(db)
        self.out_dir = Path(paths.TEAM_ROOT) / "evaluations"
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def evaluate_problem(self, problem_id: str, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
        exps = self.db.query("SELECT * FROM experiments WHERE problem_id=? ORDER BY created_at", (problem_id,))
        benches = self.db.query("SELECT * FROM benchmarks WHERE problem_id=? ORDER BY created_at", (problem_id,))
        by_track = {}
        for e in exps:
            track = e.get("track")
            # parse JSON fields defensively
            def pj(s):
                try: return json.loads(s) if isinstance(s, str) else s
                except Exception: return s
            e2 = dict(e)
            e2["implementation"] = pj(e.get("implementation_json"))
            e2["test_results"] = pj(e.get("test_results_json"))
            by_track[track] = e2
        comparisons = {}
        base_results = baseline or {}
        for b in benches:
            try:
                res = json.loads(b.get("results_json") or "{}")
            except Exception:
                res = {}
            comparisons[b["id"]] = {"experiment_id": b.get("experiment_id"), "delta_vs_baseline": self.bench.compare(base_results, res)}

        limitations = []
        if not base_results:
            limitations.append("No baseline results supplied; baseline deltas cannot be computed.")
        if not by_track.get("B"):
            limitations.append("Track B (open-source) experiment not recorded for this problem.")
        if not benches:
            limitations.append("No benchmark records found; comparison is incomplete.")

        # An experiment record is not evidence of an implementation. Each track must
        # name the files it changed and a test result before a comparison is credible.
        for track, exp in by_track.items():
            impl = exp.get("implementation") or {}
            files = impl.get("files") if isinstance(impl, dict) else None
            if not files:
                limitations.append(f"Track {track} names no changed files; no implementation to compare.")
            tests = exp.get("test_results") or {}
            passed = tests.get("passed") if isinstance(tests, dict) else None
            if not passed:
                limitations.append(f"Track {track} has no passing test evidence recorded.")

        verdict = "inconclusive"
        substantive_comparison = (
            bool(by_track.get("A")) and bool(by_track.get("B")) and bool(benches)
            and not any("no implementation" in lim or "no passing test" in lim for lim in limitations)
        )
        if substantive_comparison:
            verdict = "evidence_recorded"

        eval_id = uuid.uuid4().hex[:12]
        payload = {
            "id": eval_id,
            "problem_id": problem_id,
            "created_at": time.time(),
            "tracks": by_track,
            "benchmarks": benches,
            "comparisons": comparisons,
            "limitations": limitations,
            "verdict": verdict,
            "independence_note": "Evaluator compares recorded artifacts/results; does not accept agent claims without evidence fields.",
        }
        report_path = self.out_dir / f"EVAL_{eval_id}.json"
        report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        pid = problem_id
        if pid is not None and self.db.query_one("SELECT id FROM problems WHERE id=?", (pid,)) is None:
            limitations.append(f"Problem {pid} not found in DB; evaluation stored without FK reference.")
            payload["limitations"] = limitations
            pid = None
        self.db.execute(
            "INSERT INTO evaluations(id,problem_id,evaluator,verdict,report_path,comparison_json,limitations_json,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (eval_id, pid, "independent-evaluator", verdict, str(report_path), json.dumps(comparisons, default=str), json.dumps(payload["limitations"]), time.time()),
        )
        return payload
