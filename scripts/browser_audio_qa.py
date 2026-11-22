#!/usr/bin/env python3
"""Exercise automatic student playback in real headless Firefox.

Run against a local Offline Guru server: .venv/bin/python scripts/browser_audio_qa.py
Firefox is given one real WebDriver click on the student's Start Listening button.
After that, every playback must start with no further interaction.

Unlike an earlier version of this script, playback is *measured*: the page records
the app's own ``guru:audio-start`` event, and the WAV served for ``/audio-test/*``
is a real 16-bit PCM file decoded by the browser's own ``decodeAudioData``. The
old version replaced ``decodeAudioData`` and read an array (``starts``) that
nothing ever wrote, so it could not tell playback apart from silence.

Headless Firefox has no output device; this proves the fetch -> decode -> start
path and its sequencing, not audibility on speakers.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import struct
import time
import urllib.request
from pathlib import Path

from browser_qa import Driver, api, wait_for

ROOT = Path(__file__).resolve().parents[1]


def wav_bytes(seconds: float = 0.18, rate: int = 22050) -> bytes:
    """A real, short mono 16-bit PCM WAV the browser can decode."""
    frames = b"".join(
        struct.pack("<h", int(9000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(int(rate * seconds))
    )
    header = (
        b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", len(frames))
    )
    return header + frames


AUDIO_B64 = base64.b64encode(wav_bytes()).decode()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    base = args.server.rstrip("/")
    with urllib.request.urlopen(base + "/healthz", timeout=5) as response:
        if response.status != 200:
            raise RuntimeError("Offline Guru server not healthy")
    created = api(base, "/api/teacher/session", {"title": "Browser audio QA"})
    joined = api(base, "/api/student/join", {"code": created["join_code"], "name": "Audio QA A"})
    token = joined["student"]["token"]
    session_id = joined["session"]["id"]
    joined_b = api(base, "/api/student/join", {"code": created["join_code"], "name": "Audio QA B"})
    token_b = joined_b["student"]["token"]

    d = Driver()
    d_b = Driver(4445)  # one WebDriver session per geckodriver process
    browser = d.new_session(1000, 800)
    browser_b = None
    results: list[dict] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})
        print(f"[{results[-1]['status']}] {name}: {detail}")

    def js(script: str, args: list | None = None, timeout: float = 5):
        end = time.time() + timeout
        while time.time() < end:
            try:
                return d.exec(browser, script, args)
            except Exception:
                time.sleep(.1)
        raise TimeoutError(script)

    def js_b(script: str, args: list | None = None, timeout: float = 5):
        end = time.time() + timeout
        while time.time() < end:
            try:
                return d_b.exec(browser_b, script, args)
            except Exception:
                time.sleep(.1)
        raise TimeoutError(script)

    def wait_starts(count: int, timeout: float = 10) -> list[dict]:
        end = time.time() + timeout
        while time.time() < end:
            got = js("return window.__qaAudio.starts()") or []
            if len(got) >= count:
                return got
            time.sleep(.05)
        return js("return window.__qaAudio.starts()") or []

    def ids(got: list[dict]) -> list[str]:
        return [x["id"] for x in got]

    def prepare(drv, br, tok: str) -> None:
        drv.goto(br, base + "/")
        if not wait_for(drv, br, "window.__guruAudioTest && document.getElementById('audioBtn')", 15):
            raise RuntimeError("student page/test hook did not load")
        drv.exec(br, "localStorage.setItem('guru.student.token', arguments[0]); localStorage.setItem('guru.student.code', arguments[1]); location.reload();",
               [tok, created["join_code"]])
        if not wait_for(drv, br, "window.__guruAudioTest && !document.getElementById('classView').classList.contains('hidden')", 15):
            raise RuntimeError("student session did not resume")
        drv.exec(br, "window.__guruAudioTest.setSession(arguments[0], arguments[1]);", [session_id, tok])
        # Record the app's own playback event and serve a real WAV for audio-test URLs.
        drv.exec(br, """
          window.__qaAudio = {
            _s: [],
            starts: () => window.__qaAudio._s,
            reset: () => { window.__qaAudio._s = []; },
          };
          if (!window.__qaAudio._wired) {
            window.__qaAudio._wired = true;
            window.addEventListener('guru:audio-start', (e) =>
              window.__qaAudio._s.push({id: e.detail.segmentId, seq: e.detail.seq, t: performance.now()}));
          }
          const originalFetch = window.fetch.bind(window);
          const bin = atob(arguments[0]);
          const bytes = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
          window.fetch = (input, init) => String(input).includes('/audio-test/')
            ? Promise.resolve({ok: true, status: 200, arrayBuffer: async () => bytes.buffer.slice(0)})
            : originalFetch(input, init);
        """, [AUDIO_B64])

    def click_audio(drv, br) -> None:
        element = drv._call("POST", f"/session/{br}/element", {
            "using": "css selector", "value": "#audioBtn"})["value"]
        element_id = element.get("element-6066-11e4-a52e-4f735466cecf") or element.get("ELEMENT")
        if not element_id:
            raise RuntimeError(f"WebDriver did not return element id: {element}")
        # W3C element click => trusted user activation, as a student's tap is.
        drv._call("POST", f"/session/{br}/element/{element_id}/click", {})

    def segment(sid: str, seq: int, state: str = "pending") -> dict:
        return {"id": sid, "seq": seq, "sequence_number": seq, "start_s": seq, "end_s": seq + 1,
                "en": "Source sentence", "bn": "অনূদিত বাক্য", "source_text": "Source sentence",
                "translated_text": "অনূদিত বাক্য", "audio_status": state, "segment_status": "final"}

    def send(br, script_runner, sid: str, seq: int, audio: bool = True, status: str = "pending") -> None:
        script_runner("window.__guruAudioTest.handleEvent(arguments[0])",
                      [{"type": "segment", "session_id": session_id, "segment": segment(sid, seq, status)}])
        if audio:
            script_runner("window.__guruAudioTest.handleEvent(arguments[0])",
                          [{"type": "audio.ready", "session_id": session_id, "segment_id": sid,
                            "seq": seq, "sequence_number": seq, "audio_url": "/audio-test/" + sid,
                            "audio_status": "ready"}])

    try:
        prepare(d, browser, token)
        browser_b = d_b.new_session(1000, 800)
        prepare(d_b, browser_b, token_b)

        # 1. Without the one-time gesture nothing may be reported as playing.
        send(browser, js, "s1", 1)
        time.sleep(.6)
        before = js("return {starts: window.__qaAudio.starts(), snap: window.__guruAudioTest.snapshot(), status: document.getElementById('audioStatus').textContent}")
        check("no playback before the one-time gesture", before["starts"] == [] and before["snap"]["played"] == [],
              json.dumps(before, ensure_ascii=False))

        # 2. One real click; no per-segment clicks from here on.
        click_audio(d, browser)
        if not wait_for(d, browser, "window.__guruAudioTest.snapshot().audioEnabled", 5):
            raise RuntimeError("trusted Start Listening click did not unlock audio")
        got = wait_starts(1)
        check("audio enabled once, newest ready segment plays automatically",
              ids(got) == ["s1"], str(ids(got)))

        # 3. A brand-new finalized segment plays with no further interaction.
        send(browser, js, "s2", 2)
        got = wait_starts(2)
        check("new audio plays without clicking replay", ids(got) == ["s1", "s2"], str(ids(got)))

        # 4. Duplicate delivery must not double-play.
        send(browser, js, "s2", 2)
        time.sleep(.5)
        got = js("return window.__qaAudio.starts()")
        check("duplicate segment is not played twice", ids(got) == ["s1", "s2"], str(ids(got)))

        # 5. Out-of-order/delayed WS events stay in sequence.
        send(browser, js, "s4", 4)
        time.sleep(.15)
        send(browser, js, "s3", 3)
        got = wait_starts(4)
        check("out-of-order events play in sequence", ids(got) == ["s1", "s2", "s3", "s4"], str(ids(got)))

        # 6. Failed audio does not block the next segment.
        send(browser, js, "s5", 5, audio=False, status="failed")
        send(browser, js, "s6", 6)
        got = wait_starts(5)
        check("failed audio does not block the next segment",
              ids(got) == ["s1", "s2", "s3", "s4", "s6"], str(ids(got)))

        # 7. audio.ready before its caption is reconciled, not lost.
        js("window.__guruAudioTest.handleEvent(arguments[0])",
           [{"type": "audio.ready", "session_id": session_id, "segment_id": "s7", "seq": 7,
             "sequence_number": 7, "audio_url": "/audio-test/s7", "audio_status": "ready"}])
        js("window.__guruAudioTest.handleEvent(arguments[0])",
           [{"type": "segment", "session_id": session_id, "segment": segment("s7", 7)}])
        got = wait_starts(6)
        check("audio arriving before its caption is still played",
              ids(got) == ["s1", "s2", "s3", "s4", "s6", "s7"], str(ids(got)))

        # 8. Manual replay remains available and repeats only on request.
        js("window.__guruAudioTest.replay('s2')")
        got = wait_starts(7)
        check("manual replay still works", ids(got) == ["s1", "s2", "s3", "s4", "s6", "s7", "s2"], str(ids(got)))

        end = time.time() + 8
        while time.time() < end:
            snap = js("return window.__guruAudioTest.snapshot()")
            if not snap["audioBusy"]:
                break
            time.sleep(.05)
        status = js("return {text: document.getElementById('audioStatus').textContent, state: document.getElementById('audioStatus').dataset.state, count: document.getElementById('audioStatus').dataset.playCount}")
        check("status reflects completion, not stale 'playing'",
              snap["audioStatus"] == "enabled" and str(status["count"]) == str(len(ids(got))),
              json.dumps({"snapshot": snap, "status": status}, ensure_ascii=False))

        # 9. A second student plays independently from its own queue.
        js_b("window.__qaAudio.reset()")
        click_audio(d_b, browser_b)
        if not wait_for(d_b, browser_b, "window.__guruAudioTest.snapshot().audioEnabled", 5):
            raise RuntimeError("second student could not enable audio")
        send(browser_b, js_b, "b1", 101)
        send(browser_b, js_b, "b2", 102)
        end = time.time() + 10
        got_b: list[dict] = []
        while time.time() < end:
            got_b = js_b("return window.__qaAudio.starts()") or []
            if len(got_b) >= 2:
                break
            time.sleep(.05)
        got_a = js("return window.__qaAudio.starts()")
        check("two students play the same lecture independently",
              ids(got_b) == ["b1", "b2"] and len(got_a) == 7,
              json.dumps({"student_b": ids(got_b), "student_a": ids(got_a)}))

        # 10. Far-behind audio is skipped rather than played late.
        js_b("window.__guruAudioTest.handleEvent(arguments[0])",
             [{"type": "segment", "session_id": session_id, "segment": segment("b40", 140)}])
        js_b("window.__guruAudioTest.handleEvent(arguments[0])",
             [{"type": "audio.ready", "session_id": session_id, "segment_id": "b40",
               "seq": 140, "sequence_number": 140, "audio_url": "/audio-test/b40", "audio_status": "ready"}])
        js_b("window.__guruAudioTest.handleEvent(arguments[0])",
             [{"type": "audio.ready", "session_id": session_id, "segment_id": "b10",
               "seq": 110, "sequence_number": 110, "audio_url": "/audio-test/b10", "audio_status": "ready"}])
        time.sleep(.8)
        got_b2 = js_b("return window.__qaAudio.starts()")
        check("audio too far behind the live edge is skipped",
              "b10" not in ids(got_b2), str(ids(got_b2)))

        out = {"results": results, "browser": "Firefox headless / geckodriver", "session_id": session_id}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1 if any(x["status"] == "FAIL" for x in results) else 0
    finally:
        d.quit(browser)
        if browser_b:
            d_b.quit(browser_b)


if __name__ == "__main__":
    raise SystemExit(main())
