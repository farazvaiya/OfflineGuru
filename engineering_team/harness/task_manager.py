"""Task manager for the engineering team."""

from __future__ import annotations

import json
import time
from typing import Any

from .db import Database


class TaskManager:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, title: str, owner: str, phase: str = "IDLE", problem_id: str | None = None,
            payload: dict[str, Any] | None = None) -> str:
        return self.db.create_task(title=title, owner=owner, phase=phase, problem_id=problem_id, payload=payload)

    def complete(self, task_id: str, result: dict[str, Any] | None = None) -> bool:
        return self.db.update_task(task_id, status="done", result_json=json.dumps(result or {}),
                                   completed_at=time.time())

    def fail(self, task_id: str, result: dict[str, Any] | None = None) -> bool:
        return self.db.update_task(task_id, status="failed", result_json=json.dumps(result or {}),
                                   completed_at=time.time())

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        return self.db.list_tasks(status=status)

    def open_for_owner(self, owner: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM tasks WHERE owner=? AND status='open' ORDER BY created_at", (owner,))
        return rows
