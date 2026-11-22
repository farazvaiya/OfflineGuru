"""Generic subprocess runtime (Phase 5) — DEPRECATED for agent labour.

The production agent runtime is `harness/claude_runtime.py`, which owns the
permission boundary (role-scoped tools, sanitized env, bwrap sandbox) described
in `harness/permissions.py`. This module is retained for the physical-test
collector tests and generic command execution; it shares the same containment
primitive (`permissions.check_within`).

Original description:

Real agent execution runtime (Phase 5).

Replaces the `pending_external` behaviour with an actually-configured
subprocess runtime, following the master prompt's Phase 5 requirements:

  - runtime availability check (executable exists + version probe)
  - explicit executable configuration (env TEAM_AGENT_RUNTIME or ctor arg)
  - safe argument handling: argv list, never a shell
  - execution timeout with kill
  - bounded stdout/stderr capture (no unbounded memory)
  - run ids, agent identity, task id, evidence references, exit status
  - resource limits where supported (POSIX RLIMIT_CPU / RLIMIT_AS)
  - NO recursive Claude launches: the runtime refuses to execute an agent
    whose executable resolves to another claude/claude-code process
  - internet-requiring runtimes are labelled, never silently assumed offline

Agents run read-only or isolated-workspace commands. This runtime does NOT
auto-approve, integrate, merge, or modify production code.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from ..harness.db import Database

DEFAULT_TIMEOUT_S = 120.0
MAX_OUTPUT_BYTES = 64 * 1024
CAPTURE_CHUNK = 4096


class AgentRuntimeError(RuntimeError):
    pass


class AgentExecution:
    """A real subprocess execution adapter for agent labour."""

    def __init__(self, db: Database, executable: str | None = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 cpu_limit_s: int | None = 300,
                 mem_limit_mb: int | None = 2048,
                 allowed_roots: list[str] | None = None) -> None:
        self.db = db
        self.executable = executable or os.environ.get("TEAM_AGENT_RUNTIME", "claude")
        self.timeout_s = float(timeout_s)
        self.cpu_limit_s = cpu_limit_s
        self.mem_limit_mb = mem_limit_mb
        self.allowed_roots = [Path(r).resolve() for r in (allowed_roots or [str(Path.cwd())])]
        self._resolve_check()

    # -- safety ---------------------------------------------------------------
    def _resolve_check(self) -> None:
        """Availability check; refuse recursive Claude launches."""
        exe = self.executable
        path = shutil.which(exe) if not Path(exe).is_absolute() else exe
        if not path:
            raise AgentRuntimeError(f"agent runtime not found: {exe!r} "
                                    "(set TEAM_AGENT_RUNTIME to an existing executable)")
        self.resolved = Path(path).resolve()
        name = self.resolved.name.lower()
        if "claude" in name or "claude-code" in name:
            raise AgentRuntimeError(
                "recursive Claude launch refused: this runtime must not spawn "
                "another Claude Code instance (master prompt rule)")

    def available(self) -> dict[str, Any]:
        """Version probe; result is evidence, not a claim."""
        try:
            proc = subprocess.run([str(self.resolved), "--version"], capture_output=True,
                                  text=True, timeout=10)
            return {"available": True, "executable": str(self.resolved),
                    "version": (proc.stdout or proc.stderr).strip()[:200],
                    "exit_code": proc.returncode}
        except Exception as e:
            return {"available": False, "executable": str(self.resolved),
                    "error": f"{type(e).__name__}: {e}"}

    # -- path safety ----------------------------------------------------------
    def _safe_args(self, args: list[str]) -> list[str]:
        """Every path-like argument must stay inside allowed_roots.

        Containment is component-based (shared `check_within`), not a string
        prefix: `/tmp/ws_evil` is not inside `/tmp/ws`, and symlinks are
        resolved before the comparison.
        """
        from .permissions import check_within

        out = []
        for a in args:
            p = Path(a)
            if a.startswith("-"):
                out.append(a)
                continue
            if not p.is_absolute() and not p.exists():
                out.append(a)  # plain command token, not a filesystem path
                continue
            try:
                rp = check_within(p, self.allowed_roots, what="argument path")
            except PermissionError as e:
                raise AgentRuntimeError(str(e)) from e
            out.append(str(rp))
        return out

    # -- execution ------------------------------------------------------------
    def run(self, agent: str, task: dict[str, Any] | None = None,
            problem_id: str | None = None, command: list[str] | None = None,
            cwd: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Execute `command` (argv list) as the agent's runtime work.

        If `command` is None, the adapter records a pending_external run —
        the honest state when no command was configured. It never fabricates
        an execution.
        """
        run_id = uuid.uuid4().hex[:12]
        started = time.time()
        self.db.execute(
            "INSERT INTO agent_runs(id,agent,task_id,problem_id,status,started_at,output_json)"
            " VALUES(?,?,?,?,?,?,?)",
            (run_id, agent, (task or {}).get("id"), problem_id, "running", started, "{}"),
        )
        if command is None:
            out = {"status": "pending_external",
                   "message": "no command configured for this agent run; "
                              "pass command=[...] to execute a real runtime",
                   "task": task}
            self.db.execute(
                "UPDATE agent_runs SET status=?, finished_at=?, output_json=? WHERE id=?",
                ("pending_external", time.time(), json.dumps(out, default=str), run_id))
            return {"run_id": run_id, "status": "pending_external", "output": out}

        try:
            argv = [str(self.resolved)] + self._safe_args([str(c) for c in command])
            workdir = str(Path(cwd).resolve()) if cwd else str(Path.cwd())
            limits = {}

            def preexec() -> None:
                import resource
                if self.cpu_limit_s:
                    resource.setrlimit(resource.RLIMIT_CPU, (self.cpu_limit_s, self.cpu_limit_s + 30))
                if self.mem_limit_mb:
                    resource.setrlimit(resource.RLIMIT_AS,
                                       (self.mem_limit_mb * 1024 * 1024, self.mem_limit_mb * 1024 * 1024))

            proc = subprocess.Popen(
                argv, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                preexec_fn=preexec if os.name != "nt" else None,
            )
            stdout_chunks: list[bytes] = []
            stderr_chunks: list[bytes] = []

            def drain(stream, bucket):
                while True:
                    chunk = stream.read(CAPTURE_CHUNK)
                    if not chunk:
                        break
                    bucket.append(chunk)
                    if sum(map(len, bucket)) > MAX_OUTPUT_BYTES:
                        bucket[-1] = bucket[-1][: MAX_OUTPUT_BYTES - sum(map(len, bucket[:-1]))]
                        break

            import threading
            t1 = threading.Thread(target=drain, args=(proc.stdout, stdout_chunks), daemon=True)
            t2 = threading.Thread(target=drain, args=(proc.stderr, stderr_chunks), daemon=True)
            t1.start(); t2.start()
            try:
                rc = proc.wait(timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                rc = None
            t1.join(timeout=2); t2.join(timeout=2)
            stdout = b"".join(stdout_chunks).decode("utf-8", "replace")
            stderr = b"".join(stderr_chunks).decode("utf-8", "replace")
            finished = time.time()
            status = "completed" if rc == 0 else ("failed" if rc is not None else "timeout")
            out = {
                "status": status,
                "exit_code": rc,
                "executable": str(self.resolved),
                "argv": argv[1:],
                "cwd": workdir,
                "elapsed_s": round(finished - started, 2),
                "stdout_bytes": len(stdout),
                "stderr_bytes": len(stderr),
                "stdout_tail": stdout[-2000:],
                "stderr_tail": stderr[-2000:],
                "timeout_s": self.timeout_s,
            }
            self.db.execute(
                "UPDATE agent_runs SET status=?, finished_at=?, output_json=? WHERE id=?",
                (status, finished, json.dumps(out, default=str), run_id))
            out["run_id"] = run_id
            return out
        except Exception as e:
            self.db.execute(
                "UPDATE agent_runs SET status=?, finished_at=?, error=?, output_json=? WHERE id=?",
                ("failed", time.time(), str(e), json.dumps({"exception": str(e)}), run_id))
            return {"run_id": run_id, "status": "failed", "error": str(e)}
