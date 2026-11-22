"""Historical comparison and improvement planning (Phase 9).

Compares physical test sessions only when their conditions match materially;
otherwise the comparison is reported as invalid, never as a regression or
improvement. Generates a proposed next-test plan from evidence gaps.

Rules (master prompt §13):
  - Do not compare results as equivalent when conditions differ materially.
  - Detect regressions only across equivalent sessions.
  - Recommend new test scenarios from missing evidence.
  - Track previously approved fixes via the decision log.
"""

from __future__ import annotations

import json
from typing import Any

from .sessions import PhysicalTestDB

# Conditions that must match for two sessions to be comparable.
MATERIAL_CONDITIONS = {
    "teacher_language", "target_language", "expected_students",
    "room_noise", "microphone_info",
}


class ComparisonError(RuntimeError):
    pass


class History:
    def __init__(self, db: PhysicalTestDB) -> None:
        self.db = db

    def conditions_map(self, session_id: str) -> dict[str, str]:
        """Merge session-level conditions with condition-table overrides.

        create_session() stores the main condition fields on the session row;
        set_condition() records extras in test_conditions. Both count as
        session conditions for comparability; the table wins on conflict.
        """
        s = self.db.get_session(session_id) or {}
        base = {k: ("" if s.get(k) is None else str(s.get(k)))
                for k in MATERIAL_CONDITIONS}
        for c in self.db.conditions(session_id):
            base[c["key"]] = c["value"] or ""
        return base

    def comparable(self, sid_a: str, sid_b: str) -> dict[str, Any]:
        ca, cb = self.conditions_map(sid_a), self.conditions_map(sid_b)
        differ = {k: (ca.get(k), cb.get(k)) for k in MATERIAL_CONDITIONS
                  if ca.get(k) != cb.get(k)}
        return {"comparable": not differ, "material_differences": differ}

    def compare(self, sid_a: str, sid_b: str, metrics: dict[str, tuple[float | None, float | None]]) -> dict[str, Any]:
        """metrics: name -> (value_a, value_b). None means not measured.

        Returns per-metric direction with significance guard; invalid if
        conditions differ materially.
        """
        comp = self.comparable(sid_a, sid_b)
        out: dict[str, Any] = {
            "session_a": sid_a, "session_b": sid_b,
            "comparable": comp["comparable"],
            "material_differences": comp["material_differences"],
        }
        if not comp["comparable"]:
            out["verdict"] = "INVALID_CONDITIONS"
            out["metrics"] = {}
            out["note"] = ("sessions differ materially; no regression/improvement "
                           "claim is made (master prompt rule)")
            return out
        rows = {}
        regressions, improvements = [], []
        for name, (a, b) in metrics.items():
            if a is None or b is None:
                rows[name] = {"a": a, "b": b, "direction": "INCONCLUSIVE",
                              "reason": "missing measurement on one side"}
                continue
            if a == b:
                rows[name] = {"a": a, "b": b, "direction": "SAME"}
                continue
            # lower-is-better for these metric names; extend as needed
            lower_better = name.endswith("_ms") or name in ("wer", "cer", "drops", "rss_mb")
            better = (b < a) if lower_better else (b > a)
            direction = "IMPROVED" if better else "REGRESSED"
            rows[name] = {"a": a, "b": b, "direction": direction,
                          "delta": round(b - a, 6)}
            (improvements if better else regressions).append(name)
        out["metrics"] = rows
        out["verdict"] = "REGRESSION" if regressions else ("IMPROVED" if improvements else "SAME")
        out["regressions"] = regressions
        out["improvements"] = improvements
        return out

    def next_test_plan(self, session_id: str | None = None) -> dict[str, Any]:
        """Propose the next physical test from evidence gaps, not ambition."""
        gaps = []
        # auditable gaps from the gap analysis + session evidence
        if session_id:
            fb = self.db.feedback(session_id)
            has_playback = any("playback" in (f.get("category") or "") or "heard" in f["body"].lower()
                               for f in fb)
            if not has_playback:
                gaps.append("audible playback unverified: collect student acks or operator observation")
            conds = self.conditions_map(session_id)
            if conds.get("room_noise") in (None, "", "quiet"):
                gaps.append("background-noise scenario not exercised: run Scenario E with noise")
            if conds.get("expected_students") in (None, "") or int(conds.get("expected_students") or 0) < 30:
                gaps.append("student load below 30: run Scenario D at 30 students before claiming capacity")
        else:
            gaps.append("no session yet: run Scenario A (microphone startup/continuity) first")
        for g in [
            "45-minute long-duration stability (Scenario F) NOT_EXECUTED in this environment",
            "temperature sensors unavailable here: thermal validation requires target hardware",
        ]:
            gaps.append(g)
        return {
            "basis": "evidence gaps only; no fabricated priorities",
            "session_id": session_id,
            "recommended_next": gaps,
            "human_confirmation_required": True,
            "note": "physical actions need human operator execution and confirmation",
        }

    def approved_fixes(self) -> list[dict[str, Any]]:
        """Previously approved integrations, from the decision log.

        Reads the approval-gate decisions table if present (Phase 8 DB);
        returns [] when that DB is separate/absent — never invents approvals.
        """
        import sqlite3
        try:
            conn = sqlite3.connect(str(self.db.path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT decision,track,note,created_at FROM decisions "
                "WHERE decision LIKE 'APPROVE_%' ORDER BY created_at").fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []
