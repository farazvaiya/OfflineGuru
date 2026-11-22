from __future__ import annotations

import time
from pathlib import Path

import pytest

from guru.storage import Storage


def make_storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "test.db")


def test_session_code_lookup(tmp_path):
    s = make_storage(tmp_path)
    sid = s.create_session("Photosynthesis", "tok", "ABC123")
    assert s.get_session(sid)["title"] == "Photosynthesis"
    assert s.get_session_by_code("abc123")["id"] == sid
    s.end_session(sid)
    assert s.get_session_by_code("ABC123") is None


def test_segments_are_ordered_and_updatable(tmp_path):
    s = make_storage(tmp_path)
    sid = s.create_session("t", "tok", "CODE01")
    a = s.add_segment(sid, start_s=0, end_s=2, en="one", asr_ms=100)
    b = s.add_segment(sid, start_s=2, end_s=4, en="two")
    assert a["seq"] == 1 and b["seq"] == 2
    s.update_segment_text(a["id"], bn="এক")
    s.update_segment_metrics(a["id"], total_ms=1234.5, numbers_preserved=False)
    got = s.get_segment(a["id"])
    assert got["bn"] == "এক"
    assert got["total_ms"] == 1234.5
    assert got["numbers_preserved"] == 0
    assert [x["en"] for x in s.list_segments(sid)] == ["one", "two"]
    assert [x["en"] for x in s.list_segments(sid, after_seq=1)] == ["two"]


def test_materials_upsert_and_status(tmp_path):
    s = make_storage(tmp_path)
    sid = s.create_session("t", "tok", "CODE02")
    mid = s.upsert_material(sid, "quiz", {"quiz_id": "q", "questions": []})
    assert s.get_material(sid, "quiz")["status"] == "draft"
    s.set_material_status(sid, "quiz", "approved")
    assert s.get_material(sid, "quiz")["status"] == "approved"
    mid2 = s.upsert_material(sid, "quiz", {"quiz_id": "q2", "questions": [1]})
    assert mid2 == mid
    assert s.get_material(sid, "quiz")["content"]["quiz_id"] == "q2"


def test_answer_upsert_is_idempotent(tmp_path):
    s = make_storage(tmp_path)
    sid = s.create_session("t", "tok", "CODE03")
    st = s.add_student(sid, "Rima")
    for score in (0.0, 1.0):
        s.save_answer(session_id=sid, quiz_id="q1", student_id=st["id"],
                      question_id="q1", answer="a", is_correct=score == 1, score=score)
    answers = s.list_answers(sid, st["id"])
    assert len(answers) == 1
    assert answers[0]["score"] == 1.0


def test_topic_stats_accumulate(tmp_path):
    s = make_storage(tmp_path)
    sid = s.create_session("t", "tok", "CODE04")
    for ok in (True, False, True):
        s.bump_topic(sid, "s1", "photosynthesis", ok)
    stats = s.topic_stats(sid, "s1")
    assert stats[0]["attempts"] == 3
    assert stats[0]["correct"] == 2


def test_purge_removes_session_audio_directory(tmp_path):
    s = make_storage(tmp_path)
    sid = s.create_session("t", "tok", "CODE07")
    audio_root = tmp_path / "audio"
    session_dir = audio_root / sid
    session_dir.mkdir(parents=True)
    (session_dir / "seg1.wav").write_bytes(b"RIFF")
    (session_dir / "seg2.wav").write_bytes(b"RIFF")
    other = audio_root / "othersession"
    other.mkdir()
    (other / "keep.wav").write_bytes(b"RIFF")

    counts = s.purge_session(sid, audio_root)
    assert counts["audio_files"] == 2
    assert not session_dir.exists()
    assert other.exists()  # other sessions untouched


def test_purge_removes_everything(tmp_path):
    s = make_storage(tmp_path)
    sid = s.create_session("t", "tok", "CODE05")
    s.add_segment(sid, start_s=0, end_s=1, en="hello")
    s.add_student(sid, "Karim")
    s.upsert_material(sid, "summary", {"summary_bn": []})
    counts = s.purge_session(sid)
    assert counts["segments"] == 1
    assert s.get_session(sid) is None
    assert s.list_segments(sid) == []
    assert s.segment_count(sid) == 0


def test_metric_summary_percentiles(tmp_path):
    s = make_storage(tmp_path)
    for v in (100, 200, 300, 400, 1000):
        s.record_metric("e2e_ms", v, "sess")
    summary = s.metric_summary("e2e_ms", "sess")
    assert summary["count"] == 5
    assert summary["p50"] == 300
    assert summary["max"] == 1000


def test_export_includes_transcript_and_materials(tmp_path):
    s = make_storage(tmp_path)
    sid = s.create_session("Photosynthesis", "tok", "CODE06")
    s.add_segment(sid, start_s=0, end_s=1, en="hello", bn="হ্যালো")
    s.upsert_material(sid, "summary", {"title": "x"}, status="approved")
    bundle = s.export_session(sid)
    assert bundle["session"]["id"] == sid
    assert bundle["segments"][0]["bn"] == "হ্যালো"
    assert bundle["summary"]["content"]["title"] == "x"
    assert "exported_at" in bundle


def test_metrics_index_exists(tmp_path):
    s = make_storage(tmp_path)
    names = {r["name"] for r in s._conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_metrics_kind" in names


def test_metric_summary_uses_a_bounded_recent_window(tmp_path):
    s = make_storage(tmp_path)
    now = time.time()
    rows = [("sess", "asr_ms", float(i), "{}", now - i) for i in range(1500)]
    s._conn.executemany(
        "INSERT INTO metrics (session_id, kind, value, meta_json, created_at) VALUES (?,?,?,?,?)",
        rows,
    )
    s._conn.commit()
    summary = s.metric_summary("asr_ms", "sess")
    assert summary["count"] == 1500        # the count still covers the whole history
    assert summary["max"] == 999.0         # statistics come from the newest 1000 samples
    assert summary["mean"] == 499.5


def test_update_segment_writes_many_columns_in_one_call(tmp_path):
    s = make_storage(tmp_path)
    sid = s.create_session("t", "tok", "CODE08")
    seg = s.add_segment(sid, start_s=0, end_s=1, en="hello")
    assert s.update_segment(seg["id"], bn="হ্যালো", total_ms=12.5,
                            translate_ms=3.0, numbers_preserved=False) is True
    got = s.get_segment(seg["id"])
    assert got["bn"] == "হ্যালো" and got["total_ms"] == 12.5
    assert got["translate_ms"] == 3.0 and got["numbers_preserved"] == 0
    assert s.update_segment(seg["id"]) is False  # nothing to write


def test_record_metrics_batches_many_kinds(tmp_path):
    s = make_storage(tmp_path)
    s.record_metrics([("asr_ms", 10.0, None), ("e2e_ms", 20.0, None), ("tts_ms", 30.0, None)], "s1")
    assert s.metric_summary("asr_ms", "s1")["count"] == 1
    assert s.metric_summary("tts_ms", "s1")["max"] == 30.0


def test_record_metrics_accepts_the_pipeline_two_item_form(tmp_path):
    """The pipeline sends (kind, value); that must persist, not raise."""
    s = make_storage(tmp_path)
    s.record_metrics([("asr_ms", 42.0), ("e2e_ms", 1234.5), ("translate_ms", 30.0)], "s2")
    summary = s.metric_summary("e2e_ms", "s2")
    assert summary["count"] == 1
    assert summary["max"] == 1234.5
    assert s.metric_summary("asr_ms", "s2")["count"] == 1


def test_record_metrics_mixes_arities_and_keeps_meta(tmp_path):
    s = make_storage(tmp_path)
    s.record_metrics([("e2e_ms", 10.0), ("e2e_ms", 30.0, {"stage": "translate"})], "s3")
    summary = s.metric_summary("e2e_ms", "s3")
    assert summary["count"] == 2
    assert summary["max"] == 30.0
    metas = [r["meta_json"] for r in s._conn.execute(
        "SELECT meta_json FROM metrics WHERE session_id='s3' ORDER BY value")]
    assert metas == ["{}", '{"stage": "translate"}']


@pytest.mark.parametrize("bad", [
    [("e2e_ms",)],                     # too short
    [("e2e_ms", 1.0, {}, "extra")],    # too long
    [("e2e_ms", "slow")],              # not a number
    [("", 1.0)],                        # empty kind
    [("e2e_ms", 1.0, "not-a-dict")],   # bad meta
    [None],                              # not a tuple at all
])
def test_record_metrics_rejects_malformed_entries(tmp_path, bad):
    """A bad caller must fail loudly instead of losing measurements silently."""
    s = make_storage(tmp_path)
    with pytest.raises(ValueError):
        s.record_metrics(bad, "s4")
    assert s.metric_summary("e2e_ms", "s4")["count"] == 0


def test_record_metrics_writes_nothing_when_one_entry_is_bad(tmp_path):
    s = make_storage(tmp_path)
    with pytest.raises(ValueError, match="e2e_ms"):
        s.record_metrics([("asr_ms", 1.0), ("e2e_ms", "slow")], "s5")
    assert s.metric_summary("asr_ms", "s5")["count"] == 0


def test_purge_older_than_keeps_new_and_active_sessions(tmp_path):
    s = make_storage(tmp_path)
    old = s.create_session("old", "tok", "OLD001")
    active = s.create_session("running", "tok", "ACT001")
    fresh = s.create_session("fresh", "tok", "NEW001")
    s.end_session(old)
    s._conn.execute("UPDATE sessions SET created_at=? WHERE id IN (?,?)",
                    (time.time() - 200 * 86400, old, active))
    s._conn.commit()

    purged = s.purge_older_than(90)
    assert purged == [old]
    assert s.get_session(old) is None
    assert s.get_session(active) is not None  # a session that is still running is kept
    assert s.get_session(fresh) is not None


def test_purge_older_than_runs_the_callback_while_transcript_still_exists(tmp_path):
    """The callback is how per-session TTS cache files get cleaned up."""
    s = make_storage(tmp_path)
    sid = s.create_session("old", "tok", "OLD002")
    s.add_segment(sid, start_s=0, end_s=1, en="hello", bn="হ্যালো")
    s.end_session(sid)
    s._conn.execute("UPDATE sessions SET created_at=?", (time.time() - 200 * 86400,))
    s._conn.commit()

    seen: list[list[str]] = []
    s.purge_older_than(90, before_delete=lambda expired: seen.append(
        [seg["bn"] for seg in s.list_segments(expired)]))
    assert seen == [["হ্যালো"]]
