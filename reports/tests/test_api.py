from __future__ import annotations

import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from guru.server.app import create_app

QUIZ = {
    "quiz_id": "quiz1",
    "questions": [
        {
            "id": "q1",
            "type": "mcq",
            "topic": "photosynthesis",
            "question_en": "Which term completes the statement?",
            "question_bn": "কোন শব্দটি খালি জায়গায় বসবে?",
            "options": [
                {"id": "A", "en": "chlorophyll", "bn": "ক্লোরোফিল"},
                {"id": "B", "en": "photosynthesis", "bn": "সালোকসংশ্লেষণ"},
                {"id": "C", "en": "respiration", "bn": "শ্বসন"},
            ],
            "correct_option_id": "B",
            "answer_text_en": "photosynthesis",
            "answer_text_bn": "সালোকসংশ্লেষণ",
            "rubric_terms": [],
            "explanation_en": "The lecture says photosynthesis.",
            "explanation_bn": "শিক্ষকের বক্তব্য: সালোকসংশ্লেষণ।",
            "evidence": {"segment_id": "s1", "seq": 1, "start_s": 3.0, "quote_en": "Photosynthesis is ..."},
            "source_segment_id": "s1",
        },
        {
            "id": "q2",
            "type": "short",
            "topic": "photosynthesis",
            "question_en": "What does photosynthesis mean?",
            "question_bn": "সালোকসংশ্লেষণ বলতে কী বোঝায়?",
            "options": [],
            "correct_option_id": None,
            "answer_text_en": "the process by which green plants make food using sunlight",
            "answer_text_bn": "যে প্রক্রিয়ায় সবুজ 식물 সূর্যের আলোয় খাদ্য তৈরি করে",
            "rubric_terms": ["process", "green", "plants", "sunlight"],
            "explanation_en": "...",
            "explanation_bn": "...",
            "evidence": {"segment_id": "s1", "seq": 1, "start_s": 3.0, "quote_en": "..."},
            "source_segment_id": "s1",
        },
    ],
    "insufficient_evidence": False,
    "generated_by": "test",
    "source_segment_ids": ["s1"],
}


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c, app


def create_session(c: TestClient, title="Biology") -> tuple[dict, str, str]:
    res = c.post("/api/teacher/session", json={"title": title})
    assert res.status_code == 200, res.text
    data = res.json()
    return data, data["teacher_token"], data["session"]["id"]


def join(c: TestClient, code: str, name: str) -> dict:
    res = c.post("/api/student/join", json={"code": code, "name": name})
    assert res.status_code == 200, res.text
    return res.json()


def auth(token: str) -> dict:
    return {"X-Teacher-Token": token}


def test_teacher_endpoints_require_token(client):
    c, _app = client
    data, token, sid = create_session(c)
    assert c.get(f"/api/teacher/session/{sid}").status_code == 401
    assert c.get(f"/api/teacher/session/{sid}", headers=auth("wrong")).status_code == 403
    ok = c.get(f"/api/teacher/session/{sid}", headers=auth(token))
    assert ok.status_code == 200
    assert ok.json()["session"]["code"] == data["join_code"]


def test_student_join_and_state(client):
    c, app = client
    _data, _token, sid = create_session(c)
    storage = app.state.ctx.storage
    storage.add_segment(sid, start_s=0, end_s=3, en="Photosynthesis is the process.", bn="সালোকসংশ্লেষণ।")
    joined = join(c, _data["join_code"], "  Rima  Ahmed ")
    assert joined["student"]["name"] == "Rima Ahmed"
    assert len(joined["segments"]) == 1
    assert joined["segments"][0]["bn"] == "সালোকসংশ্লেষণ।"
    assert joined["quiz"] is None  # nothing approved yet

    state = c.get(f"/api/student/state?token={joined['student']['token']}").json()
    assert state["session"]["id"] == sid
    assert state["quiz"] is None


def test_student_state_can_be_fetched_incrementally(client):
    """``after_seq``/``limit`` let a reconnecting phone skip captions it already has."""
    c, app = client
    data, _token, sid = create_session(c)
    for i in range(3):
        app.state.ctx.storage.add_segment(sid, start_s=i, end_s=i + 1, en=f"s{i}", bn=f"খ{i}")
    joined = join(c, data["join_code"], "Incremental")
    token = joined["student"]["token"]

    full = c.get(f"/api/student/state?token={token}").json()
    assert [s["seq"] for s in full["segments"]] == [1, 2, 3]  # default response is unchanged

    delta = c.get(f"/api/student/state?token={token}&after_seq=2").json()
    assert [s["seq"] for s in delta["segments"]] == [3]
    assert c.get(f"/api/student/state?token={token}&limit=1").json()["segments"][0]["seq"] == 1


def test_student_cap_is_enforced(settings):
    settings.server.max_students = 2
    app = create_app(settings)
    with TestClient(app) as c:
        data, _token, _sid = create_session(c)
        assert c.post("/api/student/join", json={"code": data["join_code"], "name": "A"}).status_code == 200
        assert c.post("/api/student/join", json={"code": data["join_code"], "name": "B"}).status_code == 200
        full = c.post("/api/student/join", json={"code": data["join_code"], "name": "C"})
        assert full.status_code == 429


def test_join_with_bad_code(client):
    c, _ = client
    assert c.post("/api/student/join", json={"code": "ZZZZZZ", "name": "x"}).status_code == 404


def test_quiz_hidden_until_teacher_approves(client):
    c, app = client
    data, token, sid = create_session(c)
    app.state.ctx.storage.upsert_material(sid, "quiz", QUIZ, status="draft")
    joined = join(c, data["join_code"], "Karim")
    assert joined["quiz"] is None

    res = c.patch(f"/api/teacher/session/{sid}/materials/quiz",
                  headers=auth(token), json={"status": "approved"})
    assert res.status_code == 200
    state = c.get(f"/api/student/state?token={joined['student']['token']}").json()
    assert state["quiz"] is not None
    assert len(state["quiz"]["questions"]) == 2
    for q in state["quiz"]["questions"]:
        assert "correct_option_id" not in q


def test_answer_grading_and_visibility(client):
    c, app = client
    data, token, sid = create_session(c)
    app.state.ctx.storage.upsert_material(sid, "quiz", QUIZ, status="approved")
    a = join(c, data["join_code"], "Student A")
    b = join(c, data["join_code"], "Student B")

    res = c.post(f"/api/student/answer?token={a['student']['token']}",
                 json={"question_id": "q1", "answer": "B"})
    assert res.status_code == 200
    assert res.json()["is_correct"] is True
    assert res.json()["answer_text_bn"] == "সালোকসংশ্লেষণ"

    wrong = c.post(f"/api/student/answer?token={b['student']['token']}",
                   json={"question_id": "q1", "answer": "A"})
    assert wrong.status_code == 200
    assert wrong.json()["is_correct"] is False

    # Each student only sees their own result.
    sa = c.get(f"/api/student/state?token={a['student']['token']}").json()
    sb = c.get(f"/api/student/state?token={b['student']['token']}").json()
    assert sa["feedback"]["answers"][0]["is_correct"] == 1
    assert sb["feedback"]["answers"][0]["is_correct"] == 0

    # Teacher sees both, and can correct one.
    teacher = c.get(f"/api/teacher/session/{sid}/answers", headers=auth(token)).json()
    assert len(teacher["answers"]) == 2
    answer_id = next(x["id"] for x in teacher["answers"] if x["student_name"] == "Student B")
    ov = c.post(f"/api/teacher/answer/{answer_id}/override", headers=auth(token),
                json={"is_correct": True})
    assert ov.status_code == 200
    sb2 = c.get(f"/api/student/state?token={b['student']['token']}").json()
    assert sb2["feedback"]["answers"][0]["is_correct"] == 1


def test_short_answer_needs_review_flag(client):
    c, app = client
    data, _token, sid = create_session(c)
    app.state.ctx.storage.upsert_material(sid, "quiz", QUIZ, status="approved")
    s = join(c, data["join_code"], "Nabila")
    res = c.post(f"/api/student/answer?token={s['student']['token']}",
                 json={"question_id": "q2", "answer": "plants sunlight"})
    body = res.json()
    assert body["needs_review"] is True
    assert body["is_correct"] is None


def test_audio_is_scoped_to_the_students_session(client, tmp_path: Path):
    c, app = client
    data, _token, sid = create_session(c)
    wav_path = tmp_path / "seg.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)
    seg = app.state.ctx.storage.add_segment(
        sid, start_s=0, end_s=1, en="hello", bn="হ্যালো", audio_path=str(wav_path)
    )
    a = join(c, data["join_code"], "A")
    ok = c.get(f"/api/student/audio/{seg['id']}?token={a['student']['token']}")
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "audio/wav"

    # A student from another session must not fetch it.
    other, _otok, _osid = create_session(c, "Other class")
    b = join(c, other["join_code"], "B")
    denied = c.get(f"/api/student/audio/{seg['id']}?token={b['student']['token']}")
    assert denied.status_code == 404
    assert c.get(f"/api/student/audio/{seg['id']}?token=nope").status_code == 401


def test_answer_requires_open_quiz(client):
    c, app = client
    data, _token, sid = create_session(c)
    app.state.ctx.storage.upsert_material(sid, "quiz", QUIZ, status="draft")
    s = join(c, data["join_code"], "A")
    res = c.post(f"/api/student/answer?token={s['student']['token']}",
                 json={"question_id": "q1", "answer": "B"})
    assert res.status_code == 403


def test_qr_endpoint_only_accepts_http_urls(client):
    c, _ = client
    assert c.get("/qr.svg", params={"data": "http://10.42.0.1:8000/"}).status_code == 200
    assert c.get("/qr.svg", params={"data": "javascript:alert(1)"}).status_code == 400


def test_health_endpoint_shape(client):
    c, _ = client
    # Health now requires the teacher token (LAN clients are not loopback).
    _data, token, _sid = create_session(c)
    res = c.get("/api/health", headers=auth(token))
    assert res.status_code == 200
    body = res.json()
    for key in ("session", "models", "resources", "network", "metrics", "warnings"):
        assert key in body


def test_export_and_purge(client):
    c, app = client
    _data, token, sid = create_session(c)
    app.state.ctx.storage.add_segment(sid, start_s=0, end_s=1, en="hello", bn="হ্যালো")
    exp = c.get(f"/api/teacher/session/{sid}/export", headers=auth(token))
    assert exp.status_code == 200
    assert exp.json()["segments"][0]["bn"] == "হ্যালো"
    assert c.delete(f"/api/teacher/session/{sid}", headers=auth(token)).status_code == 400
    assert c.delete(f"/api/teacher/session/{sid}?confirm=true", headers=auth(token)).status_code == 200
    assert c.get(f"/api/teacher/session/{sid}", headers=auth(token)).status_code == 404


def test_student_edit_pushes_update(client):
    c, app = client
    data, token, sid = create_session(c)
    seg = app.state.ctx.storage.add_segment(sid, start_s=0, end_s=1, en="old text", bn="পুরনো")
    res = c.patch(f"/api/teacher/segment/{seg['id']}", headers=auth(token),
                  json={"en": "new text", "bn": "নতুন"})
    assert res.status_code == 200
    assert res.json()["segment"]["bn"] == "নতুন"
    joined = join(c, data["join_code"], "A")
    assert joined["segments"][0]["bn"] == "নতুন"
