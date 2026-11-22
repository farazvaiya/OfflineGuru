"""SQLite persistence with explicit, versioned migrations.

The database is the source of truth for problems, tasks, experiments, benchmarks,
evaluations, decisions, and integration history. All writes use transactions and
the schema is evolved only through numbered migrations.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

MIGRATIONS: dict[int, str] = {
    1: r"""
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS problems (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        severity TEXT NOT NULL DEFAULT 'P2',
        status TEXT NOT NULL DEFAULT 'discovered',
        root_cause TEXT,
        evidence_json TEXT NOT NULL DEFAULT '[]',
        baseline_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        problem_id TEXT,
        title TEXT NOT NULL,
        owner TEXT NOT NULL,
        phase TEXT NOT NULL DEFAULT 'IDLE',
        status TEXT NOT NULL DEFAULT 'open',
        payload_json TEXT NOT NULL DEFAULT '{}',
        result_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        completed_at REAL,
        FOREIGN KEY (problem_id) REFERENCES problems(id)
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, owner);

    CREATE TABLE IF NOT EXISTS experiments (
        id TEXT PRIMARY KEY,
        problem_id TEXT,
        track TEXT NOT NULL, -- A, B, HYBRID
        status TEXT NOT NULL DEFAULT 'created',
        branch TEXT,
        worktree TEXT,
        hypothesis TEXT NOT NULL DEFAULT '',
        implementation_json TEXT NOT NULL DEFAULT '{}',
        test_results_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        FOREIGN KEY (problem_id) REFERENCES problems(id)
    );

    CREATE TABLE IF NOT EXISTS benchmarks (
        id TEXT PRIMARY KEY,
        experiment_id TEXT,
        problem_id TEXT,
        dataset TEXT,
        config_json TEXT NOT NULL DEFAULT '{}',
        results_json TEXT NOT NULL DEFAULT '{}',
        raw_path TEXT,
        created_at REAL NOT NULL,
        FOREIGN KEY (experiment_id) REFERENCES experiments(id),
        FOREIGN KEY (problem_id) REFERENCES problems(id)
    );

    CREATE TABLE IF NOT EXISTS evaluations (
        id TEXT PRIMARY KEY,
        problem_id TEXT,
        evaluator TEXT NOT NULL,
        verdict TEXT NOT NULL DEFAULT 'pending',
        report_path TEXT,
        comparison_json TEXT NOT NULL DEFAULT '{}',
        limitations_json TEXT NOT NULL DEFAULT '[]',
        created_at REAL NOT NULL,
        FOREIGN KEY (problem_id) REFERENCES problems(id)
    );

    CREATE TABLE IF NOT EXISTS decisions (
        id TEXT PRIMARY KEY,
        problem_id TEXT,
        command TEXT,
        selected TEXT, -- A, B, HYBRID, REJECT, MORE_TESTS
        decided_by TEXT NOT NULL DEFAULT 'human',
        note TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        FOREIGN KEY (problem_id) REFERENCES problems(id)
    );

    CREATE TABLE IF NOT EXISTS integration_history (
        id TEXT PRIMARY KEY,
        problem_id TEXT,
        experiment_id TEXT,
        action TEXT NOT NULL,
        status TEXT NOT NULL,
        checkpoint_id TEXT,
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        FOREIGN KEY (problem_id) REFERENCES problems(id),
        FOREIGN KEY (experiment_id) REFERENCES experiments(id)
    );

    CREATE TABLE IF NOT EXISTS agent_runs (
        id TEXT PRIMARY KEY,
        agent TEXT NOT NULL,
        task_id TEXT,
        problem_id TEXT,
        status TEXT NOT NULL,
        started_at REAL NOT NULL,
        finished_at REAL,
        output_json TEXT NOT NULL DEFAULT '{}',
        error TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_agent_runs_agent ON agent_runs(agent, started_at);

    CREATE TABLE IF NOT EXISTS checkpoints (
        id TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'manual',
        path TEXT NOT NULL,
        source_digest TEXT,
        created_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS workflow_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        from_state TEXT,
        to_state TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        actor TEXT NOT NULL DEFAULT 'orchestrator',
        evidence_json TEXT NOT NULL DEFAULT '[]'
    );
    """,
}


def _now() -> float:
    return time.time()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class Database:
    """Thread-safe SQLite wrapper with migrations."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        self.migrate()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.executemany(sql, list(seq))
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def migrate(self) -> None:
        with self.transaction() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            row = conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
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

    # -- problems ------------------------------------------------------------
    def create_problem(self, title: str, description: str = "", severity: str = "P2",
                       evidence: list[Any] | None = None) -> str:
        pid = uuid.uuid4().hex[:12]
        now = _now()
        self.execute(
            "INSERT INTO problems(id,title,description,severity,status,evidence_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (pid, title, description, severity, "discovered", _json(evidence or []), now, now),
        )
        return pid

    def update_problem(self, problem_id: str, **fields: Any) -> bool:
        allowed = {"title", "description", "severity", "status", "root_cause", "evidence_json", "baseline_json"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k.endswith("_json") and not isinstance(v, str):
                v = _json(v)
            sets.append(f"{k}=?")
            vals.append(v)
        if not sets:
            return False
        sets.append("updated_at=?")
        vals.extend([_now(), problem_id])
        cur = self.execute(f"UPDATE problems SET {', '.join(sets)} WHERE id=?", vals)
        return cur.rowcount > 0

    def list_problems(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            return self.query("SELECT * FROM problems WHERE status=? ORDER BY created_at DESC", (status,))
        return self.query("SELECT * FROM problems ORDER BY created_at DESC")

    # -- tasks ---------------------------------------------------------------
    def create_task(self, title: str, owner: str, phase: str = "IDLE",
                    problem_id: str | None = None, payload: dict[str, Any] | None = None) -> str:
        tid = uuid.uuid4().hex[:12]
        now = _now()
        self.execute(
            "INSERT INTO tasks(id,problem_id,title,owner,phase,status,payload_json,result_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (tid, problem_id, title, owner, phase, "open", _json(payload or {}), "{}", now, now),
        )
        return tid

    def update_task(self, task_id: str, **fields: Any) -> bool:
        allowed = {"status", "owner", "phase", "payload_json", "result_json", "completed_at"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k.endswith("_json") and not isinstance(v, str):
                v = _json(v)
            sets.append(f"{k}=?")
            vals.append(v)
        if not sets:
            return False
        sets.append("updated_at=?")
        vals.extend([_now(), task_id])
        cur = self.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", vals)
        return cur.rowcount > 0

    def list_tasks(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            return self.query("SELECT * FROM tasks WHERE status=? ORDER BY created_at", (status,))
        return self.query("SELECT * FROM tasks ORDER BY created_at")

    # -- experiments ---------------------------------------------------------
    def create_experiment(self, problem_id: str | None, track: str, hypothesis: str = "",
                          branch: str | None = None, worktree: str | None = None) -> str:
        eid = uuid.uuid4().hex[:12]
        now = _now()
        self.execute(
            "INSERT INTO experiments(id,problem_id,track,status,branch,worktree,hypothesis,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (eid, problem_id, track, "created", branch, worktree, hypothesis, now, now),
        )
        return eid

    def update_experiment(self, experiment_id: str, **fields: Any) -> bool:
        allowed = {"status", "branch", "worktree", "hypothesis", "implementation_json", "test_results_json"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k.endswith("_json") and not isinstance(v, str):
                v = _json(v)
            sets.append(f"{k}=?")
            vals.append(v)
        if not sets:
            return False
        sets.append("updated_at=?")
        vals.extend([_now(), experiment_id])
        cur = self.execute(f"UPDATE experiments SET {', '.join(sets)} WHERE id=?", vals)
        return cur.rowcount > 0

    # -- generic helpers -----------------------------------------------------
    def record_decision(self, problem_id: str | None, command: str, selected: str | None,
                        decided_by: str = "human", note: str = "") -> str:
        did = uuid.uuid4().hex[:12]
        self.execute(
            "INSERT INTO decisions(id,problem_id,command,selected,decided_by,note,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (did, problem_id, command, selected, decided_by, note, _now()),
        )
        return did

    def record_workflow_event(self, from_state: str | None, to_state: str, reason: str,
                              actor: str = "orchestrator", evidence: list[Any] | None = None) -> None:
        self.execute(
            "INSERT INTO workflow_events(ts,from_state,to_state,reason,actor,evidence_json)"
            " VALUES(?,?,?,?,?,?)",
            (_now(), from_state, to_state, reason, actor, _json(evidence or [])),
        )

    def add_checkpoint(self, label: str, path: str, kind: str = "manual",
                       source_digest: str | None = None) -> str:
        cid = uuid.uuid4().hex[:12]
        self.execute(
            "INSERT INTO checkpoints(id,label,kind,path,source_digest,created_at) VALUES(?,?,?,?,?,?)",
            (cid, label, kind, path, source_digest, _now()),
        )
        return cid

    def latest_checkpoint(self) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM checkpoints ORDER BY created_at DESC LIMIT 1")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
