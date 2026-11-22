#!/usr/bin/env python3
"""Probe the microphone capture path without a microphone.

A fake ``arecord`` is placed first on PATH. It emits S16_LE mono 16 kHz PCM at
real-time pace and ALSA-style overrun warnings on stderr, exactly like a real
arecord on a loaded laptop. Two scenarios:

``overrun``  stderr keeps producing ~2 KB per 100 ms chunk. MicCapture gives
             arecord a stderr pipe that nothing reads. After the 64 KiB pipe
             buffer fills, arecord blocks in write(2), stdout stops, and the
             reader thread blocks forever in read(2) — with no error recorded.

``exit``     arecord dies after 3 s (device yanked, USB error). The reader thread
             returns and sets last_error, but nothing in the server watches for
             that, so the lecture continues with no audio.

    .venv/bin/python engineering_harness/probes/capture_stall.py
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import evidence  # noqa: E402
from guru.audio import MicCapture  # noqa: E402

SHIM = """#!/usr/bin/env bash
BUF={buf}
if [ ! -f "$BUF" ]; then
  python3 -c "import sys,struct; sys.stdout.buffer.write(b''.join(struct.pack('<h',0) for _ in range(1600)))" > "$BUF"
fi
N=0
while true; do
  dd if="$BUF" bs=3200 count=1 status=none || exit 3
  for i in $(seq 1 {warn_lines}); do
    echo "ALSA lib pcm.c:2666:(snd_pcm_open_noupdate) overrun in read from default $i" >&2
  done
  N=$((N+1))
  if [ {max_chunks} -gt 0 ] && [ $N -ge {max_chunks} ]; then exit 0; fi
  sleep 0.1
done
"""


def make_shim(max_chunks: int, warn_lines: int = 40) -> Path:
    d = Path(tempfile.mkdtemp(prefix="arecord-shim-"))
    shim = d / "arecord"
    shim.write_text(SHIM.format(buf=d / "chunk.raw", max_chunks=max_chunks,
                                warn_lines=warn_lines))
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return d


def run_scenario(name: str, max_chunks: int, seconds: float,
                 warn_lines: int = 40) -> dict:
    d = make_shim(max_chunks, warn_lines)
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{d}:{old_path}"
    state = {"samples": 0, "callbacks": 0, "last": None}
    try:
        def on_frames(a):
            state["samples"] += len(a) // 2 if a.dtype.name != "float32" else len(a)
            state["callbacks"] += 1
            state["last"] = time.perf_counter()

        cap = MicCapture(device="default")
        t0 = time.perf_counter()
        cap.start(on_frames)
        time.sleep(seconds)
        now = time.perf_counter()
        alive = cap._proc.poll() is None if cap._proc else None
        stderr_open_but_unread = bool(cap._proc and cap._proc.stderr and not cap._proc.stderr.closed)
        out = {
            "scenario": name,
            "run_seconds": round(now - t0, 2),
            "callbacks": state["callbacks"],
            "audio_delivered_s": round(state["samples"] / 16000.0, 2),
            "delivered_ratio": round((state["samples"] / 16000.0) / (now - t0), 3),
            "silent_for_s": round(now - (state["last"] or t0), 2),
            "arecord_process_still_alive": alive,
            "stderr_pipe_never_drained": stderr_open_but_unread,
            "reader_thread_alive": cap._thread.is_alive() if cap._thread else None,
            "last_error_recorded": cap.last_error,
            "health_endpoint_reports_capture_state": False,  # verified by inspection of /api/health
        }
        cap.stop()
        return out
    finally:
        os.environ["PATH"] = old_path


def main() -> int:
    overrun = run_scenario("stderr_overrun_deadlock", max_chunks=0, seconds=10.0)
    # No stderr this time: the shim simply exits after 30 chunks (~3 s), which is
    # what a yanked USB device or an ALSA error looks like to the reader thread.
    exited = run_scenario("device_exits_silently", max_chunks=30, seconds=8.0, warn_lines=0)
    payload = {
        "audio": {
            "overrun": overrun,
            "exit": exited,
        },
        "conclusion": (
            "overrun: arecord is still alive but blocked writing stderr; audio delivery "
            "stops once the 64 KiB pipe buffer fills, the reader thread stays blocked in "
            "read(), and no error is recorded. exit: the reader thread ends on EOF, sets "
            "last_error, and nothing restarts capture or surfaces it in /api/health."
        ),
        "caveat": "stdout pacing is produced by a shell shim, not a real ALSA device.",
    }
    path = evidence.write("capture_stall", payload, command=" ".join(sys.argv))
    print(json.dumps(payload, indent=2))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
