"""Configuration loader for the engineering team."""

from __future__ import annotations

from typing import Any

from .paths import TEAM_ROOT

try:
    import tomllib
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore


def load_team_config() -> dict[str, Any]:
    path = TEAM_ROOT / "config" / "team.toml"
    if not path.exists() or tomllib is None:
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)
