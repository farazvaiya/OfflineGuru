"""Problem discovery helpers (local, evidence-based)."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from .paths import REPO_ROOT


def quick_repo_scan() -> dict[str, Any]:
    findings: dict[str, Any] = {"root": str(REPO_ROOT), "signals": []}
    # pytest collection sanity (no run) - cheap signal
    try:
        p = subprocess.run(
            ["python3", "-m", "pytest", "--collect-only", "-q"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120, check=False,
        )
        findings["pytest_collect"] = {
            "returncode": p.returncode,
            "stdout_tail": p.stdout.strip().splitlines()[-10:],
            "stderr_tail": p.stderr.strip().splitlines()[-10:],
        }
        if p.returncode != 0:
            findings["signals"].append({"type": "test_collection_issue", "severity": "P2", "evidence": "pytest --collect-only failed"})
    except Exception as e:
        findings["pytest_collect_error"] = str(e)
    # existing harness/workflow signals
    eh_state = REPO_ROOT / "engineering_harness" / "state" / "workflow.json"
    if eh_state.exists():
        try:
            wf = json.loads(eh_state.read_text(encoding="utf-8"))
            findings["existing_engineering_harness"] = {
                "state": wf.get("state"), "phase": wf.get("phase"),
                "gate_results": wf.get("gate_results"),
            }
            gates = wf.get("gate_results") or {}
            for k, v in gates.items():
                if isinstance(v, dict) and v.get("status") == "FAIL":
                    findings["signals"].append({"type": "harness_gate_failure", "severity": "P1", "evidence": f"engineering_harness gate {k} FAIL"})
        except Exception:
            pass
    # docs/reports presence
    findings["has_reports_dir"] = (REPO_ROOT / "reports").is_dir()
    findings["has_engineering_harness"] = (REPO_ROOT / "engineering_harness").is_dir()
    return findings
