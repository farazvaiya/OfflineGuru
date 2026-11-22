#!/usr/bin/env python3
"""Run a project benchmark and store it as stamped harness evidence.

The project already ships the benchmarks; this wrapper exists so their output is
normalised, digest-stamped and comparable between baseline and final runs. It does
not re-implement any measurement.

    # end-to-end latency + CPU + RSS through the real models
    .venv/bin/python engineering_harness/run_bench.py latency --label baseline

    # English->Bangla quality against the author-written reference set
    .venv/bin/python engineering_harness/run_bench.py translation --label baseline

    # 30-student loopback load test (server must already be listening)
    .venv/bin/python engineering_harness/run_bench.py loadtest --label final --clients 30

A --label of "baseline" also writes the artifact to baselines/, which is the only
place a gate will read a pre-optimisation number from. Baseline files are never
overwritten by a non-baseline run.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HARNESS = Path(__file__).resolve().parent
REPO = HARNESS.parent
sys.path.insert(0, str(HARNESS))

import evidence  # noqa: E402


def _run(cmd: list[str], timeout: int = 1800) -> tuple[int, str]:
    print("$", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout)
    tail = (proc.stdout or "").strip().splitlines()[-6:]
    for line in tail:
        print("   ", line)
    if proc.returncode != 0:
        print("   stderr:", (proc.stderr or "").strip()[-800:])
    return proc.returncode, proc.stdout or ""


def _store(name: str, label: str, payload: dict, cmd: list[str]) -> Path:
    """Write artifacts/<name>_final.json (or _<label>) and, for a baseline run,
    baselines/<name>_baseline.json — the only file gates read pre-optimisation numbers from."""
    payload = dict(payload)
    payload["label"] = label
    artifact_name = f"{name}_final" if label == "final" else f"{name}_{label}"
    path = evidence.write(artifact_name, payload, command=" ".join(cmd))
    if label == "baseline":
        base = HARNESS / "baselines" / f"{name}_baseline.json"
        base.parent.mkdir(parents=True, exist_ok=True)
        base.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"baseline -> {base}")
    return path


def latency(args: argparse.Namespace) -> int:
    out = Path(tempfile.mkdtemp()) / "latency.json"
    cmd = [sys.executable, "scripts/bench_latency.py", "--wav", args.wav, "--out", str(out)]
    code, _ = _run(cmd)
    if not out.is_file():
        print("bench_latency produced no output")
        return 4
    payload = json.loads(out.read_text())
    payload["exit_code"] = code
    _store("latency", args.label, payload, cmd)
    stages = payload.get("stages", {})
    print(json.dumps({k: stages.get(k, {}).get("p95") for k in stages}, indent=2))
    return 0 if code == 0 else code


def translation(args: argparse.Namespace) -> int:
    out = Path(tempfile.mkdtemp()) / "translation.json"
    md = Path(tempfile.mkdtemp()) / "translation.md"
    cmd = [sys.executable, "scripts/eval_translation_quality.py",
           "--out", str(out), "--md", str(md)]
    if args.backend:
        cmd += ["--backend", args.backend]
    code, _ = _run(cmd)
    if not out.is_file():
        print("translation eval produced no output")
        return 4
    payload = json.loads(out.read_text(encoding="utf-8"))
    payload["exit_code"] = code
    payload.pop("rows", None)  # rows stay in the project report; keep evidence compact
    _store("translation_quality", args.label, payload, cmd)
    print(f"chrF mean {payload.get('chrf_mean')} over {payload.get('entries')} entries")
    return 0 if code == 0 else code


def loadtest(args: argparse.Namespace) -> int:
    # The project writes data/eval/loadtest_<N>.json; copy before/after so a later
    # run cannot silently replace the evidence a gate already read.
    target = REPO / "data" / "eval" / f"loadtest_{args.clients}.json"
    before = target.stat().st_mtime if target.is_file() else 0.0
    cmd = [sys.executable, "scripts/loadtest.py", "--clients", str(args.clients),
           "--wav", args.wav, "--base", args.base, "--duration", str(args.duration)]
    if args.audio_clients:
        cmd += ["--audio-clients", str(args.audio_clients)]
    if args.reconnect:
        cmd.append("--reconnect")
    code, _ = _run(cmd, timeout=900)
    if not target.is_file() or target.stat().st_mtime <= before:
        print("loadtest produced no new result file")
        return 4
    payload = json.loads(target.read_text())
    payload["exit_code"] = code
    payload["command_line"] = " ".join(cmd)
    if args.clients == 30:
        _store("loadtest_30", args.label, payload, cmd)
    else:
        _store(f"loadtest_{args.clients}", args.label, payload, cmd)
    print(json.dumps({k: payload.get(k) for k in
                      ("clients", "segments_published", "received_min", "socket_errors",
                       "bus_drops")}, indent=2))
    return 0 if code == 0 and int(payload.get("socket_errors", 0)) == 0 else code or 5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bench", choices=["latency", "translation", "loadtest"])
    ap.add_argument("--label", default="final")
    ap.add_argument("--wav", default="data/demo/lecture_45s.wav")
    ap.add_argument("--backend", default=None)
    ap.add_argument("--clients", type=int, default=30)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--duration", type=float, default=55.0)
    ap.add_argument("--audio-clients", type=int, default=0)
    ap.add_argument("--reconnect", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    code = {"latency": latency, "translation": translation, "loadtest": loadtest}[args.bench](args)
    print(f"[{args.bench}] exit={code} elapsed={time.time()-t0:.0f}s")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
