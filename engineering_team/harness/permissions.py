"""One permission boundary for agent execution.

Three layers, all enforceable without trusting the prompt:

1. Tool policy   - per-role `--allowedTools` allowlist + `--permission-mode`.
                   Read-only roles get Read/Grep/Glob in `plan` mode; write
                   roles get Edit/Write; never `--dangerously-skip-permissions`.
2. Environment   - an explicit allowlist of variables. HOME is redirected to a
                   private per-run directory; cloud credentials, tokens, SSH
                   agent sockets and unrelated project settings are not passed.
3. Filesystem    - when `bwrap` is available the agent runs in a user namespace
                   where only the workspace is writable, the user's home and the
                   real project tree are invisible, and /usr,/lib,/etc are
                   read-only. The mode is recorded in every run result; if the
                   sandbox is required but unavailable the run is refused rather
                   than silently downgraded.

`check_within` is the single path-containment primitive; it compares resolved
paths with `is_relative_to`, so `/tmp/ws_evil` no longer passes for `/tmp/ws`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

READ_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob")
EDIT_TOOLS: tuple[str, ...] = READ_TOOLS + ("Edit", "Write")
TEST_TOOLS: tuple[str, ...] = EDIT_TOOLS + ("Bash(.venv/bin/python -m pytest:*)",)

PLAN = "plan"
ACCEPT_EDITS = "acceptEdits"


@dataclass(frozen=True)
class RolePolicy:
    tools: tuple[str, ...]
    permission_mode: str
    can_edit: bool


_READ_ONLY = RolePolicy(READ_TOOLS, PLAN, False)
_IMPLEMENT = RolePolicy(EDIT_TOOLS, ACCEPT_EDITS, True)
_TESTING = RolePolicy(TEST_TOOLS, ACCEPT_EDITS, True)

ROLE_POLICIES: dict[str, RolePolicy] = {
    "chief-orchestrator": _READ_ONLY,
    "problem-discovery": _READ_ONLY,
    "root-cause-analysis": _READ_ONLY,
    "opensource-research-track-b": _READ_ONLY,
    "independent-evaluator": _READ_ONLY,
    "solution-track-a": _IMPLEMENT,
    "integration-engineer": _IMPLEMENT,
    "benchmark-engineer": _TESTING,
    "qa-regression": _TESTING,
}

# AGENTS.md section letters, for callers that use "A".."I".
_SECTION_ALIASES = {
    "a": "chief-orchestrator",
    "b": "problem-discovery",
    "c": "root-cause-analysis",
    "d": "solution-track-a",
    "e": "qa-regression",
    "f": "benchmark-engineer",
    "g": "qa-regression",
    "h": "independent-evaluator",
    "i": "integration-engineer",
}


def policy_for(agent: str) -> RolePolicy:
    key = (agent or "").strip().lower()
    key = _SECTION_ALIASES.get(key, key)
    # Least privilege by default: an unrecognised role is treated as read-only.
    return ROLE_POLICIES.get(key, _READ_ONLY)


def permission_args(agent: str) -> list[str]:
    p = policy_for(agent)
    return ["--permission-mode", p.permission_mode, "--allowedTools", *p.tools]


def policy_summary(agent: str) -> dict[str, object]:
    p = policy_for(agent)
    return {"tools": list(p.tools), "permission_mode": p.permission_mode,
            "can_edit": p.can_edit}


# ---------------------------------------------------------------- environment
ENV_ALLOWLIST = (
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
    "XDG_CACHE_HOME",
)

# Explicit deny list for defence in depth: even if added to the allowlist later,
# these never reach an agent.
ENV_DENY_PREFIXES = ("AWS_", "GCP_", "AZURE_", "GOOGLE_", "SSH_", "GITHUB_", "GH_",
                     "GITLAB_", "DOCKER_", "KUBECONFIG", "DATABASE_", "PG", "REDIS_",
                     "TEAM_", "OPENAI_", "HF_", "HUGGINGFACE_")
ENV_DENY_EXACT = frozenset({"SSH_AUTH_SOCK", "AWS_PROFILE", "HOME"})


def build_agent_env(workspace: Path, extra: Iterable[str] = ()) -> dict[str, str]:
    """Sanitized environment for one agent process.

    HOME is a private directory inside the workspace, so the CLI can keep its
    own config while the user's real home (SSH keys, dotfiles, browser data) is
    never the agent's home. Credentials must come from ANTHROPIC_* variables.
    """
    allowed = tuple(ENV_ALLOWLIST) + tuple(extra)
    env: dict[str, str] = {}
    for key in allowed:
        val = os.environ.get(key)
        if val is None or key in ENV_DENY_EXACT:
            continue
        if any(key == p or key.startswith(p) for p in ENV_DENY_PREFIXES):
            continue
        env[key] = val

    home = Path(workspace) / ".home"
    config = home / ".claude"
    config.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(home, 0o700)
    except OSError:
        pass
    env["HOME"] = str(home)
    env["CLAUDE_CONFIG_DIR"] = str(config)
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return env


# ---------------------------------------------------------------- containment
def check_within(candidate: str | Path, roots: Sequence[str | Path],
                 what: str = "path") -> Path:
    """Resolve `candidate` and require it to be equal to, or inside, a root.

    Uses path components, not string prefixes: a sibling like `/tmp/ws_evil`
    is not inside `/tmp/ws`, and symlinks are resolved before the check.
    """
    resolved = Path(candidate).expanduser().resolve()
    for root in roots:
        rr = Path(root).expanduser().resolve()
        if resolved == rr or resolved.is_relative_to(rr):
            return resolved
    raise PermissionError(f"{what} escapes allowed roots: {candidate}")


# ---------------------------------------------------------------- sandbox
class SandboxUnavailable(RuntimeError):
    pass


SANDBOX_MODES = ("auto", "required", "off")
_probe_cache: dict[str, object] = {}


def sandbox_binary() -> str | None:
    return shutil.which("bwrap")


def sandbox_works() -> tuple[bool, str]:
    """Cheap, cached capability probe (no network, no agent)."""
    if "result" in _probe_cache:
        return _probe_cache["result"]  # type: ignore[return-value]
    exe = sandbox_binary()
    if not exe:
        _probe_cache["result"] = (False, "bwrap not installed")
        return _probe_cache["result"]  # type: ignore[return-value]
    try:
        proc = subprocess.run(
            [exe, "--unshare-all", "--die-with-parent", "--ro-bind", "/", "/", "--", "/bin/true"],
            capture_output=True, text=True, timeout=10,
        )
        ok = proc.returncode == 0
        reason = "" if ok else (proc.stderr.strip()[:200] or f"exit {proc.returncode}")
    except Exception as e:  # noqa: BLE001
        ok, reason = False, f"{type(e).__name__}: {e}"
    _probe_cache["result"] = (ok, reason)
    return _probe_cache["result"]  # type: ignore[return-value]


@dataclass
class SandboxPlan:
    enabled: bool
    mode: str
    reason: str = ""
    argv: list[str] = field(default_factory=list)
    details: dict[str, object] = field(default_factory=dict)


_SYSTEM_RO_BINDS = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc")


def plan_sandbox(argv: Sequence[str], workspace: Path,
                 mode: str | None = None) -> SandboxPlan:
    """Wrap `argv` so only the workspace is writable and home is empty."""
    mode = (mode or os.environ.get("TEAM_AGENT_SANDBOX") or "auto").lower()
    if mode not in SANDBOX_MODES:
        mode = "auto"
    ws = Path(workspace).resolve()
    if mode == "off":
        return SandboxPlan(False, mode, "disabled by TEAM_AGENT_SANDBOX=off",
                           list(argv), {"filesystem": "none"})
    ok, reason = sandbox_works()
    if not ok:
        if mode == "required":
            raise SandboxUnavailable(reason)
        return SandboxPlan(False, mode, reason, list(argv),
                           {"filesystem": "none", "reason": reason})

    cmd = [sandbox_binary() or "bwrap", "--unshare-all", "--share-net",
           "--die-with-parent", "--new-session",
           "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for p in _SYSTEM_RO_BINDS:
        if Path(p).exists():
            cmd += ["--ro-bind", p, p]
    # Hide the real home directory entirely, then expose only the toolchain
    # directories the CLI needs (its install prefix and its own directory).
    cmd += ["--tmpfs", "/home"]
    for p in _toolchain_dirs(argv[0]):
        cmd += ["--ro-bind", p, p]
    cmd += ["--bind", str(ws), str(ws)]
    cmd += ["--chdir", str(ws)]
    cmd += ["--setenv", "HOME", str(ws / ".home")]
    cmd += list(argv)
    return SandboxPlan(True, mode, "", cmd,
                       {"filesystem": "bwrap", "writable": str(ws),
                        "home": str(ws / ".home"), "network": "shared"})


def _toolchain_dirs(executable: str) -> list[str]:
    dirs: list[str] = []
    exe = Path(executable)
    if not exe.is_absolute():
        return dirs
    # Follow the symlink chain by hand: `Path.resolve()` collapses to the final
    # target, but execvp resolves every hop, so each hop's directory must exist
    # inside the sandbox (venvs commonly point into a uv-managed interpreter).
    candidates: list[Path] = []
    hops: list[Path] = [exe]
    for _ in range(6):
        try:
            link = hops[-1].readlink() if hops[-1].is_symlink() else None
        except OSError:
            link = None
        if link is None:
            break
        nxt = link if link.is_absolute() else hops[-1].parent / link
        hops.append(nxt)
    for hop in hops:
        candidates.append(hop.parent)
        if hop.parent.name == "bin":
            candidates.append(hop.parent.parent)
    for cand in candidates:
        s = str(cand)
        if cand.is_symlink():
            continue  # bind the real target, never a symlinked directory
        if Path(s).is_dir() and s not in dirs and s not in ("/", "/home", "/tmp"):
            dirs.append(s)
        # Keep the sibling target of a venv symlink reachable too.
        parts = Path(s).parts
        if ".local" in parts and "share" in parts:
            i = parts.index(".local")
            root = Path(*parts[:i + 3])
            rs = str(root)
            if root.is_dir() and rs not in dirs:
                dirs.append(rs)
    return dirs
