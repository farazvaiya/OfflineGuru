"""Report generation: decision reports and integration reports."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import paths as _paths


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _fmt_json(value: Any) -> str:
    try:
        if isinstance(value, str):
            value = json.loads(value)
    except Exception:
        pass
    return "```json\n" + json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n```"


def generate_decision_report(
    problem: dict[str, Any],
    root_cause: str,
    track_a: dict[str, Any] | None,
    track_b: dict[str, Any] | None,
    hybrid: dict[str, Any] | None,
    benchmark: dict[str, Any],
    evaluation: dict[str, Any],
    recommendation: str,
    uncertainties: list[str] | None = None,
    out_path: Path | None = None,
) -> Path:
    _paths.ensure_workspace()
    out_path = Path(out_path) if out_path else _paths.decision_report_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    uncertainties = uncertainties or []
    lines = [
        "# DECISION REPORT",
        "",
        f"- Generated: {_now()}",
        f"- Problem ID: {problem.get('id')}",
        "",
        "## 1. Problem description",
        problem.get("title", "(unknown)"),
        "",
        problem.get("description", ""),
        "",
        "## 2. Root cause",
        root_cause or "(not yet determined)",
        "",
        "## 3. Track A implementation (Original Engineering Solution)",
        _fmt_json(track_a or {"status": "not available"}),
        "",
        "## 4. Track B open-source project and license",
        _fmt_json(track_b or {"status": "not available", "note": "No suitable open-source project identified or evaluated."}),
        "",
        "## 5. Benchmark methodology",
        _fmt_json(benchmark.get("methodology", {"note": "Not recorded"})),
        "",
        "## 6. Actual benchmark results",
        _fmt_json(benchmark.get("results", {"note": "Not recorded"})),
        "",
        "## 7. Accuracy comparison",
        _fmt_json(benchmark.get("accuracy", {"note": "Not recorded"})),
        "",
        "## 8. Latency comparison",
        _fmt_json(benchmark.get("latency", {"note": "Not recorded"})),
        "",
        "## 9. CPU/RAM comparison",
        _fmt_json(benchmark.get("resources", {"note": "Not recorded"})),
        "",
        "## 10. Reliability comparison",
        _fmt_json(benchmark.get("reliability", {"note": "Not recorded"})),
        "",
        "## 11. Compatibility and maintenance risks",
        _fmt_json(benchmark.get("risks", {"note": "Not recorded"})),
        "",
        "## 12. Independent evaluator findings",
        _fmt_json(evaluation),
        "",
        "## 13. AI recommendation",
        recommendation or "(no recommendation)",
        "",
        "## 14. Remaining uncertainties",
    ]
    if uncertainties:
        for u in uncertainties:
            lines.append(f"- {u}")
    else:
        lines.append("- None recorded.")
    if hybrid:
        lines += ["", "## Optional Hybrid candidate", _fmt_json(hybrid)]
    lines += [
        "",
        "## Human decision required",
        "Supported commands:",
        "",
        "- `APPROVE_A`",
        "- `APPROVE_B`",
        "- `APPROVE_HYBRID`",
        "- `REJECT_BOTH`",
        "- `REQUEST_MORE_TESTS`",
        "",
        "The AI team recommends but does not select. No merge/integration without explicit approval.",
    ]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def generate_integration_report(result: dict[str, Any], out_path: Path | None = None) -> Path:
    _paths.ensure_workspace()
    out_path = Path(out_path) if out_path else _paths.integration_report_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# INTEGRATION REPORT",
        "",
        f"- Generated: {_now()}",
        "",
        _fmt_json(result),
    ]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
