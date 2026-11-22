"""Experiment and candidate-solution tracking."""

from __future__ import annotations

import json
from typing import Any

from .db import Database


class ExperimentManager:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, problem_id: str | None, track: str, hypothesis: str = "",
               branch: str | None = None, worktree: str | None = None) -> str:
        track = track.upper()
        if track not in {"A", "B", "HYBRID"}:
            raise ValueError("track must be A, B, or HYBRID")
        return self.db.create_experiment(problem_id=problem_id, track=track, hypothesis=hypothesis,
                                         branch=branch, worktree=worktree)

    def update(self, experiment_id: str, **fields: Any) -> bool:
        return self.db.update_experiment(experiment_id, **fields)

    def list_for_problem(self, problem_id: str) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM experiments WHERE problem_id=? ORDER BY created_at", (problem_id,))

    def record_implementation(self, experiment_id: str, implementation: dict[str, Any], status: str = "implemented") -> bool:
        return self.update(experiment_id, implementation_json=json.dumps(implementation), status=status)

    def record_tests(self, experiment_id: str, tests: dict[str, Any], status: str | None = None) -> bool:
        fields: dict[str, Any] = {"test_results_json": json.dumps(tests)}
        if status:
            fields["status"] = status
        return self.update(experiment_id, **fields)
