"""Telemetry evidence database for physical test monitoring.

Stores what the Phase 2 collectors produce, plus validation results, agent
findings, and student events. Migrations are numbered and additive, following
the engineering_team/harness/db.py convention. Nothing here writes to the
Offline Guru app database.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..harness.paths import TEAM_ROOT

SCHEMA_VERSION = 1

MIGRATIONS: dict[int, str] = {
    1: r"""
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS telemetry_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        ts REAL NOT NULL,
        collector TEXT NOT NULL,
        status TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        reason TEXT,
        source TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_te_session ON telemetry_events(session_id, ts);
    CREATE INDEX IF NOT EXISTS idx_te_collector ON telemetry_events(collector, ts);

    CREATE TABLE IF NOT EXISTS metric_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        ts REAL NOT NULL,
        kind TEXT NOT NULL,
        value REAL
    );
    CREATE INDEX IF NOT EXISTS idx_ms_kind ON metric_snapshots(kind, ts);

    CREATE TABLE IF NOT EXISTS error_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        ts REAL NOT NULL,
        component TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'error',
        message TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS student_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        ts REAL NOT NULL,
        event TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS validation_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        ts REAL NOT NULL,
        check_id TEXT NOT NULL,
        outcome TEXT NOT NULL,
        measured_json TEXT NOT NULL DEFAULT '{}',
        threshold_json TEXT NOT NULL DEFAULT '{}',
        evidence TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS agent_findings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        ts REAL NOT NULL,
        agent TEXT NOT NULL,
        finding TEXT NOT NULL,
        confidence TEXT NOT NULL DEFAULT 'unknown',
        evidence_json TEXT NOT NULL DEFAULT '[]'
    );
    """,
}


def _now() -> float:
    return time.time()


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class TelemetryDB:
    """Thread-safe SQLite for telemetry evidence (migration 1)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else TEAM_ROOT / "state" / "physical_tests.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
        self.migrate()

    @contextmanager
    def _tx(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def migrate(self) -> None:
        with self._tx() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            current = int(row["value"]) if row else 0
            for version in range(current + 1, SCHEMA_VERSION + 1):
                sql = MIGRATIONS.get(version)
                if not sql:
                    raise RuntimeError(f"Missing migration for version {version}")
                conn.executescript(sql)
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)",
                    (str(version),),
                )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- writes -------------------------------------------------------------
    def record_collector_result(self, session_id: str | None, collector: str,
                                result: dict[str, Any]) -> int:
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO telemetry_events(session_id,ts,collector,status,payload_json,reason,source)"
                " VALUES(?,?,?,?,?,?,?)",
                (session_id, _now(), collector, result.get("status", "error"),
                 _js(result.get("measured") or {}),
                 result.get("reason"), result.get("source")),
            )
            return int(cur.lastrowid or 0)

    def record_error(self, session_id: str | None, component: str, message: str,
                     severity: str = "error") -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO error_events(session_id,ts,component,severity,message)"
                " VALUES(?,?,?,?,?)",
                (session_id, _now(), component, severity, message),
            )

    def record_student_event(self, session_id: str | None, event: str, detail: str = "") -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO student_events(session_id,ts,event,detail) VALUES(?,?,?,?)",
                (session_id, _now(), event, detail),
            )

    def record_validation(self, session_id: str | None, check_id: str, outcome: str,
                          measured: Any = None, threshold: Any = None, evidence: str = "",
                          notes: str = "") -> None:
        if outcome not in ("PASS", "FAIL", "INCONCLUSIVE", "NOT_EXECUTED"):
            raise ValueError(f"bad outcome {outcome!r}")
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO validation_results(session_id,ts,check_id,outcome,measured_json,"
                "threshold_json,evidence,notes) VALUES(?,?,?,?,?,?,?,?)",
                (session_id, _now(), check_id, outcome, _js(measured), _js(threshold),
                 evidence, notes),
            )

    def record_agent_finding(self, session_id: str | None, agent: str, finding: str,
                             confidence: str = "unknown", evidence: list[str] | None = None) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO agent_findings(session_id,ts,agent,finding,confidence,evidence_json)"
                " VALUES(?,?,?,?,?,?)",
                (session_id, _now(), agent, finding, confidence, _js(evidence or [])),
            )

    def record_metric(self, session_id: str | None, kind: str, value: float | None) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO metric_snapshots(session_id,ts,kind,value) VALUES(?,?,?,?)",
                (session_id, _now(), kind, value),
            )

    # -- reads --------------------------------------------------------------
    def events(self, session_id: str | None = None, collector: str | None = None,
               limit: int = 500) -> list[dict[str, Any]]:
        sql = "SELECT * FROM telemetry_events"
        where, params = [], []
        if session_id:
            where.append("session_id=?"); params.append(session_id)
        if collector:
            where.append("collector=?"); params.append(collector)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def validations(self, session_id: str | None = None) -> list[dict[str, Any]]:
        if session_id:
            rows = self._conn.execute(
                "SELECT * FROM validation_results WHERE session_id=? ORDER BY ts", (session_id,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM validation_results ORDER BY ts"
            ).fetchall()
        return [dict(r) for r in rows]

    def errors(self, session_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM error_events"
        params: list[Any] = []
        if session_id:
            sql += " WHERE session_id=?"; params.append(session_id)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def student_events(self, session_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        sql = "SELECT * FROM student_events"
        params: list[Any] = []
        if session_id:
            sql += " WHERE session_id=?"; params.append(session_id)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def latest_snapshot(self, session_id: str | None, collector: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM telemetry_events WHERE collector=? AND session_id IS ?"
            " ORDER BY ts DESC LIMIT 1",
            (collector, session_id),
        ).fetchone()
        return dict(row) if row else None


def drain_to_db(q: Any, db: TelemetryDB, session_id: str | None,
                stop, timeout_s: float = 0.2) -> int:
    """Move queued collector results into the DB; returns count stored."""
    stored = 0
    while True:
        try:
            item = q.get_nowait()
        except Exception:
            break
        db.record_collector_result(session_id, item["collector"], item)
        if item.get("status") == "error":
            db.record_error(session_id, item["collector"], str(item.get("reason"))[:500])
        stored += 1
        if stop.is_set():
            break
    return stored
