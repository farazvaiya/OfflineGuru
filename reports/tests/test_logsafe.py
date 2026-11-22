"""Tokens must never reach a log sink (FINAL_VERIFICATION_REPORT.md, MEDIUM-2).

Fake tokens only: these strings do not exist in any real session.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from guru.logsafe import (
    PLACEHOLDER,
    RedactingFormatter,
    RedactionFilter,
    install_log_redaction,
    redact,
)
from guru.server.app import create_app

FAKE_TEACHER_TOKEN = "FAKE_TEACHER_TOKEN_0123456789abcdef"
FAKE_STUDENT_TOKEN = "FAKE_STUDENT_TOKEN_fedcba9876543210"


# ---------------------------------------------------------------------------
# unit: the patterns themselves
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("text", "secret"), [
    ("GET /api/student/state?token=abc123SECRET HTTP/1.1", "abc123SECRET"),
    ("GET /api/student/state?after_seq=3&token=abc123SECRET&limit=4", "abc123SECRET"),
    ("GET /api/teacher/session/1?teacher_token=abc123SECRET", "abc123SECRET"),
    ("headers {'X-Teacher-Token': 'abc123SECRET'}", "abc123SECRET"),
    ("Authorization: Bearer abc123SECRET", "abc123SECRET"),
    ("Cookie: guru=abc123SECRET", "abc123SECRET"),
    ("x_teacher_token=abc123SECRET", "abc123SECRET"),
])
def test_redact_removes_token_values(text, secret):
    cleaned = redact(text)
    assert secret not in cleaned
    assert PLACEHOLDER in cleaned


def test_redact_keeps_unrelated_context():
    line = "2026-01-01 INFO guru.pipeline Offline Guru ready on 0.0.0.0:8000"
    assert redact(line) == line
    assert redact("segment 12 translated in 240 ms") == "segment 12 translated in 240 ms"


def test_formatter_redacts_a_bare_token_argument():
    """``log.info("token=%s", tok)`` keeps the secret out of the rendered line."""
    record = logging.LogRecord("guru.test", logging.INFO, __file__, 1,
                               "token=%s", (FAKE_STUDENT_TOKEN,), None)
    formatted = RedactingFormatter(logging.Formatter("%(message)s")).format(record)
    assert FAKE_STUDENT_TOKEN not in formatted
    assert PLACEHOLDER in formatted


def test_filter_preserves_argument_length_and_types():
    """The access formatter reads positional args; redaction must not reshape them."""
    args = ("127.0.0.1:5000", "GET", "/api/student/state?token=abc123SECRET", "1.1", 200)
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                               '%s - "%s %s HTTP/%s" %d', args, None)
    assert RedactionFilter().filter(record) is True
    assert len(record.args) == 5
    assert record.args[0] == "127.0.0.1:5000"
    assert record.args[4] == 200
    assert "abc123SECRET" not in record.args[2]
    assert record.getMessage().endswith("200")


def test_filter_keeps_format_placeholders_renderable():
    record = logging.LogRecord("guru.test", logging.INFO, __file__, 1, "token=%s", ("abc",), None)
    RedactionFilter().filter(record)
    assert record.getMessage() == "token=abc"  # still a valid, format-safe record


def test_install_is_idempotent():
    install_log_redaction()
    install_log_redaction()
    root = logging.getLogger()
    assert len([f for f in root.filters if isinstance(f, RedactionFilter)]) == 1
    for handler in root.handlers:
        assert len([f for f in handler.filters if isinstance(f, RedactionFilter)]) == 1
        assert isinstance(handler.formatter, RedactingFormatter)


# ---------------------------------------------------------------------------
# integration: the app's own log file
# ---------------------------------------------------------------------------
@pytest.fixture
def log_client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c, app


def _log_text(settings) -> str:
    path = Path(settings.storage.data_dir) / "guru.log"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _drive_every_token_path(c: TestClient) -> tuple[str, str, str]:
    """Exercise every place a token travels, including query strings."""
    created = c.post("/api/teacher/session", json={"title": "logs"}).json()
    teacher, sid = created["teacher_token"], created["session"]["id"]
    joined = c.post("/api/student/join",
                    json={"code": created["join_code"], "name": "Log Student"}).json()
    student = joined["student"]["token"]

    assert c.get(f"/api/teacher/session/{sid}",
                 headers={"X-Teacher-Token": teacher}).status_code == 200
    assert c.get(f"/api/student/state?token={student}").status_code == 200
    assert c.get(f"/api/student/state?token={student}&after_seq=0").status_code == 200
    c.post(f"/api/student/answer?token={student}", json={"question_id": "missing", "answer": "x"})
    c.get(f"/api/student/audio/missing-segment?token={student}")
    c.post(f"/api/student/audio-preference?enabled=false&token={student}")
    with c.websocket_connect(f"/ws/student?token={student}") as ws:
        assert ws.receive_json()["type"] == "hello"
    return teacher, student, sid


def test_tokens_never_reach_the_application_log(log_client, settings):
    c, _app = log_client
    teacher, student, _sid = _drive_every_token_path(c)
    # A header dump and an access-log line, the two shapes that leak in practice.
    logging.getLogger("guru.test.leak").info("bearer header X-Teacher-Token: %s", teacher)
    logging.getLogger("httpx").info(
        'HTTP Request: GET http://10.42.0.1:8000/api/student/state?token=%s "HTTP/1.1 200 OK"',
        student,
    )
    logging.getLogger("uvicorn.access").info(
        '%s - "WebSocket /ws/student?token=%s" [accepted]', "10.42.0.1:5000", student)

    text = _log_text(settings)
    assert text, "the app should still be logging to data/guru.log"
    assert teacher not in text
    assert student not in text
    # ... and the redaction is actually doing the work, not logging being off.
    assert PLACEHOLDER in text


def test_fake_tokens_are_redacted_from_the_log_file(log_client, settings):
    c, _app = log_client
    c.post("/api/teacher/session", json={"title": "fake"})
    logging.getLogger("guru.test.fake").info(
        "teacher token=%s student token=%s", FAKE_TEACHER_TOKEN, FAKE_STUDENT_TOKEN)
    text = _log_text(settings)
    assert FAKE_TEACHER_TOKEN not in text
    assert FAKE_STUDENT_TOKEN not in text


def test_exceptions_do_not_carry_tokens_into_logs(log_client, settings):
    c, _app = log_client
    _teacher, student, _sid = _drive_every_token_path(c)
    logging.getLogger("guru.test.error").error(
        "handling failed for token=%s", student, exc_info=RuntimeError("no token here"))
    text = _log_text(settings)
    assert student not in text


def test_authentication_still_works_after_redaction(log_client):
    c, _app = log_client
    created = c.post("/api/teacher/session", json={"title": "auth"}).json()
    teacher, sid = created["teacher_token"], created["session"]["id"]
    joined = c.post("/api/student/join",
                    json={"code": created["join_code"], "name": "A"}).json()
    student = joined["student"]["token"]

    assert c.get(f"/api/teacher/session/{sid}",
                 headers={"X-Teacher-Token": teacher}).status_code == 200
    assert c.get(f"/api/teacher/session/{sid}",
                 headers={"X-Teacher-Token": "wrong"}).status_code == 403
    assert c.get(f"/api/teacher/session/{sid}").status_code == 401
    assert c.get(f"/api/student/state?token={student}").status_code == 200
    assert c.get("/api/student/state?token=not-a-token").status_code == 401
    assert c.get("/api/student/state").status_code == 422


def test_websocket_authentication_still_rejects_bad_tokens(log_client):
    c, _app = log_client
    with pytest.raises(WebSocketDisconnect) as excinfo, \
            c.websocket_connect("/ws/student?token=not-a-token") as ws:
        ws.receive_json()
    assert excinfo.value.code == 4401


def test_student_sessions_remains_isolated(log_client):
    c, app = log_client
    created = c.post("/api/teacher/session", json={"title": "iso"}).json()
    sid = created["session"]["id"]
    a = c.post("/api/student/join",
               json={"code": created["join_code"], "name": "A"}).json()["student"]
    b = c.post("/api/student/join",
               json={"code": created["join_code"], "name": "B"}).json()["student"]
    assert a["token"] != b["token"]

    state_a = c.get(f"/api/student/state?token={a['token']}").json()
    state_b = c.get(f"/api/student/state?token={b['token']}").json()
    assert state_a["student"]["id"] == a["id"]
    assert state_b["student"]["id"] == b["id"]
    assert state_a["student"]["id"] != state_b["student"]["id"]
    # A token only ever resolves to its own session.
    assert state_a["session"]["id"] == sid == state_b["session"]["id"]
    assert app.state.ctx.storage.student_by_token(a["token"])["id"] == a["id"]
