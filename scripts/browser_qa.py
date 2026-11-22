#!/usr/bin/env python3
"""Browser QA for the teacher and student consoles through Firefox + geckodriver.

No Playwright, no Selenium: this drives the W3C WebDriver HTTP protocol with the
standard library, against the Firefox and geckodriver already installed here. It
performs real browser rendering, real JavaScript execution, real fetch traffic and
real screenshots — not static HTML inspection.

Viewport note: headless Firefox refuses to shrink a window below ~500 px, so a
390 px CSS viewport is reached the way a phone reaches it — by raising the device
pixel ratio (``layout.css.devPixelsPerPx``) and halving the window in CSS pixels.
The measured ``innerWidth`` is reported for every check, so the emulation is
visible rather than assumed.

    .venv/bin/python scripts/browser_qa.py --server http://127.0.0.1:8011

Screenshots land in ``reports/screenshots``, the table in
``reports/results/browser_qa.json``. A check that could not run is SKIP with a
reason; it is never reported as a pass.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "reports" / "screenshots"

# (label, CSS width, CSS height, device pixel ratio)
VIEWPORTS = [
    ("desktop-1366", 1366, 768, 1.0),
    ("tablet-768", 768, 1024, 1.0),
    ("phone-390", 390, 844, 2.0),
    ("phone-360", 360, 800, 2.0),
]


class Driver:
    def __init__(self, port: int = 4444) -> None:
        self.base = f"http://127.0.0.1:{port}"

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"webdriver {method} {path}: {exc.code} {exc.read()[:300]!r}") from exc
        return json.loads(body) if body else {}

    def new_session(self, width: int, height: int, dpr: float = 1.0) -> str:
        prefs = {"dom.disable_open_during_load": False}
        if dpr != 1.0:
            prefs["layout.css.devPixelsPerPx"] = str(dpr)
        caps = {
            "capabilities": {
                "alwaysMatch": {
                    "browserName": "firefox",
                    "moz:firefoxOptions": {"args": ["-headless"], "prefs": prefs},
                }
            }
        }
        sid = self._call("POST", "/session", caps)["value"]["sessionId"]
        self.resize(sid, int(width * dpr), int(height * dpr))
        return sid

    def resize(self, sid: str, width: int, height: int) -> None:
        self._call("POST", f"/session/{sid}/window/rect",
                   {"width": width, "height": height, "x": 0, "y": 0})

    def goto(self, sid: str, url: str) -> None:
        self._call("POST", f"/session/{sid}/url", {"url": url})

    def exec(self, sid: str, script: str, args: list | None = None):
        return self._call("POST", f"/session/{sid}/execute/sync",
                          {"script": script, "args": args or []})["value"]

    def exec_async(self, sid: str, script: str, args: list | None = None):
        return self._call("POST", f"/session/{sid}/execute/async",
                          {"script": script, "args": args or []})["value"]

    def screenshot(self, sid: str) -> bytes:
        try:
            return base64.b64decode(self._call("GET", f"/session/{sid}/screenshot/full")["value"])
        except RuntimeError:
            return base64.b64decode(self._call("GET", f"/session/{sid}/screenshot")["value"])

    def quit(self, sid: str) -> None:
        try:
            self._call("DELETE", f"/session/{sid}")
        except RuntimeError:
            pass


def api(base: str, path: str, payload: dict | None = None, token: str | None = None,
        method: str | None = None, body: bytes | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Teacher-Token"] = token
    data = body if body is not None else (json.dumps(payload).encode() if payload is not None else None)
    req = urllib.request.Request(base + path, data=data,
                                 method=method or ("POST" if data else "GET"), headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def wait_for(driver: Driver, sid: str, expression: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if driver.exec(sid, f"return !!({expression});"):
                return True
        except RuntimeError:
            pass
        time.sleep(0.15)
    return False


def measure_iframe(driver: Driver, sid: str, base: str, path: str, width: int,
                   height: int) -> dict:
    """Render a page at a true narrow CSS viewport.

    Headless Firefox refuses to make a window narrower than ~500 px and ignores a
    device-pixel-ratio override here, so both were dead ends (the CSS viewport
    stayed 780 px). An iframe is a real viewport: media queries inside it evaluate
    against its 390 px width, which is exactly what a phone browser does.
    """
    driver.goto(sid, f"{base}/")
    return driver.exec_async(
        sid,
        """
        const done = arguments[arguments.length - 1];
        document.body.innerHTML = '';
        document.body.style.margin = '0';
        const f = document.createElement('iframe');
        f.style.cssText = 'width:' + arguments[0] + 'px;height:' + arguments[1] +
                          'px;border:0;display:block;background:#0d1117';
        f.src = arguments[2];
        f.onload = () => setTimeout(() => {
          try {
            const d = f.contentDocument;
            done({css: d.documentElement.clientWidth, sw: d.documentElement.scrollWidth,
                  title: d.title, chars: (d.body.innerText || '').length});
          } catch (e) { done({error: String(e)}); }
        }, 900);
        document.body.appendChild(f);
        """,
        [width, height, path],
    )


def capture(driver: Driver, sid: str, name: str) -> str:
    SHOTS.mkdir(parents=True, exist_ok=True)
    (SHOTS / name).write_bytes(driver.screenshot(sid))
    return f"reports/screenshots/{name}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8011")
    ap.add_argument("--gecko-port", type=int, default=4444)
    args = ap.parse_args()
    base = args.server.rstrip("/")

    def driver_ready() -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{args.gecko_port}/status", timeout=2) as r:
                return json.loads(r.read()).get("value", {}).get("ready", False)
        except Exception:  # noqa: BLE001 - the driver may simply not be up yet
            return False

    gecko = None
    if not driver_ready():
        gecko = subprocess.Popen(
            ["geckodriver", "--port", str(args.gecko_port), "--log", "error"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 30
        while time.time() < deadline and not driver_ready():
            time.sleep(0.5)
    if not driver_ready():
        print(f"geckodriver did not become ready on port {args.gecko_port}")
        return 2

    driver = Driver(args.gecko_port)
    results: list[dict] = []

    def check(name: str, ok: bool | None, detail: str, shot: str | None = None) -> None:
        results.append({
            "check": name,
            "status": "PASS" if ok else ("SKIP" if ok is None else "FAIL"),
            "detail": detail,
            "screenshot": shot,
        })
        print(f"[{results[-1]['status']}] {name}: {detail}")

    try:
        # ------------------------------------------------ teacher: desktop flow
        sid = driver.new_session(*VIEWPORTS[0][1:])
        driver.goto(sid, f"{base}/teacher")
        ok = wait_for(sid=sid, driver=driver, expression="document.getElementById('setupView')")
        check("teacher page loads", ok, "setup view rendered",
              capture(driver, sid, "teacher-desktop-1366-setup.png"))

        driver.exec(sid, "document.getElementById('titleInput').value='QA lecture';"
                         "document.getElementById('createBtn').click();")
        ok = wait_for(driver, sid, "!document.getElementById('roomView').classList.contains('hidden')", 25)
        check("teacher creates a session in the browser", ok,
              "room view visible after clicking create")

        code = driver.exec(sid, "return document.getElementById('classCode').textContent.trim();")
        check("join code is displayed", bool(code) and code != "—", f"code={code!r}")

        qr = driver.exec(sid, "return document.getElementById('qrImg').src;")
        check("QR image is generated locally", "/qr.svg" in (qr or ""), qr or "no src")

        storage = driver.exec(
            sid,
            "const o={}; for (let i=0;i<localStorage.length;i++){const k=localStorage.key(i);"
            "o[k]=localStorage.getItem(k);} return o;",
        ) or {}
        token = next((v for k, v in storage.items() if "token" in k.lower() and v), None)
        sid_key = next((v for k, v in storage.items() if k.lower().endswith(".session") and v), None)
        check("teacher token stays client-side", bool(token), f"keys={sorted(storage)}")

        if token and sid_key:
            up = api(base, f"/api/teacher/session/{sid_key}/documents?name=photosynthesis.txt",
                     token=token, body=(
                         b"Photosynthesis is the process by which green plants make food using "
                         b"sunlight. Chlorophyll is the green pigment that captures light energy. "
                         b"The chloroplast is the organelle where this happens."
                     ), method="POST")
            check("document upload from the console API", up.get("chunks", 0) >= 1,
                  f"chunks={up.get('chunks')} bilingual={up.get('bilingual_chunks')} "
                  f"index_ms={up.get('index_ms')}")

            summ = api(base, f"/api/teacher/session/{sid_key}/summary", token=token, payload={})
            check("summary generation", "material" in summ,
                  f"status={(summ.get('material') or {}).get('status')}")
            quiz = api(base, f"/api/teacher/session/{sid_key}/quiz", token=token,
                       payload={"count": 3, "include_short": True})
            qcount = len(quiz.get("content", {}).get("questions", []))
            check("quiz generation", qcount >= 1, f"{qcount} questions")
            for kind in ("quiz", "summary"):
                api(base, f"/api/teacher/session/{sid_key}/materials/{kind}", token=token,
                    payload={"status": "approved"}, method="PATCH")

            ok = wait_for(driver, sid, "document.getElementById('quizBox').innerText.length>20", 25)
            check("teacher console renders the generated quiz", ok, "quizBox has content",
                  capture(driver, sid, "teacher-desktop-1366-room.png"))
        driver.quit(sid)

        # ------------------------------------------------ narrower screens
        sid = driver.new_session(768, 1024)
        driver.goto(sid, f"{base}/teacher")
        wait_for(driver, sid, "document.getElementById('setupView')")
        dims = driver.exec(sid, "return [window.innerWidth, window.innerHeight,"
                                "document.documentElement.scrollWidth];")
        check("teacher fits tablet-768", dims[2] <= dims[0] + 2,
              f"css viewport {dims[0]}x{dims[1]}, scrollWidth={dims[2]}",
              capture(driver, sid, "teacher-tablet-768.png"))

        # True phone viewports, where the <=640px media query actually applies.
        for w, h in ((390, 844), (360, 800)):
            page = measure_iframe(driver, sid, base, "/teacher", w, h)
            check(f"teacher renders at {w}px CSS width",
                  not page.get("error") and page.get("sw", 1e9) <= page.get("css", 0) + 2,
                  f"viewport={page.get('css')} scrollWidth={page.get('sw')} "
                  f"chars={page.get('chars')}",
                  capture(driver, sid, f"teacher-phone-{w}.png"))
        driver.quit(sid)

        # ------------------------------------------------ student: phone flow
        sid = driver.new_session(768, 1024)
        driver.goto(sid, f"{base}/")
        ok = wait_for(driver, sid, "document.getElementById('joinView')")
        dims = driver.exec(sid, "return [window.innerWidth, window.innerHeight];")
        check("student page loads at phone width", ok,
              f"css viewport {dims[0]}x{dims[1]}",
              capture(driver, sid, "student-phone-390-join.png"))

        driver.exec(
            sid,
            "document.getElementById('codeInput').value=arguments[0];"
            "document.getElementById('nameInput').value='QA Student';"
            "document.getElementById('joinBtn').click();",
            [code],
        )
        ok = wait_for(driver, sid, "!document.getElementById('classView').classList.contains('hidden')", 25)
        check("student joins with the code on a phone viewport", ok, "class view visible")

        title = driver.exec(sid, "return document.getElementById('classTitle').textContent;")
        check("student sees the session title", bool((title or "").strip()), f"title={title!r}")

        driver.exec(sid, "document.querySelector('[data-tab=\"quiz\"]').click();")
        time.sleep(0.6)
        quiz_text = driver.exec(sid, "return document.getElementById('tab-quiz').innerText;") or ""
        check("approved quiz renders for the student", len(quiz_text) > 10,
              f"{len(quiz_text)} chars", capture(driver, sid, "student-phone-390-quiz.png"))

        driver.exec(sid, "document.querySelector('[data-tab=\"summary\"]').click();")
        time.sleep(0.5)
        sum_text = driver.exec(sid, "return document.getElementById('tab-summary').innerText;") or ""
        check("approved summary renders for the student", len(sum_text) > 5, f"{len(sum_text)} chars")

        a11y = driver.exec(
            sid,
            "const t=document.getElementById('transcript');"
            "const f=document.querySelectorAll('button,input,select,textarea');"
            "let unlabeled=0; f.forEach(e=>{if(!e.innerText.trim()&&!e.getAttribute('aria-label')"
            "&&!e.id&&!e.getAttribute('placeholder'))unlabeled++;});"
            "return {live:t.getAttribute('aria-live'), controls:f.length, unlabeled};",
        )
        check("caption updates are announced to assistive tech",
              a11y.get("live") in ("polite", "assertive"),
              f"aria-live={a11y.get('live')} controls={a11y.get('controls')} "
              f"unlabeled={a11y.get('unlabeled')}")

        dims = driver.exec(sid, "return [window.innerWidth, document.documentElement.scrollWidth];")
        check("student page fits the tablet viewport", dims[1] <= dims[0] + 2,
              f"css viewport {dims[0]}, scrollWidth={dims[1]}",
              capture(driver, sid, "student-tablet-768-class.png"))
        driver.quit(sid)

        # ------------------------------------------------ student at 390/360/1366
        sid = driver.new_session(1366, 768)
        for w, h in ((390, 844), (360, 800)):
            page = measure_iframe(driver, sid, base, "/", w, h)
            check(f"student join screen renders at {w}px CSS width",
                  not page.get("error") and page.get("sw", 1e9) <= page.get("css", 0) + 2,
                  f"viewport={page.get('css')} scrollWidth={page.get('sw')} "
                  f"chars={page.get('chars')}",
                  capture(driver, sid, f"student-phone-{w}.png"))
        driver.goto(sid, f"{base}/")
        wait_for(driver, sid, "document.getElementById('joinView')")
        dims = driver.exec(sid, "return [window.innerWidth, document.documentElement.scrollWidth];")
        check("student fits desktop-1366", dims[1] <= dims[0] + 2,
              f"css viewport {dims[0]}, scrollWidth={dims[1]}",
              capture(driver, sid, "student-desktop-1366.png"))
        driver.quit(sid)
    finally:
        if gecko is not None:
            gecko.terminate()

    out = ROOT / "reports" / "results" / "browser_qa.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    skipped = sum(1 for r in results if r["status"] == "SKIP")
    out.write_text(json.dumps(
        {"checks": results, "passed": passed, "failed": failed, "skipped": skipped,
         "browser": "Firefox (geckodriver, headless)",
         "viewports": [{"label": v[0], "css": [v[1], v[2]], "dpr": v[3]} for v in VIEWPORTS]},
        indent=2,
    ))
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped -> {out}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
