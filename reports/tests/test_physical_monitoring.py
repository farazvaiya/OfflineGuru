"""Automated tests for the Physical Test Monitoring System (Phases 1-9).

Every phase of engineering_team/physical/ has coverage here, per the master
prompt's testing requirement. All tests are synthetic: no microphone, no real
arecord, no network beyond loopback stub servers, no fabricated telemetry.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engineering_team.physical import collectors as C  # noqa: E402
from engineering_team.physical.approval import ApprovalGate, ApprovalError  # noqa: E402
from engineering_team.physical.benchmark import BenchmarkEngine, BenchmarkError  # noqa: E402
from engineering_team.physical.dashboard import Dashboard  # noqa: E402
from engineering_team.physical.history import History  # noqa: E402
from engineering_team.physical.isolation import IsolatedWorkspace, IsolationError  # noqa: E402
from engineering_team.physical.sessions import (  # noqa: E402
    PhysicalTestDB, SessionError, session_summary,
)
from engineering_team.physical.telemetry import TelemetryDB  # noqa: E402
from engineering_team.physical.validator import Validator, wer, cer, _chrf  # noqa: E402


# ---------------------------------------------------------------- Phase 1
class TestPhase1Sessions:
    def test_lifecycle_transitions(self, tmp_path):
        db = PhysicalTestDB(tmp_path / "s.db")
        s = db.create_session(expected_students=30)
        sid = s["id"]
        assert s["status"] == "created"
        db.start(sid); assert db.get_session(sid)["status"] == "running"
        db.pause(sid); assert db.get_session(sid)["status"] == "paused"
        db.resume(sid); assert db.get_session(sid)["status"] == "running"
        db.stop(sid); assert db.get_session(sid)["status"] == "stopped"
        db.report(sid); assert db.get_session(sid)["status"] == "reported"
        db.close()

    def test_invalid_transition_rejected(self, tmp_path):
        db = PhysicalTestDB(tmp_path / "s.db")
        s = db.create_session()
        with pytest.raises(SessionError):
            db._transition(s["id"], "stopped")  # created -> stopped illegal
        db.close()

    def test_reported_is_terminal(self, tmp_path):
        db = PhysicalTestDB(tmp_path / "s.db")
        s = db.create_session()
        for fn in (db.start, db.pause, db.resume, db.stop, db.report):
            fn(s["id"])
        with pytest.raises(SessionError):
            db.pause(s["id"])
        db.close()

    def test_persistence_across_restart(self, tmp_path):
        p = tmp_path / "s.db"
        db = PhysicalTestDB(p)
        s = db.create_session(teacher_language="en")
        db.start(s["id"]); db.stop(s["id"])
        db.set_condition(s["id"], "k", "v")
        db.add_feedback(s["id"], "heard clearly")
        db.close()
        db2 = PhysicalTestDB(p)
        got = db2.get_session(s["id"])
        assert got["status"] == "stopped"
        assert got["teacher_language"] == "en"
        assert db2.duration_s(s["id"]) is not None
        assert len(db2.conditions(s["id"])) == 1
        assert len(db2.feedback(s["id"])) == 1
        assert len(db2.events(s["id"])) == 2  # running + stopped transition events
        db2.close()

    def test_unknown_session_raises(self, tmp_path):
        db = PhysicalTestDB(tmp_path / "s.db")
        assert db.get_session("nope") is None
        with pytest.raises(SessionError):
            db.set_condition("nope", "k", "v")
        with pytest.raises(SessionError):
            db.add_feedback("nope", "b")
        with pytest.raises(SessionError):
            db.start("nope")
        db.close()

    def test_summary_and_fields(self, tmp_path):
        db = PhysicalTestDB(tmp_path / "s.db")
        s = db.create_session(teacher_language="en", target_language="bn",
                              expected_students=30, actual_students=28,
                              microphone_info="USB", laptop_info="i5",
                              student_devices="phones", operating_system="Ubuntu",
                              network_config="hotspot", internet_available=False,
                              room_noise="quiet", device_distances="1-3m",
                              metadata={"class": "math"}, notes="test")
        assert s["internet_available"] == 0
        assert json.loads(s["metadata_json"]) == {"class": "math"}
        summ = session_summary(db, s["id"])
        assert summ["session"]["id"] == s["id"]
        db.close()

    def test_list_sessions_filter(self, tmp_path):
        db = PhysicalTestDB(tmp_path / "s.db")
        a = db.create_session(); db.start(a["id"]); db.stop(a["id"])
        b = db.create_session(); db.start(b["id"])
        running = db.list_sessions(status="running")
        assert [x["id"] for x in running] == [b["id"]]
        all_s = db.list_sessions()
        assert len(all_s) == 2
        db.close()


# ---------------------------------------------------------------- Phase 2
class _HealthStub(BaseHTTPRequestHandler):
    payload = {"ok": True, "session": {"status": "running",
               "stats": {"stream_decodes": 5, "segments_empty": 0, "last_error": None}},
               "resources": {"cpu_percent": 42.0},
               "metrics": {"asr_ms": {"p50": 1100}},
               "clients": {"subscribers": [{"role": "student", "delivered": 10,
                                            "dropped": 0, "queued": 1}]},
               "network": {"iface": "wlan0"}, "warnings": []}

    def do_GET(self):  # noqa: N802
        body = json.dumps(self.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def health_url():
    srv = HTTPServer(("127.0.0.1", 0), _HealthStub)
    url = f"http://127.0.0.1:{srv.server_address[1]}/api/health"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield url
    srv.shutdown()


class TestPhase2Collectors:
    def test_all_collectors_ok(self, health_url):
        results = {c.name(): c.collect() for c in [
            C.HealthSnapshotCollector(health_url), C.CaptureCollector(health_url),
            C.ASRCollector(health_url), C.TranslationCollector(health_url),
            C.TTSCollector(health_url), C.StudentPlaybackCollector(health_url),
            C.NetworkCollector(health_url), C.SystemCollector()]}
        assert len(results) == 8
        for name, r in results.items():
            assert r["status"] in ("ok", "unavailable", "error"), (name, r)
            if r["status"] == "ok":
                assert "measured" in r

    def test_missing_metrics_stay_missing(self, health_url):
        r = C.ASRCollector(health_url).collect()
        assert r["measured"]["final_tail_decode_count"] is None
        assert "NOT_EXPOSED" in r["measured"]["final_tail_decode_note"]
        r = C.TTSCollector(health_url).collect()
        assert r["measured"]["tts_cache_hits"] is None

    def test_playback_never_claims_audible(self, health_url):
        r = C.StudentPlaybackCollector(health_url).collect()
        assert r["measured"]["audible_playback"] == "NOT_EXECUTED"

    def test_capture_inference_labelled(self, health_url):
        r = C.CaptureCollector(health_url).collect()
        assert "inferred" in r["measured"]["capture_state"]
        assert "inference" in r["measured"]["capture_state_source"]

    def test_unreachable_server_is_error(self):
        r = C.ASRCollector("http://127.0.0.1:1/x").collect()
        assert r["status"] == "error" and "reason" in r

    def test_capture_error_inferred(self, health_url, monkeypatch):
        _HealthStub.payload = {"ok": True,
                               "session": {"status": "running",
                                           "stats": {"last_error": "capture error: device gone"}},
                               "warnings": []}
        try:
            r = C.CaptureCollector(health_url).collect()
            assert r["measured"]["capture_state"] == "error"
        finally:
            _HealthStub.payload = {"ok": True, "session": {"status": "running", "stats": {}},
                                   "resources": {}, "metrics": {}, "clients": {},
                                   "network": {}, "warnings": []}

    def test_collector_loop_bounded_and_independent(self, health_url):
        q = C.queue.Queue()
        loop = C.CollectorLoop(health_url, out_q=q, interval_s=0.1)
        loop.start()
        time.sleep(0.5)
        loop.stop()
        assert loop.polls >= 2
        types = set()
        while not q.empty():
            types.add(q.get()["collector"])
        assert len(types) >= 7
        assert loop.collector_errors == {}


# ---------------------------------------------------------------- Phase 3
class TestPhase3TelemetryDashboard:
    def test_record_and_read(self, tmp_path):
        db = TelemetryDB(tmp_path / "t.db")
        db.record_collector_result("S1", "asr",
                                   {"status": "ok", "measured": {"stream_decodes": 5},
                                    "source": "GET /api/health"})
        db.record_error("S1", "asr", "boom")
        db.record_student_event("S1", "connected")
        db.record_validation("S1", "WER", "PASS", {"wer": 0.04}, {"max": 0.05}, "ev")
        db.record_agent_finding("S1", "asr-agent", "tail loss", "high", ["a.json"])
        db.record_metric("S1", "asr_ms", 1100.0)
        assert len(db.events(session_id="S1")) == 1
        assert db.errors(session_id="S1")[0]["component"] == "asr"
        assert db.validations(session_id="S1")[0]["outcome"] == "PASS"
        assert len(db.student_events(session_id="S1")) == 1
        db.close()

    def test_outcome_validation(self, tmp_path):
        db = TelemetryDB(tmp_path / "t.db")
        with pytest.raises(ValueError):
            db.record_validation("S1", "x", "MAYBE")
        db.close()

    def test_migration_persists(self, tmp_path):
        p = tmp_path / "t.db"
        db = TelemetryDB(p)
        row = db._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        assert int(row["value"]) == 1
        db.close()
        db2 = TelemetryDB(p)  # reopen: migration must be idempotent
        assert len(db2.events()) == 0
        db2.close()

    def test_dashboard_serves_honest_data(self, tmp_path):
        import urllib.request
        db = TelemetryDB(tmp_path / "t.db")
        db.record_collector_result("S1", "tts",
                                   {"status": "ok", "measured": {"tts_cache_hits": None},
                                    "source": "x"})
        dash = Dashboard(db, port=8793)
        dash.start()
        time.sleep(0.3)
        with urllib.request.urlopen(
                "http://127.0.0.1:8793/api/telemetry", timeout=3) as r:
            payload = json.loads(r.read())
        assert payload["latest"]["tts"].get("tts_cache_hits") is None  # missing != zero
        with urllib.request.urlopen(
                "http://127.0.0.1:8793/", timeout=3) as r:
            html = r.read().decode()
        assert "not audible playback" in html
        dash.stop()
        db.close()

    def test_dashboard_failure_does_not_crash(self, tmp_path):
        db = TelemetryDB(tmp_path / "t.db")
        dash = Dashboard(db, port=8794)
        dash.start(); time.sleep(0.2); dash.stop()  # starts/stops cleanly
        db.close()


# ---------------------------------------------------------------- Phase 4
class TestPhase4Validator:
    def test_metric_correctness(self):
        assert wer("hello world", "hello world") == 0.0
        assert wer("hello world", "hello word") == 0.5
        assert wer("a b c", "") == 1.0
        assert abs(cer("cat", "car") - 1 / 3) < 1e-9
        assert _chrf("hello", "hello") > 0.9
        assert _chrf("abc", "xyz") < 0.5

    def test_outcomes_all_four(self, tmp_path):
        db = TelemetryDB(tmp_path / "v.db")
        v = Validator(db, thresholds={"max_wer": 0.10, "max_drops": 0})
        sid = "S1"
        assert v.check_asr_wer(sid, "a b c", "a b c")["outcome"] == "PASS"
        assert v.check_asr_wer(sid, "a b c", "x y z")["outcome"] == "FAIL"
        assert Validator(db).check_asr_wer(sid, "a b", "a b")["outcome"] == "INCONCLUSIVE"
        assert v.check_asr_wer(sid, "", "x")["outcome"] == "INCONCLUSIVE"
        assert v.check_final_tail(sid)["outcome"] == "NOT_EXECUTED"
        assert v.check_audible_playback(sid)["outcome"] == "NOT_EXECUTED"
        assert v.check_audible_playback(sid, "heard it")["outcome"] == "INCONCLUSIVE"
        db.close()

    def test_queue_drops_needs_telemetry(self, tmp_path):
        db = TelemetryDB(tmp_path / "v.db")
        v = Validator(db)
        sid = "S1"
        assert v.check_asr_queue_drops(sid)["outcome"] == "INCONCLUSIVE"
        db.record_collector_result(sid, "asr",
                                   {"status": "ok", "measured": {"asr_queue_drops": 3}})
        assert v.check_asr_queue_drops(sid)["outcome"] == "FAIL"
        db.record_collector_result(sid, "asr",
                                   {"status": "ok", "measured": {"asr_queue_drops": 0}})
        assert v.check_asr_queue_drops(sid)["outcome"] == "PASS"
        db.close()

    def test_run_all_aggregates(self, tmp_path):
        db = TelemetryDB(tmp_path / "v.db")
        v = Validator(db, thresholds={"max_wer": 0.1, "min_chrf": 0.5})
        out = v.run_all("S1", reference_transcript="a b c", hypothesis_transcript="a b c",
                        reference_translation="x y", hypothesis_translation="x y",
                        expected_duration_s=60.0, human_feedback="ok")
        outcomes = [o["outcome"] for o in out]
        assert "PASS" in outcomes and "NOT_EXECUTED" in outcomes
        assert len(db.validations(session_id="S1")) >= 4
        db.close()


# ---------------------------------------------------------------- Phase 5
class TestPhase5AgentRuntime:
    def test_availability(self, tmp_path):
        from engineering_team.harness.db import Database
        from engineering_team.harness.agent_runtime import AgentExecution
        db = Database(tmp_path / "a.db")
        rt = AgentExecution(db, executable="python3", timeout_s=10,
                            cpu_limit_s=10, mem_limit_mb=512,
                            allowed_roots=[str(tmp_path)])
        av = rt.available()
        assert av["available"] is True
        db.close()

    def test_real_execution(self, tmp_path):
        from engineering_team.harness.db import Database
        from engineering_team.harness.agent_runtime import AgentExecution
        db = Database(tmp_path / "a.db")
        rt = AgentExecution(db, executable="python3", timeout_s=10,
                            allowed_roots=[str(tmp_path)])
        r = rt.run("test-agent", task={"id": "T1"}, command=["-c", "print('hello')"])
        assert r["status"] == "completed" and "hello" in r["stdout_tail"]
        rows = db.query("SELECT * FROM agent_runs")
        assert rows[0]["agent"] == "test-agent"
        db.close()

    def test_nonzero_exit_is_failed(self, tmp_path):
        from engineering_team.harness.db import Database
        from engineering_team.harness.agent_runtime import AgentExecution
        db = Database(tmp_path / "a.db")
        rt = AgentExecution(db, executable="python3", timeout_s=10,
                            allowed_roots=[str(tmp_path)])
        r = rt.run("test-agent", command=["-c", "import sys; sys.exit(3)"])
        assert r["status"] == "failed" and r["exit_code"] == 3
        db.close()

    def test_timeout_kills(self, tmp_path):
        from engineering_team.harness.db import Database
        from engineering_team.harness.agent_runtime import AgentExecution
        db = Database(tmp_path / "a.db")
        rt = AgentExecution(db, executable="python3", timeout_s=1.0,
                            allowed_roots=[str(tmp_path)])
        r = rt.run("slow", command=["-c", "import time; time.sleep(30)"])
        assert r["status"] == "timeout"
        db.close()

    def test_recursive_claude_refused(self, tmp_path, monkeypatch):
        from engineering_team.harness.db import Database
        from engineering_team.harness import agent_runtime as ar
        from engineering_team.harness.agent_runtime import AgentExecution, AgentRuntimeError
        monkeypatch.setattr(ar.shutil, "which", lambda x: "/usr/bin/claude")
        db = Database(tmp_path / "a.db")
        with pytest.raises(AgentRuntimeError, match="recursive"):
            AgentExecution(db, executable="claude")
        db.close()

    def test_path_escape_refused(self, tmp_path):
        from engineering_team.harness.db import Database
        from engineering_team.harness.agent_runtime import AgentExecution
        db = Database(tmp_path / "a.db")
        rt = AgentExecution(db, executable="python3", timeout_s=10,
                            allowed_roots=[str(tmp_path)])
        r = rt.run("x", command=["/etc/passwd"])
        assert r["status"] == "failed" and "escapes" in r["error"]
        db.close()

    def test_no_command_is_honest_pending(self, tmp_path):
        from engineering_team.harness.db import Database
        from engineering_team.harness.agent_runtime import AgentExecution
        db = Database(tmp_path / "a.db")
        rt = AgentExecution(db, executable="python3", timeout_s=10,
                            allowed_roots=[str(tmp_path)])
        r = rt.run("x", command=None)
        assert r["status"] == "pending_external"
        db.close()


# ---------------------------------------------------------------- Phase 6
class TestPhase6Isolation:
    def test_tracks_independent(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "guru").mkdir(parents=True)
        (repo / "guru" / "pipeline.py").write_text("def f(): pass\n")
        ws = IsolatedWorkspace(repo=repo, root=tmp_path / "cands",
                               source_paths=["guru/pipeline.py"])
        a = ws.create("A", "t"); b = ws.create("B", "t")
        pa, pb = Path(a["path"]), Path(b["path"])
        (pa / "guru" / "pipeline.py").write_text("A-change")
        assert (pb / "guru" / "pipeline.py").read_text() == "def f(): pass\n"
        assert ws.digest("A") != ws.digest("B")
        ws.release("A"); ws.release("B")

    def test_mutation_detection(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "guru").mkdir(parents=True)
        (repo / "guru" / "pipeline.py").write_text("x")
        ws = IsolatedWorkspace(repo=repo, root=tmp_path / "cands",
                               source_paths=["guru/pipeline.py"])
        ws.create("A")
        assert ws.verify("A")["unchanged"] is True
        (Path(ws.candidate("A") / "guru" / "pipeline.py")).write_text("y")
        assert ws.verify("A")["unchanged"] is False
        ws.release("A")

    def test_bad_track_rejected(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        ws = IsolatedWorkspace(repo=repo, root=tmp_path / "cands")
        with pytest.raises(IsolationError):
            ws.create("C")


# ---------------------------------------------------------------- Phase 7
class TestPhase7Benchmark:
    def test_record_requires_real_artifact(self, tmp_path):
        eng = BenchmarkEngine(tmp_path / "b.db")
        (tmp_path / "out.json").write_text("{}")
        bid = eng.record("e1", "p1", "ds", {"m": 1}, {"wer": 0.05}, tmp_path / "out.json")
        assert bid
        with pytest.raises(BenchmarkError):
            eng.record("e2", "p1", "ds", {}, {}, tmp_path / "missing.json")
        eng.close()

    def test_equivalence_gate(self, tmp_path):
        eng = BenchmarkEngine(tmp_path / "b.db")
        (tmp_path / "a.json").write_text("same")
        (tmp_path / "b.json").write_text("same")
        (tmp_path / "c.json").write_text("diff")
        assert eng.assert_equivalent(tmp_path / "a.json", tmp_path / "b.json",
                                     {"x": 1}, {"x": 1})["equivalent"] is True
        assert eng.assert_equivalent(tmp_path / "a.json", tmp_path / "c.json",
                                     {"x": 1}, {"x": 1})["equivalent"] is False
        assert eng.assert_equivalent(tmp_path / "a.json", tmp_path / "b.json",
                                     {"x": 1}, {"x": 2})["equivalent"] is False
        eng.close()

    def test_compare_deltas(self, tmp_path):
        eng = BenchmarkEngine(tmp_path / "b.db")
        cmp_ = eng.compare({"wer": 0.05, "p": 100}, {"wer": 0.04, "p": 90})
        assert cmp_["deltas"]["wer"]["delta_pct"] == -20.0
        assert cmp_["deltas"]["p"]["delta"] == -10
        eng.close()


# ---------------------------------------------------------------- Phase 8
class TestPhase8Approval:
    def test_gate_requires_human_approval(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "guru").mkdir(parents=True)
        (repo / "guru" / "pipeline.py").write_text("x")
        ws = IsolatedWorkspace(repo=repo, root=tmp_path / "cands",
                               source_paths=["guru/pipeline.py"])
        gate = ApprovalGate(tmp_path / "gate.db", ws)
        with pytest.raises(ApprovalError):
            gate.integrate()
        with pytest.raises(ApprovalError):
            gate.decide("APPROVE_Z")
        gate.decide("APPROVE_A", track="A", note="ok")
        assert gate.approved_track() == "A"
        ws.create("A")
        res = gate.integrate()
        assert res["applied"] is False
        assert Path(res["rollback_checkpoint"]).is_dir()
        res2 = gate.integrate(apply=True)
        assert res2["applied"] is True
        # live repo untouched even with apply=True
        assert (repo / "guru" / "pipeline.py").read_text() == "x"
        rb = gate.rollback(res2["integration_id"])
        assert rb["rolled_back"] is True
        gate.decide("REJECT_BOTH")
        assert gate.approved_track() is None
        with pytest.raises(ApprovalError):
            gate.integrate()
        ws.release("A")


# ---------------------------------------------------------------- Phase 9
class TestPhase9History:
    def test_comparability_rules(self, tmp_path):
        db = PhysicalTestDB(tmp_path / "h.db")
        a = db.create_session(expected_students=30, room_noise="quiet")
        b = db.create_session(expected_students=30, room_noise="quiet")
        c = db.create_session(expected_students=5, room_noise="noisy")
        for s in (a, b, c):
            db.start(s["id"]); db.stop(s["id"])
        h = History(db)
        assert h.comparable(a["id"], b["id"])["comparable"] is True
        comp = h.compare(a["id"], c["id"], {"wer": (0.05, 0.04)})
        assert comp["verdict"] == "INVALID_CONDITIONS"
        assert not comp["comparable"]
        db.close()

    def test_directions_and_missing(self, tmp_path):
        db = PhysicalTestDB(tmp_path / "h.db")
        a = db.create_session(expected_students=30)
        b = db.create_session(expected_students=30)
        for s in (a, b):
            db.start(s["id"]); db.stop(s["id"])
        h = History(db)
        assert h.compare(a["id"], b["id"], {"wer": (0.05, 0.04)})["verdict"] == "IMPROVED"
        assert h.compare(a["id"], b["id"], {"wer": (0.04, 0.06)})["verdict"] == "REGRESSION"
        assert h.compare(a["id"], b["id"], {"wer": (0.05, None)})["metrics"]["wer"][
            "direction"] == "INCONCLUSIVE"
        db.close()

    def test_next_plan_from_gaps(self, tmp_path):
        db = PhysicalTestDB(tmp_path / "h.db")
        small = db.create_session(expected_students=5, room_noise="quiet")
        db.start(small["id"]); db.stop(small["id"])
        h = History(db)
        plan = h.next_test_plan(small["id"])
        assert plan["human_confirmation_required"] is True
        assert any("30" in g for g in plan["recommended_next"])
        assert any("playback" in g for g in h.next_test_plan(small["id"])["recommended_next"])
        assert any("Scenario A" in g for g in h.next_test_plan(None)["recommended_next"])
        assert h.approved_fixes() == []  # honest empty, never invented
        db.close()
