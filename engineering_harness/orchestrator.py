#!/usr/bin/env python3
"""Offline Guru engineering harness — orchestrator.

This is the executable core of the harness described in engineering_harness/README.md.
It owns four things and nothing else:

1. a persistent phase state machine (state/workflow.json) with atomic writes,
2. task, experiment and decision journals (state/*.json, state/*.jsonl),
3. phase exit gates that are evaluated against machine-readable evidence
   (config/acceptance_criteria.yaml -> checks.py),
4. source checkpoints and rollback (checkpoints/).

Agent "labour" (research, code changes, benchmark runs) is performed by the
executing agent and recorded through this CLI. The orchestrator never reports a
phase as passed on its own: a gate is computed from artifacts on disk, and an
approval must be signed by a named reviewer in state/signoffs.json.

Usage
-----
    python engineering_harness/orchestrator.py init
    python engineering_harness/orchestrator.py status
    python engineering_harness/orchestrator.py hw
    python engineering_harness/orchestrator.py task add "..." --owner asr-agent --phase 1
    python engineering_harness/orchestrator.py task done T3
    python engineering_harness/orchestrator.py checkpoint -m "before P0 fixes"
    python engineering_harness/orchestrator.py gate phase1
    python engineering_harness/orchestrator.py approve phase1 --agent evaluator-agent --note "..."
    python engineering_harness/orchestrator.py record-suite
    python engineering_harness/orchestrator.py run
    python engineering_harness/orchestrator.py rollback <checkpoint_id> --yes
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HARNESS = Path(__file__).resolve().parent
REPO = HARNESS.parent
STATE = HARNESS / "state"
CFG = HARNESS / "config"

sys.path.insert(0, str(HARNESS))
import evidence  # noqa: E402  (single source of truth for source digests)

SOURCE_PATHS = evidence.SOURCE_PATHS
source_digest = evidence.source_digest
source_mtime = evidence.source_mtime

MAX_ATTEMPTS_PER_ISSUE = 5
MAX_INVESTIGATION_CYCLES = 3

STATES = [
    "INITIALIZING",
    "BASELINE_AUDIT",
    "PHASE1_RUNNING",
    "PHASE1_BLOCKED",
    "PHASE1_APPROVED",
    "PHASE2_RUNNING",
    "PHASE2_REPAIRING",
    "PHASE2_BLOCKED",
    "PHASE2_APPROVED",
    "PHASE3_RUNNING",
    "PHASE3_OPTIMIZING",
    "PHASE3_BLOCKED",
    "PHASE3_APPROVED",
    "RESEARCH_ROLLBACK",
    "DEVELOPMENT_ROLLBACK",
    "FINAL_VALIDATION",
    "COMPLETED",
    "HALTED",
]

TRANSITIONS: dict[str, list[str]] = {
    "INITIALIZING": ["BASELINE_AUDIT", "HALTED"],
    "BASELINE_AUDIT": ["PHASE1_RUNNING", "HALTED"],
    "PHASE1_RUNNING": ["PHASE1_APPROVED", "PHASE1_BLOCKED", "HALTED"],
    "PHASE1_BLOCKED": ["PHASE1_RUNNING", "HALTED"],
    "PHASE1_APPROVED": ["PHASE2_RUNNING", "RESEARCH_ROLLBACK", "HALTED"],
    "PHASE2_RUNNING": ["PHASE2_REPAIRING", "PHASE2_BLOCKED", "PHASE2_APPROVED", "RESEARCH_ROLLBACK", "HALTED"],
    "PHASE2_REPAIRING": ["PHASE2_RUNNING", "PHASE2_BLOCKED", "RESEARCH_ROLLBACK", "HALTED"],
    "PHASE2_BLOCKED": ["PHASE2_RUNNING", "RESEARCH_ROLLBACK", "HALTED"],
    "PHASE2_APPROVED": ["PHASE3_RUNNING", "RESEARCH_ROLLBACK", "DEVELOPMENT_ROLLBACK", "HALTED"],
    "PHASE3_RUNNING": ["PHASE3_OPTIMIZING", "PHASE3_BLOCKED", "PHASE3_APPROVED", "RESEARCH_ROLLBACK",
                       "DEVELOPMENT_ROLLBACK", "HALTED"],
    "PHASE3_OPTIMIZING": ["PHASE3_RUNNING", "PHASE3_BLOCKED", "RESEARCH_ROLLBACK", "DEVELOPMENT_ROLLBACK", "HALTED"],
    "PHASE3_BLOCKED": ["PHASE3_RUNNING", "RESEARCH_ROLLBACK", "DEVELOPMENT_ROLLBACK", "HALTED"],
    "PHASE3_APPROVED": ["FINAL_VALIDATION", "DEVELOPMENT_ROLLBACK", "HALTED"],
    "RESEARCH_ROLLBACK": ["PHASE1_RUNNING", "HALTED"],
    "DEVELOPMENT_ROLLBACK": ["PHASE2_RUNNING", "HALTED"],
    "FINAL_VALIDATION": ["COMPLETED", "PHASE3_RUNNING", "HALTED"],
    "COMPLETED": [],
    "HALTED": [],  # resume is handled explicitly by `resume`
}

PHASE_OF_STATE = {
    "BASELINE_AUDIT": 1,
    "PHASE1_RUNNING": 1,
    "PHASE1_BLOCKED": 1,
    "PHASE1_APPROVED": 1,
    "PHASE2_RUNNING": 2,
    "PHASE2_REPAIRING": 2,
    "PHASE2_BLOCKED": 2,
    "PHASE2_APPROVED": 2,
    "PHASE3_RUNNING": 3,
    "PHASE3_OPTIMIZING": 3,
    "PHASE3_BLOCKED": 3,
    "PHASE3_APPROVED": 3,
    "FINAL_VALIDATION": 3,
}

GATE_FOR_PHASE = {1: "phase1", 2: "phase2", 3: "phase3"}
APPROVED_FOR_PHASE = {1: "PHASE1_APPROVED", 2: "PHASE2_APPROVED", 3: "PHASE3_APPROVED"}
RUNNING_FOR_PHASE = {1: "PHASE1_RUNNING", 2: "PHASE2_RUNNING", 3: "PHASE3_RUNNING"}
BLOCKED_FOR_PHASE = {1: "PHASE1_BLOCKED", 2: "PHASE2_BLOCKED", 3: "PHASE3_BLOCKED"}


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        raise SystemExit(f"corrupt JSON: {path}") from None


def write_json_atomic(path: Path, payload: Any) -> None:
    """Write via temp file + os.replace so a crash cannot truncate state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    os.replace(tmp, path)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Workflow state machine
# ---------------------------------------------------------------------------
class Workflow:
    def __init__(self) -> None:
        self.path = STATE / "workflow.json"
        self.data = read_json(self.path)

    @property
    def exists(self) -> bool:
        return self.data is not None

    def refresh(self) -> None:
        """Re-read state from disk before a read-modify-write.

        The runner holds a Workflow instance across many steps while gate
        evaluation writes its own copy of state/workflow.json. Without this, a
        later transition would write back a stale snapshot and silently erase the
        gate result it had just computed.
        """
        latest = read_json(self.path)
        if latest is not None:
            self.data = latest

    def init(self) -> dict[str, Any]:
        if self.exists:
            return self.data
        self.data = {
            "schema": 1,
            "state": "INITIALIZING",
            "phase": 0,
            "retry_count": 0,
            "attempts": {},          # issue_id -> attempts
            "investigation_cycles": {},
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "transitions": [],
            "checkpoints": [],
            "gate_results": {},
            "invalidation": [],
            "notes": [],
        }
        write_json_atomic(self.path, self.data)
        append_jsonl(STATE / "decisions.jsonl", {
            "ts": now_iso(), "kind": "init", "agent": "orchestrator", "detail": "harness initialised",
        })
        return self.data

    def transition(self, new_state: str, reason: str, agent: str,
                   evidence: list[str] | None = None,
                   retry_count: int | None = None) -> dict[str, Any]:
        if not self.exists:
            raise SystemExit("workflow not initialised: run `orchestrator.py init`")
        self.refresh()
        old = self.data["state"]
        allowed = TRANSITIONS.get(old, [])
        if new_state not in allowed and new_state != old:
            raise SystemExit(
                f"illegal transition {old} -> {new_state}; allowed: {allowed or 'none'}"
            )
        record = {
            "ts": now_iso(),
            "from": old,
            "to": new_state,
            "reason": reason,
            "agent": agent,
            "evidence": evidence or [],
            "source_digest": source_digest(),
            "retry_count": self.data.get("retry_count", 0) if retry_count is None else retry_count,
        }
        self.data["transitions"].append(record)
        self.data["state"] = new_state
        self.data["phase"] = PHASE_OF_STATE.get(new_state, self.data.get("phase", 0))
        self.data["updated_at"] = record["ts"]
        if retry_count is not None:
            self.data["retry_count"] = retry_count
        write_json_atomic(self.path, self.data)
        append_jsonl(STATE / "decisions.jsonl", {"kind": "transition", **record})
        return record

    def bump_attempt(self, issue_id: str) -> int:
        self.refresh()
        self.data.setdefault("attempts", {})
        self.data["attempts"][issue_id] = self.data["attempts"].get(issue_id, 0) + 1
        write_json_atomic(self.path, self.data)
        return self.data["attempts"][issue_id]

    def bump_cycle(self, issue_id: str) -> int:
        self.refresh()
        self.data.setdefault("investigation_cycles", {})
        self.data["investigation_cycles"][issue_id] = (
            self.data["investigation_cycles"].get(issue_id, 0) + 1
        )
        write_json_atomic(self.path, self.data)
        return self.data["investigation_cycles"][issue_id]

    def invalidate_downstream(self, from_phase: int, reason: str, agent: str) -> list[dict[str, Any]]:
        """A return to an earlier phase voids every later approval and gate result."""
        self.refresh()
        voided: list[dict[str, Any]] = []
        signoffs_path = STATE / "signoffs.json"
        signoffs = read_json(signoffs_path, {"signoffs": []}) or {"signoffs": []}
        for s in signoffs.get("signoffs", []):
            if int(str(s.get("phase", "0"))) >= from_phase and s.get("status") == "approved":
                s["status"] = "invalidated"
                s["invalidated_at"] = now_iso()
                s["invalidated_reason"] = reason
                voided.append({"kind": "signoff", "phase": s.get("phase"), "agent": s.get("agent")})
        write_json_atomic(signoffs_path, signoffs)
        for phase in (1, 2, 3):
            if phase < from_phase:
                continue
            key = GATE_FOR_PHASE[phase]
            gate = self.data.get("gate_results", {}).get(key)
            if gate and gate.get("status") != "INVALIDATED":
                gate["status"] = "INVALIDATED"
                gate["invalidated_at"] = now_iso()
                gate["invalidated_reason"] = reason
                voided.append({"kind": "gate", "phase": phase})
        self.data.setdefault("invalidation", []).append({
            "ts": now_iso(), "from_phase": from_phase, "reason": reason, "agent": agent,
            "voided": voided,
        })
        write_json_atomic(self.path, self.data)
        append_jsonl(STATE / "decisions.jsonl", {
            "ts": now_iso(), "kind": "invalidation", "agent": agent,
            "from_phase": from_phase, "reason": reason, "voided": voided,
        })
        return voided

    def status(self) -> int:
        if not self.exists:
            print("workflow: not initialised")
            return 3
        d = self.data
        print(f"state            : {d['state']}")
        print(f"phase            : {d.get('phase')}")
        print(f"retry_count      : {d.get('retry_count')}")
        print(f"source_digest    : {source_digest()}")
        print(f"updated_at       : {d.get('updated_at')}")
        gates = d.get("gate_results", {})
        for key in ("phase1", "phase2", "phase3"):
            g = gates.get(key)
            print(f"gate {key:8s}   : {g['status'] if g else 'not evaluated'}")
        att = d.get("attempts") or {}
        if att:
            print("attempts per issue:")
            for k, v in sorted(att.items()):
                limit = "  (LIMIT REACHED)" if v >= MAX_ATTEMPTS_PER_ISSUE else ""
                print(f"  {k}: {v}{limit}")
        tasks = read_json(STATE / "agent_tasks.json", {"tasks": []}) or {"tasks": []}
        open_tasks = [t for t in tasks["tasks"] if t.get("status") != "done"]
        print(f"open tasks       : {len(open_tasks)}")
        for t in open_tasks[:10]:
            print(f"  {t['id']} [{t.get('owner','?')}] {t['title']}")
        return 0


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
def task_cmd(args: argparse.Namespace) -> int:
    path = STATE / "agent_tasks.json"
    doc = read_json(path, {"tasks": [], "next_id": 1}) or {"tasks": [], "next_id": 1}
    if args.action == "add":
        tid = f"T{doc['next_id']}"
        doc["next_id"] += 1
        doc["tasks"].append({
            "id": tid, "title": args.title, "owner": args.owner,
            "phase": args.phase, "status": "open", "created_at": now_iso(),
            "finding": args.finding, "notes": [],
        })
        write_json_atomic(path, doc)
        print(tid)
    elif args.action == "done":
        for t in doc["tasks"]:
            if t["id"] == args.task_id:
                t["status"] = "done"
                t["completed_at"] = now_iso()
                if args.note:
                    t.setdefault("notes", []).append(args.note)
                write_json_atomic(path, doc)
                print(f"{args.task_id} done")
                return 0
        raise SystemExit(f"no such task {args.task_id}")
    elif args.action == "note":
        for t in doc["tasks"]:
            if t["id"] == args.task_id:
                t.setdefault("notes", []).append(args.note or "")
                write_json_atomic(path, doc)
                print(f"{args.task_id} noted")
                return 0
        raise SystemExit(f"no such task {args.task_id}")
    else:
        for t in doc["tasks"]:
            mark = "x" if t["status"] == "done" else " "
            print(f"[{mark}] {t['id']} phase{t.get('phase')} {t.get('owner')}: {t['title']}")
    return 0


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------
def experiment_cmd(args: argparse.Namespace) -> int:
    exp_dir = HARNESS / "experiments"
    exp_dir.mkdir(parents=True, exist_ok=True)
    if args.action == "new":
        existing = sorted(exp_dir.glob("EXP-*.json"))
        num = len(existing) + 1
        eid = f"EXP-{num:03d}"
        doc = {
            "id": eid,
            "created_at": now_iso(),
            "phase": args.phase,
            "hypothesis": args.hypothesis,
            "finding": args.finding,
            "baseline_digest": source_digest(),
            "config": args.config,
            "changed_components": args.components or [],
            "dataset": args.dataset,
            "command": args.command,
            "raw_results": None,
            "comparison": None,
            "decision": None,
            "status": "running",
        }
        write_json_atomic(exp_dir / f"{eid}.json", doc)
        append_jsonl(STATE / "experiment_history.jsonl", {"ts": now_iso(), "kind": "start", **doc})
        print(eid)
        return 0
    if args.action == "finish":
        path = exp_dir / f"{args.exp_id}.json"
        doc = read_json(path)
        if doc is None:
            raise SystemExit(f"no such experiment {args.exp_id}")
        doc["status"] = args.decision or "completed"
        doc["decision"] = args.decision
        doc["comparison"] = args.comparison
        doc["result_summary"] = args.result
        doc["raw_results"] = args.raw
        doc["finished_at"] = now_iso()
        doc["source_digest"] = source_digest()
        write_json_atomic(path, doc)
        append_jsonl(STATE / "experiment_history.jsonl", {
            "ts": now_iso(), "kind": "finish", "id": doc["id"],
            "decision": doc["decision"], "comparison": doc["comparison"],
        })
        print(f"{doc['id']} -> {doc['status']}")
        return 0
    if args.action == "list":
        for p in sorted(exp_dir.glob("EXP-*.json")):
            d = read_json(p)
            print(f"{d['id']} {d.get('status'):10s} phase{d.get('phase')} "
                  f"{d.get('decision') or '-'}  {d.get('hypothesis','')[:70]}")
        return 0
    raise SystemExit("unknown experiment action")


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------
def checkpoint_create(label: str, kind: str = "manual") -> str:
    ck_root = HARNESS / "checkpoints"
    ck_root.mkdir(parents=True, exist_ok=True)
    cid = time.strftime("%Y%m%d-%H%M%S") + "-" + hashlib.sha256(
        (label + str(time.time())).encode()).hexdigest()[:6]
    dest = ck_root / cid
    dest.mkdir(parents=True, exist_ok=True)
    manifest = {"id": cid, "label": label, "kind": kind, "created_at": now_iso(),
                "source_digest": source_digest(), "files": {}}
    with tarfile.open(dest / "source.tar.gz", "w:gz") as tar:
        for rel in SOURCE_PATHS:
            p = REPO / rel
            if not p.exists():
                continue
            tar.add(p, arcname=rel, filter=_tar_filter)
            files = [p] if p.is_file() else p.rglob("*")
            for f in files:
                if f.is_file() and "__pycache__" not in f.parts:
                    manifest["files"][str(f.relative_to(REPO))] = sha256_file(f)
    write_json_atomic(dest / "manifest.json", manifest)
    wf = Workflow()
    if wf.exists:
        wf.refresh()
        wf.data.setdefault("checkpoints", []).append({
            "id": cid, "label": label, "kind": kind, "created_at": manifest["created_at"],
            "source_digest": manifest["source_digest"],
        })
        write_json_atomic(wf.path, wf.data)
    append_jsonl(STATE / "decisions.jsonl", {
        "ts": now_iso(), "kind": "checkpoint", "agent": "orchestrator",
        "id": cid, "label": label, "source_digest": manifest["source_digest"],
    })
    print(cid)
    return cid


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if "__pycache__" in info.name or info.name.endswith((".pyc", ".pyo")):
        return None
    return info


def checkpoint_restore(cid: str, yes: bool) -> int:
    dest = HARNESS / "checkpoints" / cid
    if not (dest / "source.tar.gz").is_file():
        raise SystemExit(f"no such checkpoint: {cid}")
    if not yes:
        raise SystemExit("restore overwrites source files; re-run with --yes")
    backup = checkpoint_create(f"pre-restore-of-{cid}", kind="auto")
    print(f"safety checkpoint: {backup}")
    with tarfile.open(dest / "source.tar.gz", "r:gz") as tar:
        tar.extractall(REPO, filter="data")
    print(f"restored {cid} over {REPO}")
    append_jsonl(STATE / "decisions.jsonl", {
        "ts": now_iso(), "kind": "rollback", "agent": "orchestrator",
        "restored": cid, "safety_checkpoint": backup,
    })
    return 0


# ---------------------------------------------------------------------------
# Test suite recording
# ---------------------------------------------------------------------------
def record_suite(label: str = "final") -> int:
    """Run pytest with JUnit XML so the gate can parse real counts."""
    art = HARNESS / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    xml = art / f"pytest_{label}.xml"
    log = art / f"pytest_{label}.log"
    cmd = [sys.executable, "-m", "pytest", "-q", f"--junitxml={xml}"]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    elapsed = time.time() - t0
    log.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr)
    counts = _parse_junit(xml)
    payload = {
        "created_at": now_iso(),
        "command": " ".join(cmd),
        "exit_code": proc.returncode,
        "elapsed_s": round(elapsed, 1),
        "source_digest": source_digest(),
        "source_mtime": source_mtime(),
        "_evidence": {
            "created_at": now_iso(),
            "source_digest": source_digest(),
            "source_mtime": source_mtime(),
            "command": " ".join(cmd),
            "host": platform.node(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        **counts,
        "stdout_tail": proc.stdout.strip().splitlines()[-3:],
        "xml": str(xml.relative_to(HARNESS)),
        "log": str(log.relative_to(HARNESS)),
    }
    # Append-only history plus a stable "latest" pointer.
    hist = art / f"pytest_{label}_{time.strftime('%H%M%S')}.json"
    write_json_atomic(hist, payload)
    write_json_atomic(art / f"pytest_{label}.json", payload)
    print(json.dumps({k: payload[k] for k in ("exit_code", "tests", "failures", "errors", "skipped")}))
    return 0 if proc.returncode == 0 else 4


def _parse_junit(xml: Path) -> dict[str, int]:
    if not xml.is_file():
        return {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "parse_error": 1}
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(xml).getroot()
    except ET.ParseError:
        return {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "parse_error": 1}
    suites = [root] if root.tag == "testsuite" else list(root)
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "parse_error": 0}
    for s in suites:
        if s.tag != "testsuite":
            continue
        totals["tests"] += int(s.get("tests", 0))
        totals["failures"] += int(s.get("failures", 0))
        totals["errors"] += int(s.get("errors", 0))
        totals["skipped"] += int(s.get("skipped", 0))
    return totals


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------
def evaluate_gate(phase_key: str) -> dict[str, Any]:
    sys.path.insert(0, str(HARNESS))
    import checks  # noqa: PLC0415  (local import keeps CLI startup cheap)

    criteria_doc = _load_yaml(CFG / "acceptance_criteria.yaml")
    if phase_key not in criteria_doc:
        raise SystemExit(f"no criteria for {phase_key}")
    phase_cfg = criteria_doc[phase_key]
    results = []
    for crit in phase_cfg["criteria"]:
        fn = getattr(checks, crit["check"], None)
        if fn is None:
            results.append({
                "id": crit["id"], "check": crit["check"], "status": "BLOCKED",
                "description": crit["description"], "measured": None,
                "evidence": [], "notes": ["check function not implemented"],
            })
            continue
        try:
            out = fn()
        except Exception as exc:  # a crashing check is not a pass
            out = {"status": "BLOCKED", "measured": None,
                   "notes": [f"check raised {type(exc).__name__}: {exc}"], "evidence": []}
        results.append({
            "id": crit["id"],
            "check": crit["check"],
            "description": crit["description"],
            "status": out.get("status", "BLOCKED"),
            "measured": out.get("measured"),
            "threshold": out.get("threshold"),
            "evidence": out.get("evidence", []),
            "notes": out.get("notes", []),
        })
    statuses = [r["status"] for r in results]
    if "FAIL" in statuses:
        status = "FAIL"
    elif "BLOCKED" in statuses:
        status = "BLOCKED"
    else:
        status = "PASS"
    gate = {
        "phase": phase_key,
        "name": phase_cfg.get("name"),
        "evaluated_at": now_iso(),
        "source_digest": source_digest(),
        "status": status,
        "criteria": results,
        "not_testable_locally": criteria_doc.get("not_testable_locally", []),
    }
    wf = Workflow()
    if wf.exists:
        wf.refresh()
        wf.data.setdefault("gate_results", {})[phase_key] = {
            "status": status, "evaluated_at": gate["evaluated_at"],
            "source_digest": gate["source_digest"],
        }
        write_json_atomic(wf.path, wf.data)
    out_path = HARNESS / "reports" / f"GATE_{phase_key.upper()}.json"
    write_json_atomic(out_path, gate)
    append_jsonl(STATE / "decisions.jsonl", {
        "ts": now_iso(), "kind": "gate", "agent": "orchestrator",
        "phase": phase_key, "status": status,
        "failed": [r["id"] for r in results if r["status"] == "FAIL"],
        "blocked": [r["id"] for r in results if r["status"] == "BLOCKED"],
    })
    _print_gate(gate)
    return gate


def _load_yaml(path: Path) -> Any:
    import yaml  # available in the project venv

    return yaml.safe_load(path.read_text())


def _print_gate(gate: dict[str, Any]) -> None:
    print(f"\n=== GATE {gate['phase']} ({gate['name']}) -> {gate['status']} ===")
    for r in gate["criteria"]:
        mark = {"PASS": "PASS ", "FAIL": "FAIL ", "BLOCKED": "BLOCK"}.get(r["status"], "?????")
        print(f"[{mark}] {r['id']} {r['check']}")
        if r["measured"] is not None:
            print(f"          measured: {json.dumps(r['measured'], default=str)[:300]}")
        for n in r["notes"]:
            print(f"          note: {n}")
    print(f"wrote reports/GATE_{gate['phase'].upper()}.json")


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------
def approve(phase: int, agent: str, note: str, status: str = "approved") -> int:
    path = STATE / "signoffs.json"
    doc = read_json(path, {"signoffs": []}) or {"signoffs": []}
    gate_key = GATE_FOR_PHASE[phase]
    wf = Workflow()
    gate = (wf.data or {}).get("gate_results", {}).get(gate_key, {})
    doc["signoffs"].append({
        "phase": phase,
        "agent": agent,
        "status": status,
        "note": note,
        "signed_at": now_iso(),
        "gate_status_at_signing": gate.get("status"),
        "source_digest": source_digest(),
    })
    write_json_atomic(path, doc)
    append_jsonl(STATE / "decisions.jsonl", {
        "ts": now_iso(), "kind": "signoff", "agent": agent,
        "phase": phase, "status": status, "note": note,
    })
    print(f"phase {phase}: {agent} -> {status}")
    return 0


# ---------------------------------------------------------------------------
# Hardware capture
# ---------------------------------------------------------------------------
def capture_hardware() -> int:
    info: dict[str, Any] = {
        "captured_at": now_iso(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": sys.version.split()[0],
        "cpu_count_logical": os.cpu_count(),
    }
    try:
        info["cpu_model"] = next(
            (line.split(":", 1)[1].strip()
             for line in Path("/proc/cpuinfo").read_text().splitlines()
             if line.startswith("model name")), "unknown")
    except OSError:
        info["cpu_model"] = "unknown"
    try:
        import psutil  # noqa: PLC0415

        vm = psutil.virtual_memory()
        info["ram_total_mb"] = round(vm.total / 1e6)
        info["ram_available_mb"] = round(vm.available / 1e6)
        sm = psutil.swap_memory()
        info["swap_total_mb"] = round(sm.total / 1e6)
        info["swap_used_mb"] = round(sm.used / 1e6)
    except ImportError:
        pass
    try:
        load = Path("/proc/loadavg").read_text().split()[:3]
        info["loadavg_1_5_15"] = load
    except OSError:
        pass
    write_json_atomic(HARNESS / "artifacts" / "hardware.json", info)
    print(json.dumps(info, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Autonomous runner
# ---------------------------------------------------------------------------
def run_loop(max_steps: int = 12) -> int:
    wf = Workflow()
    if not wf.exists:
        wf.init()
    steps = 0
    while steps < max_steps:
        steps += 1
        state = wf.data["state"]
        print(f"\n--- step {steps}: state={state} ---")
        if state == "COMPLETED":
            print("workflow complete")
            return 0
        if state == "HALTED":
            print("halted; use `resume` after resolving the blocker")
            return 3
        if state == "INITIALIZING":
            wf.transition("BASELINE_AUDIT", "repository audit starts", "orchestrator")
            continue
        if state in ("BASELINE_AUDIT",):
            gate = evaluate_gate("phase1")
            if gate["status"] == "PASS":
                wf.transition("PHASE1_RUNNING", "baseline audit evidence complete", "orchestrator")
            else:
                print("baseline evidence incomplete; phase 1 remains in progress")
                wf.data["state"] = "PHASE1_RUNNING"
                write_json_atomic(wf.path, wf.data)
            continue
        if state in ("PHASE1_RUNNING", "PHASE1_BLOCKED"):
            gate = evaluate_gate("phase1")
            if gate["status"] == "PASS":
                if _has_approval(1, "evaluator-agent"):
                    wf.transition("PHASE1_APPROVED", "gate PASS + evaluator approval", "orchestrator")
                else:
                    print("gate PASS; awaiting evaluator signature")
                    wf.transition("PHASE1_BLOCKED", "gate PASS but no evaluator signoff",
                                  "orchestrator")
                    return 3
            else:
                if state != "PHASE1_BLOCKED":
                    wf.transition("PHASE1_BLOCKED",
                                  f"gate {gate['status']}: work required", "orchestrator")
                return 3
            continue
        if state == "PHASE1_APPROVED":
            wf.transition("PHASE2_RUNNING", "phase 1 approved", "orchestrator")
            continue
        if state in ("PHASE2_RUNNING", "PHASE2_REPAIRING", "PHASE2_BLOCKED"):
            gate = evaluate_gate("phase2")
            if gate["status"] == "PASS":
                if _has_approval(2, "evaluator-agent"):
                    wf.transition("PHASE2_APPROVED", "gate PASS + evaluator approval", "orchestrator")
                else:
                    print("gate PASS; awaiting evaluator signature")
                    if state != "PHASE2_BLOCKED":
                        wf.transition("PHASE2_BLOCKED", "gate PASS but no evaluator signoff",
                                      "orchestrator")
                    return 3
            else:
                if state != "PHASE2_BLOCKED":
                    wf.transition("PHASE2_BLOCKED", f"gate {gate['status']}: repair required",
                                  "orchestrator")
                return 3
            continue
        if state == "PHASE2_APPROVED":
            wf.transition("PHASE3_RUNNING", "phase 2 approved", "orchestrator")
            continue
        if state in ("PHASE3_RUNNING", "PHASE3_OPTIMIZING", "PHASE3_BLOCKED"):
            gate = evaluate_gate("phase3")
            if gate["status"] == "PASS":
                if _has_approval(3, "evaluator-agent"):
                    wf.transition("PHASE3_APPROVED", "gate PASS + evaluator approval", "orchestrator")
                else:
                    print("gate PASS; awaiting evaluator signature")
                    if state != "PHASE3_BLOCKED":
                        wf.transition("PHASE3_BLOCKED", "gate PASS but no evaluator signoff",
                                      "orchestrator")
                    return 3
            else:
                if state != "PHASE3_BLOCKED":
                    wf.transition("PHASE3_BLOCKED", f"gate {gate['status']}: validation required",
                                  "orchestrator")
                return 3
            continue
        if state == "PHASE3_APPROVED":
            wf.transition("FINAL_VALIDATION", "phase 3 approved", "orchestrator")
            continue
        if state == "FINAL_VALIDATION":
            gate = evaluate_gate("phase3")
            wf.transition("COMPLETED" if gate["status"] == "PASS" else "PHASE3_RUNNING",
                          "final validation re-check", "orchestrator")
            continue
        raise SystemExit(f"runner has no handler for state {state}")
    print("step budget exhausted")
    return 3


def _has_approval(phase: int, agent: str) -> bool:
    doc = read_json(STATE / "signoffs.json", {"signoffs": []}) or {"signoffs": []}
    return any(
        s.get("phase") == phase and s.get("agent") == agent and s.get("status") == "approved"
        for s in doc.get("signoffs", [])
    )


def resume() -> int:
    """Validate persistence, then report exactly where to continue."""
    wf = Workflow()
    if not wf.exists:
        raise SystemExit("nothing to resume: run `init`")
    d = wf.data
    last = d["transitions"][-1] if d["transitions"] else None
    print(f"state={d['state']} phase={d.get('phase')}")
    if last:
        print(f"last transition: {last['from']} -> {last['to']} at {last['ts']} "
              f"({last['reason']})")
        print(f"last transition digest={last.get('source_digest')} current={source_digest()}")
        if last.get("source_digest") != source_digest():
            print("NOTE: source changed since the last transition; re-run the phase gate.")
    cks = d.get("checkpoints", [])
    print(f"checkpoints: {len(cks)} (latest: {cks[-1]['id'] if cks else 'none'})")
    tasks = read_json(STATE / "agent_tasks.json", {"tasks": []}) or {"tasks": []}
    open_tasks = [t for t in tasks["tasks"] if t.get("status") != "done"]
    print(f"open tasks: {len(open_tasks)}")
    if d["state"] == "HALTED":
        print("state is HALTED: resolve the blocker, then transition manually with `set-state`.")
        return 3
    return run_loop()


def set_state(new_state: str, reason: str, agent: str) -> int:
    wf = Workflow()
    if not wf.exists:
        raise SystemExit("workflow not initialised")
    wf.transition(new_state, reason, agent)
    print(f"state -> {new_state}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="orchestrator", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")
    sub.add_parser("status")
    sub.add_parser("hw")
    sub.add_parser("resume")
    sub.add_parser("doctor")

    t = sub.add_parser("task")
    t.add_argument("action", choices=["add", "done", "note", "list"])
    t.add_argument("title", nargs="?")
    t.add_argument("--task-id", default=None)
    t.add_argument("--owner", default="orchestrator")
    t.add_argument("--phase", type=int, default=1)
    t.add_argument("--finding", default=None)
    t.add_argument("--note", default=None)

    e = sub.add_parser("experiment")
    e.add_argument("action", choices=["new", "finish", "list"])
    e.add_argument("exp_id", nargs="?", default=None)
    e.add_argument("--phase", type=int, default=1)
    e.add_argument("--hypothesis", default="")
    e.add_argument("--finding", default=None)
    e.add_argument("--config", default=None)
    e.add_argument("--components", nargs="*", default=None)
    e.add_argument("--dataset", default=None)
    e.add_argument("--command", default=None)
    e.add_argument("--decision", choices=["accepted", "rejected", "inconclusive", "completed"],
                   default=None)
    e.add_argument("--comparison", default=None)
    e.add_argument("--result", default=None)
    e.add_argument("--raw", default=None)

    c = sub.add_parser("checkpoint")
    c.add_argument("-m", "--message", default="checkpoint")
    c.add_argument("--kind", default="manual")

    r = sub.add_parser("rollback")
    r.add_argument("checkpoint_id")
    r.add_argument("--yes", action="store_true")

    g = sub.add_parser("gate")
    g.add_argument("phase", choices=["phase1", "phase2", "phase3"])

    a = sub.add_parser("approve")
    a.add_argument("phase", type=int, choices=[1, 2, 3])
    a.add_argument("--agent", required=True)
    a.add_argument("--note", default="")
    a.add_argument("--status", choices=["approved", "rejected"], default="approved")

    s = sub.add_parser("record-suite")
    s.add_argument("--label", default="final")

    sub.add_parser("run")
    ss = sub.add_parser("set-state")
    ss.add_argument("state", choices=STATES)
    ss.add_argument("--reason", default="manual")
    ss.add_argument("--agent", default="orchestrator")

    args = ap.parse_args(argv)

    if args.cmd == "init":
        wf = Workflow()
        wf.init()
        for d in ("artifacts", "baselines", "experiments", "reports", "logs", "checkpoints"):
            (HARNESS / d).mkdir(parents=True, exist_ok=True)
        print(f"initialised at {wf.path}")
        return 0
    if args.cmd == "status":
        return Workflow().status()
    if args.cmd == "hw":
        return capture_hardware()
    if args.cmd == "doctor":
        return doctor()
    if args.cmd == "resume":
        return resume()
    if args.cmd == "task":
        if args.action in ("done", "note") and not args.task_id:
            raise SystemExit("--task-id required")
        return task_cmd(args)
    if args.cmd == "experiment":
        if args.action == "finish" and not args.exp_id:
            raise SystemExit("experiment id required")
        return experiment_cmd(args)
    if args.cmd == "checkpoint":
        checkpoint_create(args.message, args.kind)
        return 0
    if args.cmd == "rollback":
        return checkpoint_restore(args.checkpoint_id, args.yes)
    if args.cmd == "gate":
        gate = evaluate_gate(args.phase)
        return 0 if gate["status"] == "PASS" else 3
    if args.cmd == "approve":
        return approve(args.phase, args.agent, args.note, args.status)
    if args.cmd == "record-suite":
        return record_suite(args.label)
    if args.cmd == "run":
        return run_loop()
    if args.cmd == "set-state":
        return set_state(args.state, args.reason, args.agent)
    raise SystemExit(f"unknown command {args.cmd}")


def doctor() -> int:
    """Self-test the harness itself before trusting its verdicts."""
    ok = True
    problems: list[str] = []

    # 1. atomic write + read round trip
    probe = STATE / "_probe.json"
    write_json_atomic(probe, {"a": 1, "nested": {"b": [1, 2]}})
    if read_json(probe) != {"a": 1, "nested": {"b": [1, 2]}}:
        ok = False
        problems.append("atomic write/read round trip failed")
    probe.unlink(missing_ok=True)

    # 2. illegal transitions are refused
    wf = Workflow()
    if wf.exists:
        try:
            wf.transition("COMPLETED", "doctor probe", "orchestrator")
            problems.append("illegal transition was accepted")
            ok = False
        except SystemExit:
            pass
        finally:
            wf.data = read_json(wf.path)

    # 3. checkpoint round trip: create, verify manifest hashes
    cid = checkpoint_create("doctor self-test", kind="doctor")
    dest = HARNESS / "checkpoints" / cid
    man = read_json(dest / "manifest.json")
    if not man or not man.get("files") or not (dest / "source.tar.gz").is_file():
        ok = False
        problems.append("checkpoint manifest incomplete")
    else:
        sample = next(iter(man["files"]))
        if sha256_file(REPO / sample) != man["files"][sample]:
            ok = False
            problems.append(f"checkpoint hash mismatch for {sample}")
    shutil.rmtree(dest, ignore_errors=True)
    if wf.exists:
        wf.data["checkpoints"] = [c for c in wf.data.get("checkpoints", []) if c["id"] != cid]
        write_json_atomic(wf.path, wf.data)

    # 4. every gate check is implemented and callable
    sys.path.insert(0, str(HARNESS))
    import checks  # noqa: PLC0415

    crit = _load_yaml(CFG / "acceptance_criteria.yaml")
    missing = []
    for phase in ("phase1", "phase2", "phase3"):
        for c in crit[phase]["criteria"]:
            if not callable(getattr(checks, c["check"], None)):
                missing.append(c["check"])
    if missing:
        ok = False
        problems.append(f"missing check implementations: {missing}")

    # 5. a fresh state dir must not crash status/gate
    if not wf.exists:
        problems.append("workflow not initialised (run `init`)")

    print("doctor:", "OK" if ok else "PROBLEMS")
    for p in problems:
        print("  -", p)
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
