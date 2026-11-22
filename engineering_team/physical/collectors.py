"""Live telemetry collectors for physical test monitoring.

Every collector reads Offline Guru READ-ONLY:
  - HTTP GET /api/health (and session endpoints when a base_url is given)
  - the app's SQLite DB is NOT written; /api/health is the only source for
    pipeline counters, so no app-DB coupling is required.

Collector contract:
  name() -> str
  collect() -> dict with "status" in {"ok", "unavailable", "error"} and
               "measured" (real data) or "reason" (why absent).
Missing values are NEVER zero; they are "unavailable" with a reason.

Collectors are independent: one raising never kills the loop; failures are
recorded as error events. No collector blocks the audio pipeline: HTTP polls
are bounded by timeout, at most 1 Hz, and results go to a bounded queue.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any

POLL_INTERVAL_S = 1.0
HTTP_TIMEOUT_S = 2.0
MAX_QUEUE = 4096


class CollectorError(RuntimeError):
    pass


class _Base:
    def __init__(self, health_url: str) -> None:
        self.health_url = health_url

    def name(self) -> str:
        return type(self).__name__.replace("Collector", "").lower()

    def collect(self) -> dict[str, Any]:
        raise NotImplementedError


def _get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


class HealthSnapshotCollector(_Base):
    """Whole /api/health payload as the base snapshot (source of truth)."""

    def collect(self) -> dict[str, Any]:
        try:
            data = _get_json(self.health_url)
        except Exception as e:
            return {"status": "error", "reason": f"health endpoint unreachable: {type(e).__name__}: {e}"}
        if not isinstance(data, dict) or not data.get("ok"):
            return {"status": "error", "reason": "health endpoint returned not-ok"}
        return {"status": "ok", "measured": data, "source": "GET /api/health"}


class CaptureCollector(_Base):
    """Capture health, inferred read-only from pipeline health + errors.

    LIMITATION: /api/health exposes no arecord liveness (GAP G-1). We infer:
      capture_state = "active"   if pipeline running and no capture error
                    "error"      if pipeline.health.last_error mentions capture
                    "unavailable" otherwise.
    Inference is labelled, never claimed as measured.
    """

    def collect(self) -> dict[str, Any]:
        try:
            h = _get_json(self.health_url)
        except Exception as e:
            return {"status": "error", "reason": f"health unreachable: {e}"}
        pipe = h.get("session", {})
        stats = pipe.get("stats") or {}
        last_error = pipe.get("last_error") or stats.get("last_error") or ""
        warnings = " ".join(str(w) for w in h.get("warnings", []))
        inferred: str
        if "capture" in last_error.lower() or "capture" in warnings.lower():
            inferred = "error"
        elif pipe.get("status") in ("running", "paused"):
            inferred = "active (inferred)"
        else:
            inferred = "unavailable (inferred)"
        return {
            "status": "ok",
            "measured": {
                "pipeline_status": pipe.get("status"),
                "capture_state": inferred,
                "capture_state_source": "inference from /api/health, NOT direct process liveness",
                "last_error": last_error or None,
            },
            "source": "GET /api/health (inference; GAP G-1: no direct arecord liveness)",
        }


class ASRCollector(_Base):
    """ASR counters that ARE exposed: decodes, commits, partials, backlog, drops."""

    def collect(self) -> dict[str, Any]:
        try:
            h = _get_json(self.health_url)
        except Exception as e:
            return {"status": "error", "reason": f"health unreachable: {e}"}
        s = h.get("session", {}).get("stats") or {}
        if not s:
            return {"status": "unavailable", "reason": "no pipeline stats in /api/health"}
        return {
            "status": "ok",
            "measured": {
                "stream_decodes": s.get("stream_decodes"),
                "stream_commits": s.get("stream_commits"),
                "stream_partials": s.get("stream_partials"),
                "stream_backlog": s.get("stream_backlog"),
                "segments_finalized": s.get("segments_finalized"),
                "segments_empty": s.get("segments_empty"),
                "asr_queue_drops": s.get("asr_queue_drops"),
                "audio_carried_s": s.get("audio_carried_s"),
                "final_tail_decode_count": None,
                "final_tail_decode_note": "NOT_EXPOSED by app (GAP G-5); do not treat as zero",
            },
            "source": "GET /api/health session.stats",
        }


class TranslationCollector(_Base):
    def collect(self) -> dict[str, Any]:
        try:
            h = _get_json(self.health_url)
        except Exception as e:
            return {"status": "error", "reason": f"health unreachable: {e}"}
        s = h.get("session", {}).get("stats") or {}
        metrics = h.get("metrics", {})
        return {
            "status": "ok",
            "measured": {
                "segments_translated": s.get("segments_translated"),
                "enrichment_queue_drops": s.get("enrichment_queue_drops"),
                "translate_ms_summary": metrics.get("translate_ms"),
                "translation_queue_size": None,
                "translation_queue_note": "NOT_EXPOSED by app (GAP G-6); do not treat as zero",
            },
            "source": "GET /api/health session.stats + metrics",
        }


class TTSCollector(_Base):
    def collect(self) -> dict[str, Any]:
        try:
            h = _get_json(self.health_url)
        except Exception as e:
            return {"status": "error", "reason": f"health unreachable: {e}"}
        s = h.get("session", {}).get("stats") or {}
        metrics = h.get("metrics", {})
        return {
            "status": "ok",
            "measured": {
                "tts_ms_summary": metrics.get("tts_ms"),
                "tts_queue_drops": s.get("tts_queue_drops"),
                "tts_cache_hits": None,
                "tts_cache_misses": None,
                "tts_cache_note": "NOT_EXPOSED by app (GAP G-7); do not treat as zero",
            },
            "source": "GET /api/health session.stats + metrics",
        }


class StudentPlaybackCollector(_Base):
    """Delivery-side evidence ONLY. Audible playback is never claimed here.

    Rule 3.4: server delivery != student heard it. Playback proof requires
    human_feedback or explicit student ack; both stay NOT_EXECUTED here.
    """

    def collect(self) -> dict[str, Any]:
        try:
            h = _get_json(self.health_url)
        except Exception as e:
            return {"status": "error", "reason": f"health unreachable: {e}"}
        subs = h.get("clients", {}).get("subscribers", [])
        return {
            "status": "ok",
            "measured": {
                "subscribers": [
                    {"role": x.get("role"), "delivered": x.get("delivered"),
                     "dropped": x.get("dropped"), "queued": x.get("queued")}
                    for x in subs
                ],
                "audible_playback": "NOT_EXECUTED",
                "playback_note": "server cannot observe audible playback; "
                                 "requires student ack or human feedback (rule 3.4)",
            },
            "source": "GET /api/health clients.subscribers",
        }


class NetworkCollector(_Base):
    def collect(self) -> dict[str, Any]:
        try:
            h = _get_json(self.health_url)
        except Exception as e:
            return {"status": "error", "reason": f"health unreachable: {e}"}
        net = h.get("network", {})
        return {
            "status": "ok",
            "measured": {
                "network": net,
                "ws_reconnect_events": None,
                "delivery_attempts": None,
                "acks": None,
                "message_delivery_latency": None,
                "missing_note": "GAP G-8/G-10: bus exposes counts only, no per-message latency or reconnect log",
            },
            "source": "GET /api/health network",
        }


class SystemCollector:
    """Host-side system metrics via psutil (monitoring process, not the app)."""

    def __init__(self) -> None:
        try:
            import psutil  # available in project venv
            self._ps = psutil
        except ImportError:
            self._ps = None

    def name(self) -> str:
        return "system"

    def collect(self) -> dict[str, Any]:
        if self._ps is None:
            return {"status": "unavailable", "reason": "psutil not installed"}
        try:
            cpu = self._ps.cpu_percent(interval=None)
            vm = self._ps.virtual_memory()
            load = None
            try:
                load = __import__("os").getloadavg()
            except OSError:
                pass
            temps = {}
            try:
                for zone in self._ps.sensors_temperatures().values():
                    for entry in zone:
                        temps[entry.label or entry.current] = entry.current
            except (AttributeError, FileNotFoundError):
                temps = None
            return {
                "status": "ok",
                "measured": {
                    "cpu_percent": cpu,
                    "ram_total_mb": round(vm.total / 1e6),
                    "ram_available_mb": round(vm.available / 1e6),
                    "load1_5_15": load,
                    "temperature_c": temps,
                    "temperature_note": None if temps is not None else "no sensor data on this host (GAP G-12)",
                },
                "source": "psutil (monitor host)",
            }
        except Exception as e:
            return {"status": "error", "reason": f"psutil failure: {e}"}


class CollectorLoop:
    """Bounded, non-blocking aggregator. One thread, 1 Hz, independent failures."""

    def __init__(self, health_url: str, out_q: queue.Queue | None = None,
                 interval_s: float = POLL_INTERVAL_S) -> None:
        self.health_url = health_url
        self.out_q = out_q if out_q is not None else queue.Queue(maxsize=MAX_QUEUE)
        self.interval_s = interval_s
        self.collectors: list[_Base] = [
            HealthSnapshotCollector(health_url),
            CaptureCollector(health_url),
            ASRCollector(health_url),
            TranslationCollector(health_url),
            TTSCollector(health_url),
            StudentPlaybackCollector(health_url),
            NetworkCollector(health_url),
            SystemCollector(),
        ]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.polls = 0
        self.collector_errors: dict[str, int] = {}

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            for c in self.collectors:
                try:
                    result = c.collect()
                except Exception as e:  # one collector failing never kills the loop
                    result = {"status": "error", "reason": f"{type(e).__name__}: {e}"}
                    self.collector_errors[c.name()] = self.collector_errors.get(c.name(), 0) + 1
                self.out_q.put({
                    "ts": time.time(),
                    "collector": c.name(),
                    **result,
                })
                if self.out_q.qsize() > MAX_QUEUE:  # defensive; put on bounded queue
                    try:
                        self.out_q.get_nowait()
                    except queue.Empty:
                        pass
            self.polls += 1
            elapsed = time.monotonic() - t0
            delay = max(0.0, self.interval_s - elapsed)
            self._stop.wait(delay)
