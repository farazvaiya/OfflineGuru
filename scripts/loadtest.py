#!/usr/bin/env python3
"""Classroom capacity test: N students on one laptop hotspot.

It creates a real session on a running server, replays a lecture WAV, connects N
students over WebSockets, and measures join time, WebSocket latency, event
fan-out spread, audio download time and server-side drops.

    # terminal 1
    .venv/bin/python -m uvicorn guru.server.app:app --host 0.0.0.0 --port 8000
    # terminal 2
    .venv/bin/python scripts/loadtest.py --clients 30 --wav data/demo/lecture_45s.wav

This measures the *application*, not the Wi-Fi radio: run it from the same laptop
for an upper bound, and from real phones over the hotspot for a radio estimate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parent.parent


class Student:
    def __init__(self, name: str, token: str, ws) -> None:
        self.name = name
        self.token = token
        self.ws = ws
        self.segments: dict[int, float] = {}
        self.audio_urls: dict[int, str] = {}
        self.audio_done: set[int] = set()
        self.stable_events = 0
        self.partial_events = 0
        self.reconnects = 0
        self.join_ms = 0.0
        self.connect_ms = 0.0
        self.errors = 0
        self.task: asyncio.Task | None = None

    async def listen(self) -> None:
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if msg.get("type") == "segment":
                    self.segments[int(msg["segment"]["seq"])] = time.perf_counter()
                elif msg.get("type") == "transcript.stable":
                    self.stable_events += 1
                elif msg.get("type") == "transcript.partial":
                    self.partial_events += 1
                elif msg.get("type") == "audio.ready":
                    seq = msg.get("sequence_number", msg.get("seq"))
                    self.audio_urls[int(seq)] = msg["audio_url"]
                elif msg.get("type") == "ping":
                    await self.ws.send("ping")
        except Exception:  # noqa: BLE001  # client may vanish at any time
            self.errors += 1


async def connect_student(base: str, ws_base: str, code: str, idx: int) -> Student:
    async with httpx.AsyncClient(timeout=20) as client:
        t0 = time.perf_counter()
        res = await client.post(f"{base}/api/student/join", json={"code": code, "name": f"Load{idx:02d}"})
        res.raise_for_status()
        data = res.json()
        join_ms = (time.perf_counter() - t0) * 1000
        token = data["student"]["token"]

        t1 = time.perf_counter()
        ws = await websockets.connect(
            f"{ws_base}/ws/student?token={token}", open_timeout=20, ping_interval=None, max_queue=None
        )
        connect_ms = (time.perf_counter() - t1) * 1000
    student = Student(f"Load{idx:02d}", token, ws)
    student.join_ms = join_ms
    student.connect_ms = connect_ms
    student.task = asyncio.create_task(student.listen())
    return student


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(len(s) - 1, round((p / 100) * (len(s) - 1)))], 1)


async def main(args: argparse.Namespace) -> int:
    base = args.base.rstrip("/")
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
    out: dict = {"clients": args.clients, "wav": args.wav}

    async with httpx.AsyncClient(timeout=30) as client:
        # Before a session exists /api/health only answers loopback, which is where
        # this script runs; later calls carry the teacher token explicitly.
        health0 = (await client.get(f"{base}/api/health")).json()
        res = await client.post(f"{base}/api/teacher/session",
                                json={"title": f"Load test {args.clients}"})
        if res.status_code == 409:
            print("A session is already running on the server. Stop it first.")
            return 2
        res.raise_for_status()
        sess = res.json()
        token = sess["teacher_token"]
        code = sess["join_code"]
        sid = sess["session"]["id"]
        headers = {"X-Teacher-Token": token}
        print(f"session {sid} code {code}, join URL {sess['join_url']}")

        print(f"connecting {args.clients} students…")
        t_connect0 = time.perf_counter()
        students = await asyncio.gather(
            *(connect_student(base, ws_base, code, i) for i in range(args.clients))
        )
        print(f"  all connected in {time.perf_counter() - t_connect0:.2f}s")

        res = await client.post(
            f"{base}/api/teacher/session/{sid}/start",
            headers=headers,
            json={"capture": "wav", "wav_path": str(ROOT / args.wav), "speed": 1.0},
        )
        if res.status_code != 200:
            print("start failed:", res.status_code, res.text)
            return 3
        print("lecture replay started; collecting events…")

        t_start = time.perf_counter()
        audio_fetched = 0
        audio_times: list[float] = []
        reconnect_at = args.duration / 2 if args.reconnect else None
        reconnected = False
        while time.perf_counter() - t_start < args.duration:
            await asyncio.sleep(0.5)
            if reconnect_at and not reconnected and time.perf_counter() - t_start >= reconnect_at:
                reconnected = True
                # Drop and rejoin a third of the class mid-lecture. A reconnected
                # student must recover the transcript over REST and must not
                # replay audio that was already delivered.
                victims = students[: max(1, len(students) // 3)]
                print(f"  reconnecting {len(victims)} students mid-lecture…")
                for st in victims:
                    try:
                        await st.ws.close()
                    except Exception:  # noqa: BLE001
                        pass
                    if st.task:
                        st.task.cancel()
                    fresh = await connect_student(base, ws_base, code, students.index(st))
                    st.ws = fresh.ws
                    st.task = fresh.task
                    st.reconnects += 1
                    snap = await client.get(
                        f"{base}/api/student/state?after_seq=0&token={st.token}"
                    )
                    if snap.status_code == 200:
                        for seg in snap.json().get("segments", []):
                            st.segments.setdefault(int(seg["seq"]), time.perf_counter())
            if args.audio_clients and students:
                # Fetch segment audio like a real student device would.
                for st in students[: args.audio_clients]:
                    pending = [seq for seq in st.audio_urls if seq not in st.audio_done]
                    if not pending:
                        continue
                    seq = max(pending)
                    st.audio_done.add(seq)
                    t0 = time.perf_counter()
                    r = await client.get(
                        f"{base}/api/student/audio/{st.audio_urls[seq].rsplit('/', 1)[-1]}"
                        f"?token={st.token}"
                    )
                    audio_times.append((time.perf_counter() - t0) * 1000)
                    audio_fetched += r.status_code == 200

        await client.post(f"{base}/api/teacher/session/{sid}/stop", headers=headers)
        health1 = (await client.get(f"{base}/api/health", headers=headers)).json()

        # Give sockets a moment to drain queued events.
        await asyncio.sleep(2.0)

    for st in students:
        if st.task:
            st.task.cancel()
        try:
            await st.ws.close()
        except Exception:  # noqa: BLE001, S110  # already disconnected
            pass

    per_seq: dict[int, list[float]] = {}
    for st in students:
        for seq, ts in st.segments.items():
            per_seq.setdefault(seq, []).append(ts)

    expected = max(per_seq) if per_seq else 0
    received_counts = [len(st.segments) for st in students]
    spreads = [(max(v) - min(v)) * 1000 for v in per_seq.values() if len(v) > 1]
    join_times = [st.join_ms for st in students]
    connect_times = [st.connect_ms for st in students]
    errors = sum(st.errors for st in students)

    print("\n=== results ===")
    print(f"students:            {len(students)}")
    print(f"segments published:  {expected}")
    print(f"segments received:   min {min(received_counts)} / median {int(statistics.median(received_counts))} / max {max(received_counts)}")
    print(f"student join ms:     p50 {pct(join_times,50)}  p95 {pct(join_times,95)}  max {max(join_times):.0f}")
    print(f"ws connect ms:       p50 {pct(connect_times,50)}  p95 {pct(connect_times,95)}  max {max(connect_times):.0f}")
    print(f"fan-out spread ms:   p50 {pct(spreads,50)}  p95 {pct(spreads,95)}  max {max(spreads) if spreads else 0:.0f}")
    print(f"socket errors:       {errors}")
    print(f"idle pull ms:        p50 {pct(audio_times,50)}  p95 {pct(audio_times,95)}")
    print(f"ram available MB:    {health0['resources']['ram_available_mb']} -> {health1['resources']['ram_available_mb']}")
    print(f"bus subscribers:     {health1['clients']['bus']}")
    print(f"subscriber drops:    {sum(s['dropped'] for s in health1['clients']['subscribers'])}")
    print(f"pipeline drops:      {health1['session']['stats']}")
    print(f"stable/partial evts: {sum(st.stable_events for st in students)} / {sum(st.partial_events for st in students)}")
    print(f"reconnects:          {sum(st.reconnects for st in students)}")
    print(f"e2e p50/p95 ms:      {health1['metrics']['e2e_ms'].get('p50')} / {health1['metrics']['e2e_ms'].get('p95')}")

    out.update({
        "segments_published": expected,
        "received_min": min(received_counts) if received_counts else 0,
        "received_median": int(statistics.median(received_counts)) if received_counts else 0,
        "join_ms": {"p50": pct(join_times, 50), "p95": pct(join_times, 95)},
        "ws_connect_ms": {"p50": pct(connect_times, 50), "p95": pct(connect_times, 95)},
        "fanout_spread_ms": {"p50": pct(spreads, 50), "p95": pct(spreads, 95)},
        "socket_errors": errors,
        "stable_events": sum(st.stable_events for st in students),
        "partial_events": sum(st.partial_events for st in students),
        "reconnects": sum(st.reconnects for st in students),
        "ram_available_mb": [health0["resources"]["ram_available_mb"], health1["resources"]["ram_available_mb"]],
        "bus_drops": sum(s["dropped"] for s in health1["clients"]["subscribers"]),
        "pipeline": health1["session"],
        "metrics": health1["metrics"],
        "network": health1["network"],
    })
    out_path = ROOT / "data" / "eval" / f"loadtest_{args.clients}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    print(f"wrote {out_path}")

    async with httpx.AsyncClient(timeout=30) as client:
        await client.delete(f"{base}/api/teacher/session/{sid}?confirm=true", headers=headers)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--clients", type=int, default=30)
    ap.add_argument("--wav", default="data/demo/lecture_45s.wav")
    ap.add_argument("--duration", type=float, default=55.0)
    ap.add_argument("--audio-clients", type=int, default=0,
                    help="how many clients also pull a REST snapshot each second")
    ap.add_argument("--reconnect", action="store_true",
                    help="drop and rejoin a third of the class mid-lecture")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
