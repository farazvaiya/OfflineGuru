"""Local live monitoring dashboard (Phase 3).

Serves a small HTML page + JSON API from a dedicated port, reading ONLY the
telemetry DB. Works fully offline. Failure of the dashboard never affects the
Offline Guru app (separate process, separate DB, read-only).

Honesty rules enforced in the renderer:
  - missing metrics render as "unavailable", never 0
  - delivery counts are labelled "delivered", not "heard"
  - inferred capture state is labelled as inference
  - NOT_EXECUTED items are shown explicitly
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .telemetry import TelemetryDB


class Dashboard:
    def __init__(self, db: TelemetryDB, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.db = db
        self.host = host
        self.port = port
        self._http: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- data ---------------------------------------------------------------
    def data(self) -> dict[str, Any]:
        events = self.db.events(limit=200)
        latest: dict[str, dict[str, Any]] = {}
        for e in events:  # events are newest-first; first wins per collector
            latest.setdefault(e["collector"], e)
        return {
            "telemetry": [
                {
                    "collector": e["collector"],
                    "status": e["status"],
                    "ts": e["ts"],
                    "payload": json.loads(e["payload_json"] or "{}"),
                    "reason": e["reason"],
                    "source": e["source"],
                }
                for e in events
            ],
            "latest": {k: json.loads(v["payload_json"] or "{}") for k, v in latest.items()},
            "errors": self.db.errors(limit=20),
            "validations": self.db.validations(),
            "student_events": self.db.student_events(limit=50),
        }

    # -- HTTP ---------------------------------------------------------------
    def _serve(self) -> None:
        db = self.db
        dash = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 (http.server API)
                try:
                    if self.path == "/api/telemetry":
                        self._send(200, json.dumps(dash.data(), default=str).encode(),
                                   "application/json")
                    elif self.path in ("/", "/index.html"):
                        self._send(200, _PAGE.encode(), "text/html; charset=utf-8")
                    else:
                        self._send(404, b"not found", "text/plain")
                except Exception as e:  # dashboard failure must never propagate
                    self._send(500, json.dumps({"error": str(e)}).encode(),
                               "application/json")

            def log_message(self, *a: Any) -> None:
                pass  # keep the dashboard quiet

        self._http = ThreadingHTTPServer((self.host, self.port), Handler)
        self._http.serve_forever()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._http is not None:
            self._http.shutdown()
            self._http.server_close()
            self._http = None
        if self._thread is not None:
            self._thread.join(timeout=3)


_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Offline Guru Physical Test Monitor</title>
<style>
body{font-family:system-ui,sans-serif;margin:1rem;background:#0f1115;color:#e6e6e6}
h1{font-size:1.2rem} section{margin:1rem 0} table{border-collapse:collapse;width:100%}
td,th{border:1px solid #333;padding:4px 8px;text-align:left;font-size:.85rem}
.ok{color:#7f7}.err{color:#f88}.un{color:#fa0}.na{color:#888;font-style:italic}
#refresh{position:fixed;top:.5rem;right:.5rem}
</style></head><body>
<button id="refresh">refresh</button>
<h1>Offline Guru — Physical Test Monitor (local, offline)</h1>
<div id="when"></div>
<section><h2>Collector latest</h2><div id="latest"></div></section>
<section><h2>Errors</h2><div id="errors"></div></section>
<section><h2>Validations</h2><div id="val"></div></section>
<section><h2>Student events (delivery only — not audible playback proof)</h2><div id="stu"></div></section>
<script>
function cell(v){ if(v===null||v===undefined) return '<span class="na">unavailable</span>';
  if(typeof v==='object') return '<span class="un">'+JSON.stringify(v).slice(0,120)+'</span>';
  return String(v); }
async function load(){
  const r = await fetch('/api/telemetry'); const d = await r.json();
  document.getElementById('when').textContent = 'latest sample count: '+d.telemetry.length;
  let h='<table><tr><th>collector</th><th>status</th><th>measured</th><th>source</th></tr>';
  for(const [k,v] of Object.entries(d.latest)){
    const cls = v.status==='ok'?'ok':(v.status==='unavailable'?'un':'err');
    h+='<tr><td>'+k+'</td><td class="'+cls+'">'+v.status+'</td><td>'+cell(v.payload)+'</td><td class="na">'+(v.source||'')+'</td></tr>';
  }
  document.getElementById('latest').innerHTML=h+'</table>';
  document.getElementById('errors').innerHTML = d.errors.length? '<table>'+d.errors.map(e=>
    '<tr><td>'+e.ts+'</td><td>'+e.component+'</td><td>'+e.message+'</td></tr>').join('')+'</table>'
    : '<span class="na">none</span>';
  document.getElementById('val').innerHTML = d.validations.length? '<table>'+d.validations.map(v=>
    '<tr><td>'+v.check_id+'</td><td>'+v.outcome+'</td><td>'+v.measured_json+'</td><td>'+v.evidence+'</td></tr>').join('')+'</table>'
    : '<span class="na">none recorded</span>';
  document.getElementById('stu').innerHTML = d.student_events.length? '<table>'+d.student_events.map(s=>
    '<tr><td>'+s.ts+'</td><td>'+s.event+'</td><td>'+s.detail+'</td></tr>').join('')+'</table>'
    : '<span class="na">none (delivery != audible playback)</span>';
}
document.getElementById('refresh').onclick=load; load(); setInterval(load,2000);
</script></body></html>
"""


def serve(db: TelemetryDB, host: str = "127.0.0.1", port: int = 8765) -> Dashboard:
    d = Dashboard(db, host, port)
    d.start()
    return d
