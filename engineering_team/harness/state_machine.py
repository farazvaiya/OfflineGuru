"""Validated workflow state machine for the engineering harness."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import paths as _paths
from .paths import ensure_workspace

STATES = [
    "IDLE",
    "PROBLEM_DISCOVERED",
    "ROOT_CAUSE_ANALYSIS",
    "BASELINE_CREATED",
    "DUAL_TRACK_RUNNING",
    "BENCHMARKING",
    "INDEPENDENT_EVALUATION",
    "WAITING_FOR_HUMAN_DECISION",
    "APPROVED",
    "REJECTED",
    "INTEGRATION",
    "REGRESSION_TEST",
    "PHYSICAL_TEST_REQUIRED",
    "COMPLETED",
    "FAILED",
    "PAUSED",
]

TRANSITIONS: dict[str, set[str]] = {
    "IDLE": {"PROBLEM_DISCOVERED", "PAUSED", "FAILED"},
    "PROBLEM_DISCOVERED": {"ROOT_CAUSE_ANALYSIS", "FAILED", "PAUSED"},
    "ROOT_CAUSE_ANALYSIS": {"BASELINE_CREATED", "FAILED", "PAUSED", "PROBLEM_DISCOVERED"},
    "BASELINE_CREATED": {"DUAL_TRACK_RUNNING", "FAILED", "PAUSED"},
    "DUAL_TRACK_RUNNING": {"BENCHMARKING", "FAILED", "PAUSED"},
    "BENCHMARKING": {"INDEPENDENT_EVALUATION", "FAILED", "PAUSED", "DUAL_TRACK_RUNNING"},
    "INDEPENDENT_EVALUATION": {"WAITING_FOR_HUMAN_DECISION", "FAILED", "PAUSED", "BENCHMARKING"},
    # REQUEST_MORE_TESTS re-opens the evidence phase, so the benchmark/track states
    # are reachable from the decision gate as well as forward to approval/rejection.
    "WAITING_FOR_HUMAN_DECISION": {"APPROVED", "REJECTED", "PHYSICAL_TEST_REQUIRED", "PAUSED",
                                    "FAILED", "BENCHMARKING", "DUAL_TRACK_RUNNING"},
    "PHYSICAL_TEST_REQUIRED": {"INDEPENDENT_EVALUATION", "WAITING_FOR_HUMAN_DECISION", "PAUSED", "FAILED"},
    "APPROVED": {"INTEGRATION", "FAILED", "PAUSED"},
    "REJECTED": {"PROBLEM_DISCOVERED", "IDLE", "FAILED", "PAUSED"},
    "INTEGRATION": {"REGRESSION_TEST", "FAILED", "PAUSED"},
    "REGRESSION_TEST": {"COMPLETED", "FAILED", "INTEGRATION", "PAUSED"},
    "COMPLETED": {"IDLE", "PROBLEM_DISCOVERED"},
    "FAILED": {"IDLE", "PAUSED", "PROBLEM_DISCOVERED"},
    "PAUSED": set(),  # resume restores previous_state
}

# Human approval is a mandatory gate: no transition to INTEGRATION except from APPROVED.
APPROVAL_GATE_TARGET = "INTEGRATION"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class WorkflowState:
    state: str = "IDLE"
    previous_state: str | None = None
    problem_id: str | None = None
    selected_candidate: str | None = None
    retry_count: int = 0
    updated_at: str = field(default_factory=_now)
    history: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> WorkflowState:
        # Resolve at call time so a relocated/paused workspace is honoured.
        path = path or _paths.WORKFLOW_PATH
        ensure_workspace()
        if not path.exists():
            ws = cls()
            ws.save(path)
            return ws
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            # Corrupt state: preserve by moving aside and start fresh but paused/failed-safe.
            bak = path.with_suffix(".corrupt")
            try:
                path.replace(bak)
            except Exception:
                pass
            ws = cls(state="FAILED", meta={"corrupt_state_backup": str(bak)})
            ws.save(path)
            return ws
        return cls(
            state=raw.get("state", "IDLE"),
            previous_state=raw.get("previous_state"),
            problem_id=raw.get("problem_id"),
            selected_candidate=raw.get("selected_candidate"),
            retry_count=int(raw.get("retry_count", 0)),
            updated_at=raw.get("updated_at", _now()),
            history=list(raw.get("history", [])),
            meta=dict(raw.get("meta", {})),
        )

    def save(self, path: Path | None = None) -> None:
        path = path or _paths.WORKFLOW_PATH
        self.updated_at = _now()
        _atomic_write(path, {
            "state": self.state,
            "previous_state": self.previous_state,
            "problem_id": self.problem_id,
            "selected_candidate": self.selected_candidate,
            "retry_count": self.retry_count,
            "updated_at": self.updated_at,
            "history": self.history,
            "meta": self.meta,
        })

    def can_transition(self, new_state: str) -> tuple[bool, str]:
        if new_state not in STATES:
            return False, f"Unknown state: {new_state}"
        if self.state == "PAUSED":
            # Resume handled separately
            return False, "Cannot transition while PAUSED; use resume()"
        if new_state == APPROVAL_GATE_TARGET and self.state != "APPROVED":
            return False, "Approval gate: INTEGRATION requires APPROVED state (explicit human approval)"
        allowed = TRANSITIONS.get(self.state, set())
        if new_state not in allowed and new_state != self.state:
            return False, f"Illegal transition {self.state} -> {new_state}; allowed: {sorted(allowed)}"
        return True, "ok"

    def transition(self, new_state: str, reason: str = "", actor: str = "orchestrator",
                   evidence: list[Any] | None = None) -> dict[str, Any]:
        ok, msg = self.can_transition(new_state)
        if not ok:
            raise ValueError(msg)
        event = {
            "ts": _now(),
            "from": self.state,
            "to": new_state,
            "reason": reason,
            "actor": actor,
            "evidence": evidence or [],
        }
        self.previous_state = self.state
        self.state = new_state
        self.history.append(event)
        self.save()
        return event

    def pause(self, reason: str = "user requested", actor: str = "user") -> dict[str, Any]:
        if self.state == "PAUSED":
            raise ValueError("Already paused")
        event = {
            "ts": _now(),
            "from": self.state,
            "to": "PAUSED",
            "reason": reason,
            "actor": actor,
            "evidence": [],
        }
        self.previous_state = self.state
        self.state = "PAUSED"
        self.history.append(event)
        self.save()
        return event

    def resume(self, actor: str = "user") -> dict[str, Any]:
        if self.state != "PAUSED":
            raise ValueError(f"Not paused (current: {self.state})")
        target = self.previous_state or "IDLE"
        event = {
            "ts": _now(),
            "from": "PAUSED",
            "to": target,
            "reason": "resume",
            "actor": actor,
            "evidence": [],
        }
        self.state = target
        self.history.append(event)
        self.save()
        return event
