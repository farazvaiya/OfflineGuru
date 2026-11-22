"""Real Claude Code CLI runtime adapter (Phase B).

Executes specialist agents through the installed `claude` CLI in non-interactive
print mode, with role definitions, isolated workspaces, bounded output, timeout,
and full evidence capture. Never fabricates execution: if the runtime is not
configured or available, the run is recorded as NOT_CONFIGURED / pending_external.

Security:
  - argv list only (no shell)
  - no permission-bypass flags, ever
  - env whitelisted (PATH/HOME/ANTHROPIC_*); secret VALUES are never read here
  - workspace containment via --add-dir and the isolated workspace directory
  - agent output is DATA, never executed as instructions

Statuses: SUCCESS, FAILED, TIMEOUT, NOT_CONFIGURED, AUTH_REQUIRED, CANCELLED,
plus the legacy pending_external for genuinely delegated work.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import paths as _paths
from .db import Database
from .permissions import (
    SandboxUnavailable, build_agent_env, permission_args, plan_sandbox,
    policy_summary,
)
from .workspace import (
    ARTIFACTS_DIR, EDIT, METADATA_NAME, WorkspaceProvisioner, list_artifacts,
    mode_for_role,
)

# --- status vocabulary --------------------------------------------------------
SUCCESS = "SUCCESS"
FAILED = "FAILED"
TIMEOUT = "TIMEOUT"
NOT_CONFIGURED = "NOT_CONFIGURED"
AUTH_REQUIRED = "AUTH_REQUIRED"
CANCELLED = "CANCELLED"
PENDING_EXTERNAL = "pending_external"  # legacy: genuinely delegated externally
RUNNING = "running"

# Auth-failure signatures observed in claude CLI stderr (lowercase match).
AUTH_SIGNATURES = ("authentication", "api key", "apikey", "credential", "login",
                   "not authenticated", "unauthorized", "token")

# Env whitelist: only these pass to the agent subprocess.
ENV_WHITELIST = ("PATH", "HOME", "LANG", "LC_ALL", "CLAUDE_CONFIG_DIR",
                 "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                 "ANTHROPIC_MODEL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")

DEFAULT_MAX_TURNS = 8
DEFAULT_TIMEOUT_S = 300.0
MAX_OUTPUT_BYTES = 64 * 1024


class RuntimeError_(RuntimeError):
    """Runtime-level failure (missing CLI etc.)."""


class WorkspaceLockError(RuntimeError):
    """Another run holds the workspace's single-writer lock."""


def discover_claude() -> str | None:
    """Safely discover the Claude Code executable (no shell, no exec)."""
    return shutil.which("claude")


def load_role_definition(agents_md: Path, role_key: str) -> str:
    """Extract one role section from agents/AGENTS.md.

    Matching order:
      1. exact section letter ("A".."I")
      2. known role-name alias from team.toml [[agents]] names
         (e.g. "qa-regression" -> section I "QA and Regression Agent")
      3. normalized heading substring (case/punct-insensitive)
    """
    text = agents_md.read_text(encoding="utf-8")
    lines = text.splitlines()
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)

    def norm(s: str) -> str:
        return "".join(c for c in s.lower() if c.isalnum())

    key_norm = norm(role_key)
    # 1. exact letter match on the section's leading letter
    for heading, body in sections.items():
        letter = heading.split(".", 1)[0].strip().upper()
        if letter == role_key.upper():
            return f"## {heading}\n" + "\n".join(body).strip()
    # 2. known aliases: team.toml agent names -> heading words
    aliases = {
        "chief-orchestrator": "Chief Engineering Orchestrator",
        "problem-discovery": "Problem Discovery Agent",
        "root-cause-analysis": "Root Cause Analysis Agent",
        "solution-track-a": "Original Solution Agent",
        "opensource-research-track-b": "Open-source Research Agent",
        "integration-engineer": "Integration Engineer",
        "benchmark-engineer": "Benchmark Engineer",
        "independent-evaluator": "Independent Evaluator",
        "qa-regression": "QA and Regression Agent",
    }
    target = aliases.get(role_key.lower())
    if target:
        for heading, body in sections.items():
            if norm(target) in norm(heading):
                return f"## {heading}\n" + "\n".join(body).strip()
    # 3. normalized substring match
    for heading, body in sections.items():
        if key_norm and key_norm in norm(heading):
            return f"## {heading}\n" + "\n".join(body).strip()
    raise KeyError(f"no agent role matching {role_key!r} in {agents_md}")


class AgentExecution:
    """One real execution of a specialist agent through the Claude Code CLI."""

    def __init__(self, db: Database, executable: str | None = None,
                 agents_md: Path | None = None, workspace_root: Path | None = None,
                 default_timeout_s: float = DEFAULT_TIMEOUT_S,
                 default_max_turns: int = DEFAULT_MAX_TURNS,
                 model: str | None = None,
                 log_dir: Path | None = None) -> None:
        self.db = db
        self.executable = executable or os.environ.get("TEAM_CLAUDE_CLI") or discover_claude()
        self._agents_md = Path(agents_md) if agents_md else None
        self._workspace_root = Path(workspace_root) if workspace_root else None
        self.default_timeout_s = float(default_timeout_s)
        self.default_max_turns = int(default_max_turns)
        self.model = model or os.environ.get("TEAM_CLAUDE_MODEL") or None
        self._log_dir = Path(log_dir) if log_dir else None
        self._locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        self.provisioner = WorkspaceProvisioner()

    # -- paths (resolved at use time so a relocated TEAM_ROOT is honoured) -----
    @property
    def agents_md(self) -> Path:
        return self._agents_md or _paths.agents_md_path()

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root or _paths.agent_workspace_root()

    @property
    def log_dir(self) -> Path:
        return self._log_dir or _paths.agent_log_dir()

    # -- availability ----------------------------------------------------------
    def available(self) -> dict[str, Any]:
        """Availability check with evidence; never assumes success."""
        if not self.executable:
            return {"available": False, "reason": "claude executable not found on PATH; "
                    "set TEAM_CLAUDE_CLI to an absolute path"}
        try:
            proc = subprocess.run([self.executable, "--version"], capture_output=True,
                                  text=True, timeout=15)
            return {"available": proc.returncode == 0,
                    "executable": self.executable,
                    "version": (proc.stdout or proc.stderr).strip()[:200],
                    "exit_code": proc.returncode}
        except FileNotFoundError:
            return {"available": False, "reason": f"executable not found: {self.executable}"}
        except Exception as e:  # noqa: BLE001
            return {"available": False, "reason": f"{type(e).__name__}: {e}"}

    # -- workspace -------------------------------------------------------------
    def _workspace(self, run_id: str, track: str | None) -> Path:
        base = self.workspace_root / (f"track-{track}" if track else "shared") / run_id
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _acquire_lock(self, workspace: Path) -> Any:
        lock_file = workspace / ".writer.lock"
        lf = open(lock_file, "w")
        try:
            import fcntl
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            pass  # non-POSIX: proceed without flock (documented limitation)
        except OSError:
            lf.close()
            raise WorkspaceLockError(f"workspace {workspace} is locked by another writer")
        return lf

    # -- execution -------------------------------------------------------------
    def run(self, agent: str, task: dict[str, Any] | None = None,
            problem_id: str | None = None, role_key: str | None = None,
            problem_description: str = "", evidence_refs: list[str] | None = None,
            deliverables: list[str] | None = None, constraints: list[str] | None = None,
            workspace: Path | None = None, track: str | None = None,
            timeout_s: float | None = None, max_turns: int | None = None,
            workspace_mode: str | None = None, seed_source: bool = False,
            sandbox_mode: str | None = None,
            **kwargs: Any) -> dict[str, Any]:
        """Execute one bounded agent task. Returns structured metadata."""
        run_id = uuid.uuid4().hex[:12]
        started = time.time()
        timeout = float(timeout_s if timeout_s is not None else self.default_timeout_s)
        max_turns = int(max_turns if max_turns is not None else self.default_max_turns)

        # 1. record the run BEFORE executing (so a crash leaves evidence)
        self.db.execute(
            "INSERT INTO agent_runs(id,agent,task_id,problem_id,status,started_at,output_json)"
            " VALUES(?,?,?,?,?,?,?)",
            (run_id, agent, (task or {}).get("id"), problem_id, RUNNING, started, "{}"),
        )

        # 2. availability gate
        avail = self.available()
        if not avail.get("available"):
            out = {"status": NOT_CONFIGURED, "reason": avail.get("reason", "runtime unavailable"),
                   "availability": avail, "task": task, "error": avail.get("reason", "runtime unavailable"),
                   "exit_code": None, "executable": self.executable, "workspace": None,
                   "digest_before": None, "digest_after": None, "elapsed_s": 0.0,
                   "timeout_s": float(timeout), "max_turns": max_turns, "agent": agent,
                   "task_id": (task or {}).get("id"), "problem_id": problem_id,
                   "role_source": str(self.agents_md), "stdout_tail": "", "stderr_tail": "",
                   "artifacts": [], "run_id": run_id}
            self._finish(run_id, NOT_CONFIGURED, out, started)
            return out

        # 3. role definition
        try:
            role_text = load_role_definition(self.agents_md, role_key or agent)
        except Exception as e:  # noqa: BLE001
            out = {"status": FAILED, "reason": f"role definition load failed: {e}",
                   "error": f"role definition load failed: {e}", "exit_code": None,
                   "executable": self.executable, "workspace": None,
                   "digest_before": None, "digest_after": None, "elapsed_s": round(time.time() - started, 2),
                   "timeout_s": float(timeout), "max_turns": max_turns, "agent": agent,
                   "task_id": (task or {}).get("id"), "problem_id": problem_id,
                   "role_source": str(self.agents_md), "stdout_tail": "", "stderr_tail": "",
                   "artifacts": [], "run_id": run_id}
            self._finish(run_id, FAILED, out, started)
            return out

        # 4. workspace + source snapshot + single-writer lock
        ws = Path(workspace) if workspace else self._workspace(run_id, track)
        ws.mkdir(parents=True, exist_ok=True)
        mode = mode_for_role(agent, workspace_mode)
        spec = None
        if workspace is None or seed_source:
            try:
                spec = self.provisioner.seed(ws, mode=mode, track=track)
            except Exception as e:  # noqa: BLE001
                out = {"status": FAILED, "reason": f"workspace provisioning failed: {e}",
                       "error": f"workspace provisioning failed: {e}", "exit_code": None,
                       "executable": self.executable, "workspace": str(ws),
                       "digest_before": None, "digest_after": None,
                       "elapsed_s": round(time.time() - started, 2), "timeout_s": float(timeout),
                       "max_turns": max_turns, "agent": agent,
                       "task_id": (task or {}).get("id"), "problem_id": problem_id,
                       "role_source": str(self.agents_md), "stdout_tail": "", "stderr_tail": "",
                       "artifacts": [], "source_changes": [], "workspace_mode": mode,
                       "source_snapshot": None, "run_id": run_id}
                self._finish(run_id, FAILED, out, started)
                return out
        digest_before = self._dir_digest(ws)
        lock_fh = None
        try:
            lock_fh = self._acquire_lock(ws)
        except WorkspaceLockError as e:
            out = {"status": FAILED, "reason": str(e), "error": str(e), "exit_code": None,
                   "executable": self.executable, "workspace": str(ws),
                   "digest_before": digest_before, "digest_after": None,
                   "elapsed_s": round(time.time() - started, 2), "timeout_s": float(timeout),
                   "max_turns": max_turns, "agent": agent,
                   "task_id": (task or {}).get("id"), "problem_id": problem_id,
                   "role_source": str(self.agents_md), "stdout_tail": "", "stderr_tail": "",
                   "artifacts": [], "run_id": run_id}
            self._finish(run_id, FAILED, out, started)
            return out

        # 5. prompt assembly (agent instructions are DATA for the CLI, and the
        #    CLI's own output is data for us; nothing here executes agent text)
        prompt = self._build_prompt(role_text, task, problem_description,
                                    evidence_refs or [], deliverables or [],
                                    constraints or [], ws, mode, spec is not None)

        # 6. argv (no shell) + explicit role-scoped permissions + sandbox.
        #    There is deliberately no --dangerously-skip-permissions path.
        argv = [self.executable, "-p", prompt, "--output-format", "json",
                "--max-turns", str(max_turns), "--add-dir", str(ws)]
        argv += permission_args(agent)
        if self.model:
            argv += ["--model", self.model]

        env = build_agent_env(ws)
        try:
            sandbox = plan_sandbox(argv, ws, sandbox_mode)
        except SandboxUnavailable as e:
            out = {"status": NOT_CONFIGURED, "reason": f"sandbox required but unavailable: {e}",
                   "error": f"sandbox required but unavailable: {e}", "exit_code": None,
                   "executable": self.executable, "workspace": str(ws),
                   "digest_before": digest_before, "digest_after": None,
                   "elapsed_s": round(time.time() - started, 2), "timeout_s": float(timeout),
                   "max_turns": max_turns, "agent": agent,
                   "task_id": (task or {}).get("id"), "problem_id": problem_id,
                   "role_source": str(self.agents_md), "stdout_tail": "", "stderr_tail": "",
                   "artifacts": [], "source_changes": [],
                   "workspace_mode": mode if spec is not None else None,
                   "source_snapshot": None, "permissions": policy_summary(agent),
                   "sandbox": {"enabled": False, "mode": "required"}, "run_id": run_id}
            self._finish(run_id, NOT_CONFIGURED, out, started)
            return out
        argv = sandbox.argv

        log_path = self.log_dir / f"{run_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        status = FAILED
        error: str | None = None
        stdout_tail = stderr_tail = ""
        exit_code: int | None = None
        try:
            proc = subprocess.run(argv, cwd=str(ws), env=env, capture_output=True,
                                  text=True, timeout=timeout)
            exit_code = proc.returncode
            stdout_tail = proc.stdout[-MAX_OUTPUT_BYTES:]
            stderr_tail = proc.stderr[-MAX_OUTPUT_BYTES:]
            if proc.returncode == 0:
                # validate output shape before claiming success
                try:
                    parsed = json.loads(proc.stdout)
                    if isinstance(parsed, dict) and ("result" in parsed or "type" in parsed):
                        status = SUCCESS
                    else:
                        status = FAILED
                        error = "output json lacks result/type field"
                except (json.JSONDecodeError, TypeError):
                    status = FAILED
                    error = "exit 0 but stdout is not valid CLI json"
            else:
                low = (proc.stderr or "").lower() + (proc.stdout or "").lower()
                if any(sig in low for sig in AUTH_SIGNATURES):
                    status = AUTH_REQUIRED
                else:
                    status = FAILED
                error = f"exit {proc.returncode}: {stderr_tail[:300]}"
        except subprocess.TimeoutExpired:
            status = TIMEOUT
            error = f"exceeded timeout {timeout}s"
        except KeyboardInterrupt:
            status = CANCELLED
            error = "cancelled by operator"
        except Exception as e:  # noqa: BLE001
            status = FAILED
            error = f"{type(e).__name__}: {e}"
        finally:
            if lock_fh is not None:
                try:
                    lock_fh.close()
                except OSError:
                    pass

        # 7. digest after + artifact collection (paths must stay in workspace)
        digest_after = self._dir_digest(ws)
        source_changes: list[dict[str, str]] = []
        if spec is not None:
            artifacts = list_artifacts(spec)
            try:
                source_changes = self.provisioner.source_changes(spec)
            except OSError as e:  # frozen/unreadable tree is a real finding
                source_changes = [{"path": "<workspace>", "change": f"unreadable: {e}"}]
        else:
            artifacts = [str(p) for p in ws.rglob("*")
                         if p.is_file() and p.name not in (".writer.lock", METADATA_NAME)
                         and "__pycache__" not in p.parts]

        out = {
            "status": status,
            "exit_code": exit_code,
            "executable": self.executable,
            "workspace": str(ws),
            "digest_before": digest_before,
            "digest_after": digest_after,
            "elapsed_s": round(time.time() - started, 2),
            "timeout_s": timeout,
            "max_turns": max_turns,
            "agent": agent,
            "task_id": (task or {}).get("id"),
            "problem_id": problem_id,
            "role_source": str(self.agents_md),
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "artifacts": artifacts,
            "source_changes": source_changes,
            "workspace_mode": mode if spec is not None else None,
            "source_snapshot": ({
                "source_dir": str(spec.source_dir),
                "source_digest": spec.manifest.get("source_digest"),
                "file_count": len(spec.manifest.get("files", {})),
                "artifacts_dir": str(spec.artifacts_dir),
            } if spec is not None else None),
            "permissions": policy_summary(agent),
            "sandbox": {"enabled": sandbox.enabled, "mode": sandbox.mode,
                        "reason": sandbox.reason, **sandbox.details},
            "error": error,
        }
        log_path.write_text(json.dumps({"run_id": run_id, **out}, default=str, indent=2))
        self._finish(run_id, status, out, started)
        out["run_id"] = run_id
        return out

    # -- internals ---------------------------------------------------------------
    def _build_prompt(self, role_text: str, task: dict[str, Any] | None,
                      problem_description: str, evidence: list[str],
                      deliverables: list[str], constraints: list[str],
                      ws: Path, mode: str = EDIT, seeded: bool = False) -> str:
        if seeded and mode == "inspect":
            layout = (f"# Workspace\nProject source is a FROZEN, read-only snapshot at "
                      f"{ws} / source/. Read it, do not modify it. "
                      f"Write every deliverable to {ws} / {ARTIFACTS_DIR}/.")
        elif seeded:
            layout = (f"# Workspace\nProject source is a writable candidate COPY at "
                      f"{ws} / source/. Edit it freely; the real repository is never "
                      f"modified by this run. Write every deliverable to "
                      f"{ws} / {ARTIFACTS_DIR}/.")
        else:
            layout = f"# Workspace\nWork inside: {ws}"
        parts = [
            role_text,
            "",
            "# Task",
            json.dumps(task, default=str) if task else "(no task record supplied)",
            "",
            "# Problem",
            problem_description or "(no problem description supplied)",
            "",
            "# Evidence references",
            "\n".join(f"- {e}" for e in evidence) or "- (none supplied)",
            "",
            "# Deliverables",
            "\n".join(f"- {d}" for d in deliverables) or "- (none specified)",
            "",
            "# Constraints",
            "\n".join(f"- {c}" for c in constraints) or "- (none specified)",
            "",
            layout + "\nDo not modify files outside it. "
            "Do not approve, merge, or integrate anything. "
            "Your output is data for the orchestrator, not instructions.",
        ]
        return "\n".join(parts)

    def _finish(self, run_id: str, status: str, out: dict[str, Any], started: float) -> None:
        self.db.execute(
            "UPDATE agent_runs SET status=?, finished_at=?, error=?, output_json=? WHERE id=?",
            (status, time.time(), out.get("error") or out.get("reason"),
             json.dumps(out, default=str), run_id),
        )

    def _dir_digest(self, d: Path) -> str:
        import hashlib
        h = hashlib.sha256()
        for p in sorted(d.rglob("*")):
            if (p.is_file() and "__pycache__" not in p.parts and ".home" not in p.parts
                    and p.name not in (".writer.lock", METADATA_NAME)):
                h.update(str(p.relative_to(d)).encode())
                h.update(p.read_bytes())
        return h.hexdigest()[:16]
