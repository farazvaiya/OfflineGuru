from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from guru.server.app import create_app


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c, app


def _session(c: TestClient, title="Edge"):
    res = c.post("/api/teacher/session", json={"title": title})
    assert res.status_code == 200, res.text
    return res.json()


def _join(c: TestClient, code: str, name: str) -> dict:
    res = c.post("/api/student/join", json={"code": code, "name": name})
    assert res.status_code == 200, res.text
    return res.json()


def auth(token: str) -> dict:
    return {"X-Teacher-Token": token}


QUIZ = {
    "quiz_id": "edge1",
    "questions": [
        {
            "id": "q1",
            "type": "mcq",
            "topic": "t",
            "question_en": "Q?",
            "question_bn": "প্রশ্ন?",
            "options": [{"id": "A", "en": "a", "bn": "ক"}, {"id": "B", "en": "b", "bn": "খ"}],
            "correct_option_id": "A",
            "answer_text_en": "a",
            "answer_text_bn": "ক",
            "rubric_terms": [],
            "explanation_en": "e",
            "explanation_bn": "ব্যাখ্যা",
            "evidence": {"segment_id": "s1", "seq": 1, "start_s": 0.0, "quote_en": "q"},
            "source_segment_id": "s1",
        }
    ],
    "insufficient_evidence": False,
    "generated_by": "test",
    "source_segment_ids": ["s1"],
}


def test_second_session_conflict_while_running(client):
    c, app = client
    _first = _session(c)
    # Simulate a running lecture.
    app.state.ctx.pipeline.status = "running"
    conflict = c.post("/api/teacher/session", json={"title": "Second"})
    assert conflict.status_code == 409
    app.state.ctx.pipeline.status = "idle"


def test_start_without_a_json_body_still_starts(client):
    """A cached dashboard may POST /start with no body; that must not be a 422."""
    c, app = client
    data = _session(c)
    sid, token = data["session"]["id"], data["teacher_token"]
    res = c.post(f"/api/teacher/session/{sid}/start", headers=auth(token))
    # Defaults to a mic start, which the fixture cannot run because ASR is missing.
    assert res.status_code != 422
    assert "ASR unavailable" in res.json()["detail"]
    assert app.state.ctx.pipeline.status == "idle"


def test_start_requires_existing_wav(client):
    c, app = client
    data = _session(c)
    sid, token = data["session"]["id"], data["teacher_token"]
    res = c.post(f"/api/teacher/session/{sid}/start", headers=auth(token),
                 json={"capture": "wav", "wav_path": "/nonexistent/file.wav"})
    assert res.status_code == 400
    assert app.state.ctx.pipeline.status == "idle"  # pipeline was not left running


def test_start_with_mic_when_asr_missing_returns_503(client):
    c, app = client
    data = _session(c)
    sid, token = data["session"]["id"], data["teacher_token"]
    # The settings fixture points ASR at a missing model directory.
    res = c.post(f"/api/teacher/session/{sid}/start", headers=auth(token),
                 json={"capture": "mic"})
    assert res.status_code == 503
    assert "ASR unavailable" in res.json()["detail"]
    assert app.state.ctx.pipeline.status == "idle"


def test_pause_resume_stop_lifecycle(client):
    c, _app = client
    data = _session(c)
    sid, token = data["session"]["id"], data["teacher_token"]
    started = c.post(f"/api/teacher/session/{sid}/start", headers=auth(token),
                     json={"capture": "none"})
    assert started.status_code == 200
    assert c.post(f"/api/teacher/session/{sid}/start", headers=auth(token),
                  json={"capture": "none"}).status_code == 409
    paused = c.post(f"/api/teacher/session/{sid}/pause", headers=auth(token))
    assert paused.json()["status"] == "paused"
    resumed = c.post(f"/api/teacher/session/{sid}/resume", headers=auth(token))
    assert resumed.json()["status"] == "running"
    stopped = c.post(f"/api/teacher/session/{sid}/stop", headers=auth(token))
    assert stopped.json()["pipeline"]["status"] == "idle"
    # Ending a session requires the pipeline to be idle first.
    assert c.post(f"/api/teacher/session/{sid}/end", headers=auth(token)).status_code == 200


def test_ended_session_code_is_rejected(client):
    c, _app = client
    data = _session(c)
    sid, token = data["session"]["id"], data["teacher_token"]
    c.post(f"/api/teacher/session/{sid}/end", headers=auth(token))
    assert c.post("/api/student/join", json={"code": data["join_code"], "name": "X"}).status_code == 404


def test_answer_submission_rate_limited(client):
    c, app = client
    data = _session(c)
    app.state.ctx.storage.upsert_material(data["session"]["id"], "quiz", QUIZ, status="approved")
    s = _join(c, data["join_code"], "Spammer")
    token = s["student"]["token"]
    codes = [
        c.post(f"/api/student/answer?token={token}",
               json={"question_id": "q1", "answer": "A"}).status_code
        for _ in range(31)
    ]
    assert codes.count(200) == 30
    assert codes.count(429) == 1


def test_answer_unknown_question_404(client):
    c, app = client
    data = _session(c)
    app.state.ctx.storage.upsert_material(data["session"]["id"], "quiz", QUIZ, status="approved")
    s = _join(c, data["join_code"], "A")
    res = c.post(f"/api/student/answer?token={s['student']['token']}",
                 json={"question_id": "ghost", "answer": "A"})
    assert res.status_code == 404


def test_material_update_requires_valid_kind(client):
    c, _app = client
    data = _session(c)
    sid, token = data["session"]["id"], data["teacher_token"]
    res = c.patch(f"/api/teacher/session/{sid}/materials/notes", headers=auth(token),
                  json={"status": "approved"})
    assert res.status_code == 400


def test_teacher_segment_edit_is_session_scoped(client):
    """A teacher from another session must not edit or even resolve a segment."""
    c, app = client
    data_a = _session(c, "A")
    data_b = _session(c, "B")
    sid_a = data_a["session"]["id"]
    seg = app.state.ctx.storage.add_segment(sid_a, start_s=0, end_s=1, en="hi", bn="হাই")
    res = c.patch(f"/api/teacher/segment/{seg['id']}", headers=auth(data_b["teacher_token"]),
                  json={"en": "hacked"})
    assert res.status_code == 403
    assert app.state.ctx.storage.get_segment(seg["id"])["en"] == "hi"


def test_student_join_normalizes_long_names(client):
    c, _app = client
    data = _session(c)
    # Messy whitespace is collapsed; names over the schema limit are rejected
    # outright with 422 before reaching the handler.
    joined = _join(c, data["join_code"], "  A  B   C  ")
    assert joined["student"]["name"] == "A B C"
    too_long = c.post("/api/student/join",
                      json={"code": data["join_code"], "name": "A" * 100})
    assert too_long.status_code == 422


def test_export_rejects_wrong_teacher(client):
    c, _app = client
    data_a = _session(c, "A")
    data_b = _session(c, "B")
    sid_a = data_a["session"]["id"]
    res = c.get(f"/api/teacher/session/{sid_a}/export", headers=auth(data_b["teacher_token"]))
    assert res.status_code == 403


def test_purge_requires_confirmation_and_right_teacher(client):
    c, _app = client
    data_a = _session(c, "A")
    data_b = _session(c, "B")
    sid_a = data_a["session"]["id"]
    assert c.delete(f"/api/teacher/session/{sid_a}",
                    headers=auth(data_b["teacher_token"]),
                    params={"confirm": "true"}).status_code == 403
    assert c.delete(f"/api/teacher/session/{sid_a}",
                    headers=auth(data_a["teacher_token"])).status_code == 400


def test_purge_deletes_the_sessions_tts_cache(client):
    c, app = client
    data = _session(c, "Cache")
    ctx = app.state.ctx
    sid = data["session"]["id"]
    ctx.storage.add_segment(sid, start_s=0, end_s=1, en="hello", bn="হ্যালো")
    ctx.tts.cache_dir.mkdir(parents=True, exist_ok=True)
    mine = ctx.tts.cache_path("হ্যালো")
    other = ctx.tts.cache_path("অন্য ক্লাসের বাক্য")
    mine.write_bytes(b"RIFF")
    other.write_bytes(b"RIFF")

    res = c.delete(f"/api/teacher/session/{sid}", headers=auth(data["teacher_token"]),
                   params={"confirm": "true"})
    assert res.status_code == 200
    assert res.json()["deleted"]["tts_cache_files"] == 1
    assert not mine.exists()
    assert other.exists()  # another class's cached audio is untouched


def test_startup_retention_purges_expired_sessions_and_their_audio(settings):
    """Retention is enforced at startup, not only by the manual delete endpoint."""
    settings.storage.retention_days = 90
    first = create_app(settings)
    with TestClient(first) as c:
        data = _session(c, "Old class")
        ctx = first.state.ctx
        sid = data["session"]["id"]
        ctx.storage.add_segment(sid, start_s=0, end_s=1, en="hello", bn="হ্যালো")
        ctx.tts.cache_dir.mkdir(parents=True, exist_ok=True)
        cached = ctx.tts.cache_path("হ্যালো")
        cached.write_bytes(b"RIFF")
        c.post(f"/api/teacher/session/{sid}/end", headers=auth(data["teacher_token"]))
        ctx.storage._conn.execute("UPDATE sessions SET created_at=? WHERE id=?",
                                  (time.time() - 200 * 86400, sid))
        ctx.storage._conn.commit()

    second = create_app(settings)
    with TestClient(second):
        store = second.state.ctx.storage
        assert store.get_session(sid) is None  # expired session removed at startup
        assert not cached.exists()             # and its cached voice audio with it
