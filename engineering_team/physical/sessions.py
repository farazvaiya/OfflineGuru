"""Physical test session controller.

Real session lifecycle for human-run physical classroom tests of Offline Guru.
All state persists in engineering_team/state/physical_tests.db with versioned
migrations, mirroring the harness/db.py convention. This module never touches
guru/ production code: sessions record conditions and feedback; telemetry
collection is done by the Phase 2 collectors reading the app read-only.

Lifecycle: CREATED -> RUNNING -> PAUSED -> RUNNING -> STOPPED -> REPORTED.
Invalid transitions raise ValueError (tested).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
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

    CREATE TABLE IF NOT EXISTS physical_sessions (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'created',
        started_at REAL,
        paused_at REAL,
        stopped_at REAL,
        reported_at REAL,
        teacher_language TEXT NOT NULL DEFAULT 'en',
        target_language TEXT NOT NULL DEFAULT 'bn',
        expected_students INTEGER,
        actual_students INTEGER,
        microphone_info TEXT,
        laptop_info TEXT,
        student_devices TEXT,
        operating_system TEXT,
        network_config TEXT,
        internet_available INTEGER,
        room_noise TEXT,
        device_distances TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        notes TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS test_conditions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT,
        source TEXT NOT NULL DEFAULT 'human',
        recorded_at REAL NOT NULL,
        FOREIGN KEY (session_id) REFERENCES physical_sessions(id)
    );

    CREATE TABLE IF NOT EXISTS human_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'operator',
        category TEXT NOT NULL DEFAULT 'observation',
        body TEXT NOT NULL,
        recorded_at REAL NOT NULL,
        FOREIGN KEY (session_id) REFERENCES physical_sessions(id)
    );

    CREATE TABLE IF NOT EXISTS session_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        event TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        recorded_at REAL NOT NULL,
        FOREIGN KEY (session_id) REFERENCES physical_sessions(id)
    );

    CREATE INDEX IF NOT EXISTS idx_ps_status ON physical_sessions(status);
    CREATE INDEX IF NOT EXISTS idx_tc_session ON test_conditions(session_id);
    CREATE INDEX IF NOT EXISTS idx_hf_session ON human_feedback(session_id);
    CREATE INDEX IF NOT EXISTS idx_se_session ON session_events(session_id);
    """,
}

STATES = ["created", "running", "paused", "stopped", "reported"]

TRANSITIONS: dict[str, set[str]] = {
    "created": {"running"},
    "running": {"paused", "stopped"},
    "paused": {"running", "stopped"},
    "stopped": {"reported"},
    "reported": set(),
}

# Fields the public API may set on create/update (trust boundary).
CONDITION_FIELDS = {
    "teacher_language", "target_language", "expected_students", "actual_students",
    "microphone_info", "laptop_info", "student_devices", "operating_system",
    "network_config", "internet_available", "room_noise", "device_distances",
}


def _now() -> float:
    return time.time()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class SessionError(ValueError):
    """Invalid lifecycle operation or unknown session."""


class PhysicalTestDB:
    """Thread-safe SQLite for physical test sessions (migration 1)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else TEAM_ROOT / "state" / "physical_tests.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
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

    # -- sessions ----------------------------------------------------------
    def create_session(self, **fields: Any) -> dict[str, Any]:
        now = _now()
        sid = uuid.uuid4().hex[:12]
        cols = ["id", "created_at", "updated_at"]
        vals: list[Any] = [sid, now, now]
        for k in CONDITION_FIELDS:
            if k in fields and fields[k] is not None:
                cols.append(k)
                v = fields[k]
                if k == "internet_available":
                    v = 1 if v in (True, 1, "true", "yes") else 0 if v in (False, 0, "false", "no") else (1 if v else 0)
                vals.append(v)
        if "metadata" in fields:
            cols.append("metadata_json")
            vals.append(_json(fields["metadata"]))
        if "notes" in fields:
            cols.append("notes")
            vals.append(str(fields["notes"]))
        with self._tx() as conn:
            conn.execute(
                f"INSERT INTO physical_sessions({','.join(cols)})"
                f" VALUES({','.join('?' for _ in cols)})",
                vals,
            )
        return self.get_session(sid)

    def get_session(self, sid: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM physical_sessions WHERE id=?", (sid,)
        ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM physical_sessions WHERE status=? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM physical_sessions ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def _transition(self, sid: str, target: str, detail: str = "") -> dict[str, Any]:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT status FROM physical_sessions WHERE id=?", (sid,)
            ).fetchone()
            if not row:
                raise SessionError(f"unknown session {sid}")
            current = row["status"]
            if target not in TRANSITIONS.get(current, set()):
                raise SessionError(
                    f"illegal transition {current} -> {target}; "
                    f"allowed: {sorted(TRANSITIONS.get(current, set())) or 'none'}"
                )
            now = _now()
            col = {
                "running": "started_at",
                "paused": "paused_at",
                "stopped": "stopped_at",
                "reported": "reported_at",
            }.get(target)
            sets = ["status=?", "updated_at=?"]
            params: list[Any] = [target, now]
            if col:
                sets.append(f"{col}=?")
                params.append(now)
            params.append(sid)
            conn.execute(
                f"UPDATE physical_sessions SET {','.join(sets)} WHERE id=?", params
            )
            conn.execute(
                "INSERT INTO session_events(session_id,event,detail,recorded_at)"
                " VALUES(?,?,?,?)",
                (sid, target, detail, now),
            )
        return self.get_session(sid)

    def start(self, sid: str) -> dict[str, Any]:
        return self._transition(sid, "running", "session started")

    def pause(self, sid: str) -> dict[str, Any]:
        return self._transition(sid, "paused", "session paused")

    def resume(self, sid: str) -> dict[str, Any]:
        return self._transition(sid, "running", "session resumed")

    def stop(self, sid: str) -> dict[str, Any]:
        return self._transition(sid, "stopped", "session stopped")

    def report(self, sid: str) -> dict[str, Any]:
        return self._transition(sid, "reported", "session reported")

    # -- conditions / feedback ---------------------------------------------
    def set_condition(self, sid: str, key: str, value: str, source: str = "human") -> None:
        if not self.get_session(sid):
            raise SessionError(f"unknown session {sid}")
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO test_conditions(session_id,key,value,source,recorded_at)"
                " VALUES(?,?,?,?,?)",
                (sid, key, value, source, _now()),
            )

    def add_feedback(self, sid: str, body: str, role: str = "operator",
                     category: str = "observation") -> int:
        if not self.get_session(sid):
            raise SessionError(f"unknown session {sid}")
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO human_feedback(session_id,role,category,body,recorded_at)"
                " VALUES(?,?,?,?,?)",
                (sid, role, category, body, _now()),
            )
            return int(cur.lastrowid or 0)

    def conditions(self, sid: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT key,value,source,recorded_at FROM test_conditions"
            " WHERE session_id=? ORDER BY recorded_at", (sid,)
        ).fetchall()
        return [dict(r) for r in rows]

    def feedback(self, sid: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT role,category,body,recorded_at FROM human_feedback"
            " WHERE session_id=? ORDER BY recorded_at", (sid,)
        ).fetchall()
        return [dict(r) for r in rows]

    def events(self, sid: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT event,detail,recorded_at FROM session_events"
            " WHERE session_id=? ORDER BY recorded_at", (sid,)
        ).fetchall()
        return [dict(r) for r in rows]

    def duration_s(self, sid: str) -> float | None:
        s = self.get_session(sid)
        if not s or s.get("started_at") is None:
            return None
        end = s.get("stopped_at") or s.get("reported_at") or _now()
        return round(end - s["started_at"], 1)


def session_summary(db: PhysicalTestDB, sid: str) -> dict[str, Any]:
    """Public summary incl. duration, conditions, feedback, events."""
    s = db.get_session(sid)
    if not s:
        raise SessionError(f"unknown session {sid}")
    return {
        "session": s,
        "duration_s": db.duration_s(sid),
        "conditions": db.conditions(sid),
        "feedback": db.feedback(sid),
        "events": db.events(sid),
    }
