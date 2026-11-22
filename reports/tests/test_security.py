"""Regression tests for the two hardening fixes (PROJECT_AUDIT.md open findings).

1. ``/api/health`` must not answer unauthenticated LAN clients.
2. Bearer tokens must not be stored in plaintext, and existing sessions must keep
   working after the migration.

Fake tokens only.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from guru.server.app import create_app
from guru.storage import Storage, hash_token

LEGACY_TEACHER = "LEGACY_TEACHER_TOKEN_plaintext_123"
LEGACY_STUDENT = "LEGACY_STUDENT_TOKEN_plaintext_456"

LEGACY_SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
    teacher_token TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
    source_lang TEXT NOT NULL DEFAULT 'en', target_lang TEXT NOT NULL DEFAULT 'bn',
    created_at REAL NOT NULL, ended_at REAL
);
CREATE TABLE students (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, name TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL, joined_at REAL NOT NULL, last_seen REAL NOT NULL
);
"""


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c, app


def _session(c: TestClient) -> tuple[str, str]:
    body = c.post("/api/teacher/session", json={"title": "Security"}).json()
    return body["teacher_token"], body["session"]["id"]


# ---------------------------------------------------------------- /api/health
def test_health_requires_a_teacher_token_from_the_lan(client):
    c, _app = client
    assert c.get("/api/health").status_code == 401


def test_health_accepts_the_session_teacher_token(client):
    c, _app = client
    token, _sid = _session(c)
    res = c.get("/api/health", headers={"X-Teacher-Token": token})
    assert res.status_code == 200
    assert "resources" in res.json()


def test_health_rejects_a_wrong_token(client):
    c, _app = client
    _token, _sid = _session(c)
    assert c.get("/api/health", headers={"X-Teacher-Token": "not-the-token"}).status_code == 403


def test_health_token_can_travel_in_the_query_for_curl(client):
    c, _app = client
    token, _sid = _session(c)
    assert c.get(f"/api/health?token={token}").status_code == 200


def test_liveness_endpoint_stays_open(client):
    """/healthz is the unauthenticated load-balancer/hotspot probe and stays minimal."""
    c, _app = client
    body = c.get("/healthz").json()
    assert body["ok"] is True
    assert "network" not in body and "resources" not in body


# ------------------------------------------------------------- tokens at rest
def test_tokens_are_not_stored_in_plaintext(client):
    c, app = client
    token, sid = _session(c)
    joined = c.post("/api/student/join", json={"code": c.get(f"/api/teacher/session/{sid}",
                   headers={"X-Teacher-Token": token}).json()["session"]["code"],
                   "name": "Hash Test"}).json()
    student_token = joined["student"]["token"]

    conn = app.state.ctx.storage._conn
    for table, column, secret in (
        ("sessions", "teacher_token", token),
        ("sessions", "teacher_token_hash", token),
        ("students", "token", student_token),
        ("students", "token_hash", student_token),
    ):
        values = [r[column] for r in conn.execute(f"SELECT {column} FROM {table}")]
        assert all(secret != (v or "") for v in values), f"plaintext in {table}.{column}"

    stored = conn.execute("SELECT teacher_token_hash FROM sessions WHERE id=?", (sid,)).fetchone()
    assert stored["teacher_token_hash"] == hash_token(token)
    assert len(stored["teacher_token_hash"]) == 64  # sha256 hex


def test_authentication_still_works_against_the_stored_hash(client):
    c, app = client
    token, sid = _session(c)
    storage = app.state.ctx.storage
    assert storage.teacher_token_matches(sid, token) is True
    assert storage.teacher_token_matches(sid, token + "x") is False
    assert storage.teacher_token_matches(sid, "") is False

    joined = c.post("/api/student/join", json={"code": c.get(f"/api/teacher/session/{sid}",
                   headers={"X-Teacher-Token": token}).json()["session"]["code"],
                   "name": "A"}).json()
    student_token = joined["student"]["token"]
    assert storage.student_by_token(student_token)["id"] == joined["student"]["id"]
    assert storage.student_by_token(student_token + "x") is None
    # Lookup results never carry the credential back out.
    assert "token" not in storage.student_by_token(student_token)
    assert "token_hash" not in storage.student_by_token(student_token)


def test_session_payloads_never_include_credentials(client):
    c, _app = client
    token, sid = _session(c)
    body = {"code": c.get(f"/api/teacher/session/{sid}",
                          headers={"X-Teacher-Token": token}).json()["session"]["code"],
            "name": "Payload"}
    c.post("/api/student/join", json=body)
    for path, kwargs in (
        (f"/api/teacher/session/{sid}", {"headers": {"X-Teacher-Token": token}}),
        (f"/api/teacher/session/{sid}/export", {"headers": {"X-Teacher-Token": token}}),
    ):
        payload = c.get(path, **kwargs).text
        assert token not in payload
        assert "teacher_token" not in payload
        assert "token_hash" not in payload


def test_websocket_teacher_still_rejects_a_bad_token(client):
    c, _app = client
    _token, sid = _session(c)
    with pytest.raises(WebSocketDisconnect) as excinfo, \
            c.websocket_connect(f"/ws/teacher?session_id={sid}&token=wrong") as ws:
        ws.receive_json()
    assert excinfo.value.code == 4401


# ------------------------------------------------------------------ migration
def test_legacy_plaintext_database_is_migrated_without_logging_anyone_out(tmp_path: Path):
    """A pre-hash database keeps working: the same token still authenticates."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(LEGACY_SCHEMA)
    now = time.time()
    conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                 ("s1", "OLD001", "Old class", LEGACY_TEACHER, "active", "en", "bn", now, None))
    conn.execute("INSERT INTO students VALUES (?,?,?,?,?,?)",
                 ("st1", "s1", "Rima", LEGACY_STUDENT, now, now))
    conn.commit()
    conn.close()

    storage = Storage(db)
    try:
        # Existing sessions survive: the token the browser already holds still works.
        assert storage.teacher_token_matches("s1", LEGACY_TEACHER) is True
        assert storage.teacher_token_matches("s1", "anything-else") is False
        assert storage.student_by_token(LEGACY_STUDENT)["id"] == "st1"

        # ... and no plaintext is left on disk.
        raw = sqlite3.connect(db)
        session_row = raw.execute(
            "SELECT teacher_token, teacher_token_hash FROM sessions WHERE id='s1'").fetchone()
        student_row = raw.execute(
            "SELECT token, token_hash FROM students WHERE id='st1'").fetchone()
        raw.close()
        assert session_row[0] not in (LEGACY_TEACHER, "", None)
        assert session_row[1] == hash_token(LEGACY_TEACHER)
        assert student_row[1] == hash_token(LEGACY_STUDENT)
        assert student_row[0] != LEGACY_STUDENT
    finally:
        storage.close()


def test_migration_is_idempotent(tmp_path: Path):
    db = tmp_path / "legacy2.db"
    conn = sqlite3.connect(db)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                 ("s2", "OLD002", "t", LEGACY_TEACHER, "active", "en", "bn", time.time(), None))
    conn.commit()
    conn.close()

    for _ in range(2):
        storage = Storage(db)
        assert storage.teacher_token_matches("s2", LEGACY_TEACHER) is True
        storage.close()


def test_new_sessions_hash_more_than_once_safely(tmp_path: Path):
    """Two students in one session must both be storable and distinct."""
    storage = Storage(tmp_path / "fresh.db")
    try:
        sid = storage.create_session("t", "teacher-token-abc", "CODE99")
        first = storage.add_student(sid, "A")
        second = storage.add_student(sid, "B")
        assert first["token"] != second["token"]
        assert storage.student_by_token(first["token"])["name"] == "A"
        assert storage.student_by_token(second["token"])["name"] == "B"
        assert storage.teacher_token_matches(sid, "teacher-token-abc")
    finally:
        storage.close()
