"""CLI for the Autonomous AI Engineering Harness (engineering_team)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .harness import paths as harness_paths
from .harness.orchestrator import Orchestrator
from .physical import sessions as pt_sessions


def _print(obj) -> None:
    if isinstance(obj, (dict, list)):
        print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
    else:
        print(obj)


def _pt_db():
    from .physical.sessions import PhysicalTestDB
    return PhysicalTestDB()


def _pt_latest(db, status: str | None = None) -> dict | None:
    rows = db.list_sessions(status=status) if status else db.list_sessions()
    return rows[0] if rows else None


def _physical_test_cmd(args: argparse.Namespace) -> int:
    db = _pt_db()
    try:
        cmd = args.ptcmd
        if cmd == "start":
            fields = {
                "teacher_language": args.teacher_language,
                "target_language": args.target_language,
                "expected_students": args.expected_students,
                "actual_students": args.actual_students,
                "microphone_info": args.microphone_info,
                "laptop_info": args.laptop_info,
                "student_devices": args.student_devices,
                "operating_system": args.operating_system,
                "network_config": args.network_config,
                "internet_available": args.internet_available,
                "room_noise": args.room_noise,
                "device_distances": args.device_distances,
                "notes": args.notes,
            }
            fields = {k: v for k, v in fields.items() if v is not None}
            s = db.create_session(**fields)
            db.start(s["id"])
            _print({"session_id": s["id"], "status": db.get_session(s["id"])["status"]})
            return 0
        if cmd == "list":
            _print(db.list_sessions())
            return 0
        if cmd == "status":
            s = _pt_latest(db)
            if not s:
                _print({"session": None, "note": "no sessions yet; run physical-test start"})
                return 0
            from .physical.sessions import session_summary
            _print(session_summary(db, s["id"]))
            return 0
        # pause/resume/stop/report/export/feedback/condition need a session id
        sid = getattr(args, "session", None)
        if not sid:
            s = _pt_latest(db)
            sid = s["id"] if s else None
        if not sid:
            raise SystemExit("no session; run `physical-test start` first")
        if cmd == "pause":
            _print(db.pause(sid)); return 0
        if cmd == "resume":
            _print(db.resume(sid)); return 0
        if cmd == "stop":
            _print(db.stop(sid)); return 0
        if cmd == "report":
            from .physical.sessions import session_summary
            _print(session_summary(db, sid)); return 0
        if cmd == "export":
            from .physical.sessions import session_summary
            data = session_summary(db, sid)
            out = Path(f"engineering_team/state/physical_tests/{sid}_evidence.json")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
            print(out)
            return 0
        if cmd == "feedback":
            fid = db.add_feedback(sid, args.body, role=args.role, category=args.category)
            print(f"feedback {fid} recorded for {sid}")
            return 0
        if cmd == "condition":
            db.set_condition(sid, args.key, args.value, source=args.source)
            print(f"condition {args.key}={args.value} recorded for {sid}")
            return 0
        raise SystemExit(f"unknown physical-test command {cmd}")
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    harness_paths.ensure_workspace()
    ap = argparse.ArgumentParser(prog="engineering_team", description="Offline Guru Autonomous AI Engineering Harness")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("start", help="Start/resume discovery cycle")
    sub.add_parser("status", help="Show workflow and harness status")
    sub.add_parser("pause", help="Pause workflow")
    sub.add_parser("resume", help="Resume from PAUSED state")
    sub.add_parser("stop", help="Safely stop/pause workflow")
    sub.add_parser("report", help="Show decision report path and status")
    sub.add_parser("decisions", help="List recorded human decisions")

    a = sub.add_parser("approve", help="Approve a candidate (A, B, HYBRID)")
    a.add_argument("choice", choices=["A", "B", "HYBRID", "a", "b", "hybrid", "Hybrid"])

    sub.add_parser("reject", help="Reject both candidates")

    rt = sub.add_parser("request-tests", help="Request more tests/evidence")
    rt.add_argument("--note", default="More tests requested by human")

    # Useful operational commands
    pt = sub.add_parser("physical-test", help="Physical test session management")
    ptsub = pt.add_subparsers(dest="ptcmd", required=True)

    pstart = ptsub.add_parser("start", help="Create+start a physical test session")
    pstart.add_argument("--teacher-language", default="en")
    pstart.add_argument("--target-language", default="bn")
    pstart.add_argument("--expected-students", type=int, default=None)
    pstart.add_argument("--actual-students", type=int, default=None)
    pstart.add_argument("--microphone-info", default=None)
    pstart.add_argument("--laptop-info", default=None)
    pstart.add_argument("--student-devices", default=None)
    pstart.add_argument("--operating-system", default=None)
    pstart.add_argument("--network-config", default=None)
    pstart.add_argument("--internet-available", choices=["yes", "no"], default=None)
    pstart.add_argument("--room-noise", default=None)
    pstart.add_argument("--device-distances", default=None)
    pstart.add_argument("--notes", default="")

    ptsub.add_parser("list", help="List physical test sessions")
    ptsub.add_parser("status", help="Current/active session status")

    for name in ("pause", "resume", "stop", "report", "export"):
        p = ptsub.add_parser(name, help=f"{name} a physical test session")
        p.add_argument("--session", default=None, help="session id (default: latest active)")

    pfb = ptsub.add_parser("feedback", help="Record human feedback for a session")
    pfb.add_argument("body")
    pfb.add_argument("--session", default=None)
    pfb.add_argument("--role", default="operator")
    pfb.add_argument("--category", default="observation")

    pcond = ptsub.add_parser("condition", help="Record a test condition")
    pcond.add_argument("key")
    pcond.add_argument("value")
    pcond.add_argument("--session", default=None)
    pcond.add_argument("--source", default="human")

    ag = sub.add_parser("run-agent", help="Execute one specialist agent via the real Claude Code runtime")
    ag.add_argument("agent", help="agent role name or AGENTS.md section key")
    ag.add_argument("--task-id", required=True, help="existing task id to execute")
    ag.add_argument("--track", choices=["A", "B", "HYBRID"], default=None)
    ag.add_argument("--problem", default="", help="problem description for the agent")
    ag.add_argument("--evidence", nargs="*", default=None)
    ag.add_argument("--deliverables", nargs="*", default=None)
    ag.add_argument("--constraints", nargs="*", default=None)
    ag.add_argument("--timeout", type=float, default=None)
    ag.add_argument("--workspace-mode", choices=["inspect", "edit"], default=None,
                    help="inspect = frozen source snapshot; edit = writable candidate copy "
                         "(default: chosen from the agent role)")

    ck = sub.add_parser("checkpoint", help="Create a checkpoint")
    ck.add_argument("-m", "--message", default="manual checkpoint")
    ck.add_argument("--kind", default="manual")

    sub.add_parser("inspect", help="Run basic repository inspection and record facts")

    prob = sub.add_parser("problem", help="Record a discovered problem")
    prob.add_argument("--title", required=True)
    prob.add_argument("--description", default="")
    prob.add_argument("--severity", default="P2")

    integ = sub.add_parser("integrate", help="Record integration intent for approved candidate (no auto-merge)")
    integ.add_argument("--dry-run", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "physical-test":
        return _physical_test_cmd(args)

    orch = Orchestrator()
    try:
        if args.cmd == "start":
            _print(orch.start())
        elif args.cmd == "status":
            _print(orch.status())
        elif args.cmd == "pause":
            _print(orch.pause())
        elif args.cmd == "resume":
            _print(orch.resume())
        elif args.cmd == "stop":
            _print(orch.stop())
        elif args.cmd == "report":
            report_path = harness_paths.decision_report_path()
            _print({"decision_report": str(report_path), "exists": report_path.exists(), **orch.status()})
        elif args.cmd == "decisions":
            rows = orch.db.query("SELECT * FROM decisions ORDER BY created_at DESC")
            _print(rows)
        elif args.cmd == "approve":
            _print(orch.approve(args.choice.upper()))
        elif args.cmd == "reject":
            _print(orch.reject())
        elif args.cmd == "request-tests":
            _print(orch.request_tests(args.note))
        elif args.cmd == "checkpoint":
            _print(orch.create_checkpoint(args.message, kind=args.kind))
        elif args.cmd == "inspect":
            facts = orch.run_basic_repo_inspection()
            _print(facts)
        elif args.cmd == "run-agent":
            _print(orch.execute_agent(
                agent=args.agent, task_id=args.task_id, track=args.track,
                problem_description=args.problem, evidence_refs=args.evidence,
                deliverables=args.deliverables, constraints=args.constraints,
                timeout_s=args.timeout, workspace_mode=args.workspace_mode))
        elif args.cmd == "problem":
            pid = orch.discover_problem(args.title, args.description, args.severity)
            _print({"problem_id": pid})
        elif args.cmd == "integrate":
            _print(orch.integrate_approved(dry_run=args.dry_run))
        else:
            ap.print_help()
            return 2
        return 0
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        orch.close()


if __name__ == "__main__":
    raise SystemExit(main())
