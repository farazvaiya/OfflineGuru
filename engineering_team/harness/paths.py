"""Canonical paths for the engineering_team workspace."""

from __future__ import annotations

import os
from pathlib import Path

# Repo root assumed as parent of engineering_team package
REPO_ROOT = Path(__file__).resolve().parents[2]
# ENGINEERING_TEAM_ROOT lets a run use a scratch workspace (tests, demos) without
# touching the live harness state.
TEAM_ROOT = Path(os.environ.get("ENGINEERING_TEAM_ROOT") or (REPO_ROOT / "engineering_team"))


def ensure_workspace() -> None:
    for d in [
        TEAM_ROOT / "config",
        TEAM_ROOT / "agents",
        TEAM_ROOT / "harness",
        TEAM_ROOT / "state",
        TEAM_ROOT / "tasks",
        TEAM_ROOT / "experiments",
        TEAM_ROOT / "benchmarks",
        TEAM_ROOT / "reports",
        TEAM_ROOT / "checkpoints",
        TEAM_ROOT / "logs",
        TEAM_ROOT / "evaluations",
        TEAM_ROOT / "datasets",
    ]:
        d.mkdir(parents=True, exist_ok=True)


# --- Call-time path resolution -------------------------------------------------
# The constants above are bound once at import. Any code that writes state must use
# these functions instead: they re-read TEAM_ROOT on every call, so a relocated
# workspace (ENGINEERING_TEAM_ROOT, tests, demos) is honoured and an import that
# happened before relocation cannot pin a stale production path.

def team_root() -> Path:
    return Path(TEAM_ROOT)


def db_path() -> Path:
    return team_root() / "state" / "engineering_team.db"


def workflow_path() -> Path:
    return team_root() / "state" / "workflow.json"


def decisions_path() -> Path:
    return team_root() / "state" / "decisions.json"


def signoffs_path() -> Path:
    return team_root() / "state" / "signoffs.json"


def log_path() -> Path:
    return team_root() / "logs" / "harness.log"


def decision_report_path() -> Path:
    return team_root() / "reports" / "DECISION_REPORT.md"


def integration_report_path() -> Path:
    return team_root() / "reports" / "INTEGRATION_REPORT.md"


def agent_workspace_root() -> Path:
    return team_root() / "state" / "agent_workspaces"


def agent_log_dir() -> Path:
    return team_root() / "logs" / "agent_runs"


def agents_md_path() -> Path:
    """Role definitions are source, not state: they always live with the package."""
    return Path(__file__).resolve().parent.parent / "agents" / "AGENTS.md"


# Deprecated import-time aliases. Nothing inside the harness reads these; they exist
# only so external scripts that imported them keep working.
DB_PATH = TEAM_ROOT / "state" / "engineering_team.db"
WORKFLOW_PATH = TEAM_ROOT / "state" / "workflow.json"
DECISIONS_PATH = TEAM_ROOT / "state" / "decisions.json"
SIGNOFFS_PATH = TEAM_ROOT / "state" / "signoffs.json"
LOG_PATH = TEAM_ROOT / "logs" / "harness.log"
DECISION_REPORT_PATH = TEAM_ROOT / "reports" / "DECISION_REPORT.md"
INTEGRATION_REPORT_PATH = TEAM_ROOT / "reports" / "INTEGRATION_REPORT.md"
