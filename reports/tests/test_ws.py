from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from guru.server.app import create_app


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c, app


def _session(c: TestClient, title="WS class"):
    res = c.post("/api/teacher/session", json={"title": title})
    assert res.status_code == 200, res.text
    return res.json()


def _join(c: TestClient, code: str, name: str) -> dict:
    res = c.post("/api/student/join", json={"code": code, "name": name})
    assert res.status_code == 200, res.text
    return res.json()


def test_student_ws_requires_valid_token(client):
    c, _app = client
    with pytest.raises(WebSocketDisconnect) as exc, c.websocket_connect("/ws/student?token=bogus"):
        pass
    assert exc.value.code == 4401


def test_teacher_ws_requires_matching_token(client):
    c, _app = client
    data = _session(c)
    sid = data["session"]["id"]
    with pytest.raises(WebSocketDisconnect) as exc, \
            c.websocket_connect(f"/ws/teacher?session_id={sid}&token=wrong"):
        pass
    assert exc.value.code == 4401


def test_student_ws_hello_and_event_delivery(client):
    c, app = client
    data = _session(c)
    sid = data["session"]["id"]
    joined = _join(c, data["join_code"], "Rina")
    token = joined["student"]["token"]

    with c.websocket_connect(f"/ws/student?token={token}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["role"] == "student"
        assert hello["student"]["name"] == "Rina"
        # Publish a segment scoped to this session and expect delivery.
        app.state.ctx.bus.publish(
            {"type": "segment", "session_id": sid, "segment": {"seq": 1}},
            session_id=sid,
        )
        event = ws.receive_json()
        assert event["type"] == "segment"
        # ping/pong liveness
        ws.send_text("ping")
        pong = ws.receive_json()
        assert pong["type"] == "pong"


def test_events_do_not_leak_across_sessions(client):
    c, app = client
    data_a = _session(c, "Class A")
    data_b = _session(c, "Class B")
    sid_a, sid_b = data_a["session"]["id"], data_b["session"]["id"]
    joined_a = _join(c, data_a["join_code"], "Aya")
    token_a = joined_a["student"]["token"]

    with c.websocket_connect(f"/ws/student?token={token_a}") as ws:
        assert ws.receive_json()["type"] == "hello"
        # An event for session B must not reach session A's student.
        app.state.ctx.bus.publish(
            {"type": "segment", "session_id": sid_b, "segment": {"seq": 1, "en": "secret"}},
            session_id=sid_b,
        )
        # An event for session A must arrive.
        app.state.ctx.bus.publish(
            {"type": "segment", "session_id": sid_a, "segment": {"seq": 1, "en": "ok"}},
            session_id=sid_a,
        )
        event = ws.receive_json()
        assert event["segment"]["en"] == "ok"


def test_teacher_ws_hello(client):
    c, _app = client
    data = _session(c)
    sid = data["session"]["id"]
    token = data["teacher_token"]
    with c.websocket_connect(f"/ws/teacher?session_id={sid}&token={token}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["role"] == "teacher"
        assert hello["session"]["code"] == data["join_code"]
