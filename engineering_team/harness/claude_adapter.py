"""Execution adapter for agent labor.

This harness runs in constrained environments and must not assume an external
Claude Code runtime is available. The adapter provides a uniform interface to
record agent runs and to invoke local callable "agents" (Python functions or
commands) when configured. External execution can be integrated by implementing
the same interface.

When a real ClaudeCodeRuntime (harness/claude_runtime.py) is attached, runs go
through the installed claude CLI. Unregistered agents without a runtime still
return pending_external — execution is genuinely delegated, never fabricated.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .db import Database


@dataclass
class AgentRunResult:
    run_id: str
    status: str
    output: dict[str, Any]
    error: str | None = None


class ClaudeCodeAdapter:
    """Records agent execution; can run registered local handlers or a runtime."""

    def __init__(self, db: Database, runtime: Any = None) -> None:
        self.db = db
        self._handlers: dict[str, Callable[..., dict[str, Any]]] = {}
        self.runtime = runtime  # optional AgentExecution from claude_runtime.py

    def register(self, agent_name: str, handler: Callable[..., dict[str, Any]]) -> None:
        self._handlers[agent_name] = handler

    def attach_runtime(self, runtime: Any) -> None:
        self.runtime = runtime

    def run(self, agent: str, task: dict[str, Any] | None = None, problem_id: str | None = None,
            **kwargs: Any) -> AgentRunResult:
        handler = self._handlers.get(agent)
        if self.runtime is not None and handler is None:
            # the real runtime owns the agent_runs row (insert + finish);
            # this adapter must not pre-insert a competing row.
            result = self.runtime.run(agent=agent, task=task, problem_id=problem_id, **kwargs)
            status = str(result.get("status", "failed"))
            return AgentRunResult(run_id=result.get("run_id", ""), status=status,
                                  output=result, error=result.get("error"))
        run_id = uuid.uuid4().hex[:12]
        started = time.time()
        self.db.execute(
            "INSERT INTO agent_runs(id,agent,task_id,problem_id,status,started_at,output_json)"
            " VALUES(?,?,?,?,?,?,?)",
            (run_id, agent, (task or {}).get("id"), problem_id, "running", started, "{}"),
        )
        try:
            if handler is None:
                output = {
                    "status": "not_configured",
                    "message": f"No local handler registered for agent '{agent}'. "
                               "Execution must be performed by the configured runtime and recorded here.",
                    "task": task,
                }
                status = "pending_external"
            else:
                output = handler(task=task, problem_id=problem_id, db=self.db, **kwargs) or {}
                status = str(output.get("status", "completed"))
            self.db.execute(
                "UPDATE agent_runs SET status=?, finished_at=?, output_json=? WHERE id=?",
                (status, time.time(), json.dumps(output, default=str), run_id),
            )
            return AgentRunResult(run_id=run_id, status=status, output=output)
        except Exception as e:
            self.db.execute(
                "UPDATE agent_runs SET status=?, finished_at=?, error=?, output_json=? WHERE id=?",
                ("failed", time.time(), str(e), json.dumps({"exception": str(e)}), run_id),
            )
            return AgentRunResult(run_id=run_id, status="failed", output={}, error=str(e))
