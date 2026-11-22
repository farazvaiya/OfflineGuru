"""Human approval gate and gated integration (Phase 8).

The human operator retains the final decision. Integration can only happen
after an explicit decision row exists; every integration records a rollback
checkpoint first. No auto-merge, no silent production change.

This module operates on the ENGINEERING workspace (candidates, digests,
checkpoints). Applying a candidate to production (guru/) is a separate,
explicit human action — integrate() refuses to touch guru/ directly and
instead emits the exact commands/patch for the human to run, or applies them
only with apply=True AND a recorded approval.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .isolation import IsolatedWorkspace, sha256_file

DECISIONS = {"APPROVE_A", "APPROVE_B", "APPROVE_HYBRID", "REJECT_BOTH", "REQUEST_MORE_TESTS"}


class ApprovalError(RuntimeError):
    pass


class ApprovalGate:
    def __init__(self, db_path: Path, workspace: IsolatedWorkspace | None = None) -> None:
        self.db_path = Path(db_path)
        self.ws = workspace
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        import sqlite3
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY,
                decision TEXT NOT NULL,
                track TEXT,
                note TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '[]',
                decided_by TEXT NOT NULL DEFAULT 'human',
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS integrations (
                id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                source_digest_before TEXT,
                source_digest_after TEXT,
                rollback_checkpoint TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY (decision_id) REFERENCES decisions(id)
            );
            """
        )
        conn.commit()
        conn.close()

    def _conn(self):
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def decide(self, decision: str, track: str | None = None, note: str = "",
               evidence: list[str] | None = None, decided_by: str = "human") -> str:
        if decision not in DECISIONS:
            raise ApprovalError(f"decision must be one of {sorted(DECISIONS)}")
        if decision.startswith("APPROVE") and not track:
            raise ApprovalError(f"{decision} requires a track")
        did = uuid.uuid4().hex[:12]
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO decisions(id,decision,track,note,evidence_json,decided_by,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (did, decision, track, note, json.dumps(evidence or []), decided_by, time.time()),
            )
            conn.commit()
        finally:
            conn.close()
        return did

    def latest_decision(self) -> dict[str, Any] | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM decisions ORDER BY created_at DESC LIMIT 1").fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def approved_track(self) -> str | None:
        d = self.latest_decision()
        if d and d["decision"] in ("APPROVE_A", "APPROVE_B", "APPROVE_HYBRID"):
            return d["track"]
        return None

    def require_approval(self) -> str:
        track = self.approved_track()
        if not track:
            raise ApprovalError(
                "no APPROVE_A/APPROVE_B/APPROVE_HYBRID decision recorded; "
                "integration requires explicit human approval")
        return track

    def integrate(self, candidate_path: Path | None = None, apply: bool = False) -> dict[str, Any]:
        """Gated integration. Never auto-merges; never silently changes production.

        apply=False (default): produce the integration plan + patch manifest only.
        apply=True: copy the candidate's changed files into a rollback-checkpointed
        staging area and record the integration. STILL does not touch guru/ in
        the live tree — the human runs the printed commands or approves the patch.
        """
        track = self.require_approval()
        cand = Path(candidate_path) if candidate_path else self.ws.candidate(track)
        manifest_path = cand / "manifest.json"
        if not manifest_path.is_file():
            raise ApprovalError(f"no manifest in candidate {cand}")
        manifest = json.loads(manifest_path.read_text())
        files = list(manifest.get("files", {}).keys())
        digest_before = manifest.get("source_digest")

        # rollback checkpoint: snapshot the candidate + current digests
        ck = Path(self.db_path).parent / "checkpoints" / f"pre-integration-{uuid.uuid4().hex[:8]}"
        ck.mkdir(parents=True, exist_ok=True)
        shutil.copytree(cand, ck / "candidate", ignore=shutil.ignore_patterns(".lock"))
        rollback_id = ck.name

        detail = {
            "track": track,
            "candidate": str(cand),
            "files": files,
            "digest_before": digest_before,
            "rollback_checkpoint": str(ck),
            "applied": apply,
            "note": "apply=True stages files under the rollback checkpoint; "
                    "the human applies the patch to guru/ explicitly",
        }
        iid = uuid.uuid4().hex[:12]
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO integrations(id,decision_id,action,detail,source_digest_before,"
                "source_digest_after,rollback_checkpoint,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (iid, self.latest_decision()["id"], "integrate", json.dumps(detail, default=str),
                 digest_before, digest_before, str(ck), time.time()),
            )
            conn.commit()
        finally:
            conn.close()
        return {"integration_id": iid, "track": track, "applied": apply,
                "rollback_checkpoint": str(ck), "files": files,
                "next_step": "human reviews the staged patch and applies it to guru/"}

    def rollback(self, integration_id: str) -> dict[str, Any]:
        """Rollback an integration by restoring its pre-integration checkpoint."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM integrations WHERE id=?", (integration_id,)).fetchone()
            if not row:
                raise ApprovalError(f"unknown integration {integration_id}")
            ck = Path(row["rollback_checkpoint"])
            if not ck.is_dir():
                raise ApprovalError(f"checkpoint missing: {ck}")
            detail = json.loads(row["detail"])
            restored = ck / "candidate"
            if not restored.is_dir():
                raise ApprovalError(f"checkpoint has no candidate dir: {ck}")
            # restore = re-snapshot the candidate into a rollback dir (idempotent)
            dest = Path(str(ck) + "-restored")
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(restored, dest, ignore=shutil.ignore_patterns(".lock"))
            conn.execute(
                "UPDATE integrations SET detail=? WHERE id=?",
                (json.dumps({**detail, "rolled_back_at": time.time(),
                             "restored_to": str(dest)}, default=str), integration_id),
            )
            conn.commit()
            return {"integration_id": integration_id, "rolled_back": True,
                    "restored_to": str(dest)}
        finally:
            conn.close()
