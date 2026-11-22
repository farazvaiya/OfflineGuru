"""Local SQLite storage for sessions, transcripts, materials and answers.

Everything lives in one file on the teacher's laptop.  There is no cloud
component and no telemetry.  Student-visible text is stored as submitted and is
always treated as untrusted data by the API layer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    teacher_token TEXT NOT NULL,
    teacher_token_hash TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    source_lang TEXT NOT NULL DEFAULT 'en',
    target_lang TEXT NOT NULL DEFAULT 'bn',
    created_at REAL NOT NULL,
    ended_at REAL
);
CREATE TABLE IF NOT EXISTS segments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    start_s REAL NOT NULL,
    end_s REAL NOT NULL,
    en TEXT NOT NULL,
    bn TEXT NOT NULL DEFAULT '',
    confidence REAL DEFAULT 0,
    asr_ms REAL DEFAULT 0,
    translate_ms REAL DEFAULT 0,
    tts_ms REAL DEFAULT 0,
    total_ms REAL DEFAULT 0,
    numbers_preserved INTEGER DEFAULT 1,
    audio_path TEXT,
    audio_status TEXT DEFAULT 'pending',
    segment_status TEXT DEFAULT 'final',
    translation_at REAL DEFAULT 0,
    source_language TEXT NOT NULL DEFAULT 'en',
    target_language TEXT NOT NULL DEFAULT 'bn',
    source_text TEXT NOT NULL DEFAULT '',
    translated_text TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_segments_session ON segments(session_id, seq);
CREATE TABLE IF NOT EXISTS materials (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    topic TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(session_id, kind)
);
CREATE TABLE IF NOT EXISTS students (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    name TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL,
    token_hash TEXT,
    joined_at REAL NOT NULL,
    last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS answers (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    quiz_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    answer TEXT NOT NULL,
    is_correct INTEGER,
    score REAL,
    feedback TEXT,
    needs_review INTEGER DEFAULT 0,
    teacher_override INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    UNIQUE(quiz_id, student_id, question_id)
);
CREATE TABLE IF NOT EXISTS topic_stats (
    session_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    correct INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (session_id, student_id, topic)
);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    kind TEXT NOT NULL,
    value REAL NOT NULL,
    meta_json TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_kind ON metrics(kind, session_id, created_at);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    pages INTEGER NOT NULL,
    chars INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(session_id, sha256)
);
CREATE TABLE IF NOT EXISTS doc_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    page INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_session ON doc_chunks(session_id, document_id, page);
"""


def _now() -> float:
    return time.time()


def _strip(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    for key in keys:
        row.pop(key, None)
    return row


def hash_token(token: str) -> str:
    """Digest of a high-entropy bearer token.

    Tokens here are 32 random bytes, so a fast hash is the right choice: there is
    nothing to guess. Passwords would need a slow KDF instead.
    """
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


# Credentials are never returned to callers; the API only needs a match result.
_SESSION_SECRETS = ("teacher_token", "teacher_token_hash")
_STUDENT_SECRETS = ("token", "token_hash")


# Columns a segment update may touch. Single source of truth for both the batched
# writer and the two thin per-column wrappers kept for compatibility.
_SEGMENT_UPDATABLE = (
    "en", "bn", "confidence", "asr_ms", "translate_ms", "tts_ms",
    "total_ms", "numbers_preserved", "audio_path",
    "audio_status", "segment_status", "translation_at",
    "source_language", "target_language", "source_text", "translated_text",
)


class Storage:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.commit()
        self._migrate_token_hashes()
        self._migrate_document_columns()
        self._migrate_segment_columns()

    def _migrate_token_hashes(self) -> None:
        """Move any plaintext credential from the older schema into a hash.

        Runs once per database: rows written before the hash columns existed keep
        working, because the same token still hashes to the stored digest. The
        legacy NOT NULL/UNIQUE column is filled with the digest, so no schema
        rebuild (and no data loss) is needed and no secret remains on disk.
        """
        with self._lock:
            for table, column in (("sessions", "teacher_token_hash"), ("students", "token_hash")):
                cols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
                if column not in cols:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
            migrated = 0
            for table, legacy, hashed in (
                ("sessions", "teacher_token", "teacher_token_hash"),
                ("students", "token", "token_hash"),
            ):
                rows = self._conn.execute(
                    f"SELECT id, {legacy} AS value FROM {table}"
                    f" WHERE {hashed} IS NULL OR {hashed}=''"
                ).fetchall()
                for row in rows:
                    value = row["value"] or ""
                    if not value:
                        continue
                    digest = hash_token(value)
                    self._conn.execute(
                        f"UPDATE {table} SET {hashed}=?, {legacy}=? WHERE id=?",
                        (digest, digest, row["id"]),
                    )
                    migrated += 1
            self._conn.commit()
        if migrated:
            log.info(
                "credential migration: hashed %d stored token(s); plaintext is no longer persisted",
                migrated,
            )

    def _migrate_segment_columns(self) -> None:
        """Add the real-time status columns to a database created before them."""
        wanted = {
            "audio_status": "TEXT DEFAULT 'pending'",
            "segment_status": "TEXT DEFAULT 'final'",
            "translation_at": "REAL DEFAULT 0",
            "source_language": "TEXT NOT NULL DEFAULT 'en'",
            "target_language": "TEXT NOT NULL DEFAULT 'bn'",
            "source_text": "TEXT NOT NULL DEFAULT ''",
            "translated_text": "TEXT NOT NULL DEFAULT ''",
        }
        with self._lock:
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(segments)")}
            added = False
            for name, decl in wanted.items():
                if name not in cols:
                    self._conn.execute(f"ALTER TABLE segments ADD COLUMN {name} {decl}")
                    added = True
            if added:
                self._conn.commit()

    def _migrate_document_columns(self) -> None:
        """Add the optional Bangla chunk column to a database created before it."""
        with self._lock:
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(doc_chunks)")}
            if "text_bn" not in cols:
                self._conn.execute("ALTER TABLE doc_chunks ADD COLUMN text_bn TEXT")
                self._conn.commit()

    # -- sessions --------------------------------------------------------
    def create_session(self, title: str, teacher_token: str, code: str,
                       source_lang: str = "en", target_lang: str = "bn") -> str:
        sid = uuid.uuid4().hex[:12]
        digest = hash_token(teacher_token)
        with self._lock:
            # The legacy column keeps the digest, not the token: NOT NULL stays
            # satisfied and a reader of the DB file cannot act as the teacher.
            self._conn.execute(
                "INSERT INTO sessions (id, code, title, teacher_token, teacher_token_hash,"
                " source_lang, target_lang, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (sid, code, title, digest, digest, source_lang, target_lang, _now()),
            )
            self._conn.commit()
        return sid

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return _strip(dict(row), _SESSION_SECRETS) if row else None

    def get_session_by_code(self, code: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE code=? AND status='active' ORDER BY created_at DESC LIMIT 1",
                (code.upper().strip(),),
            ).fetchone()
        return _strip(dict(row), _SESSION_SECRETS) if row else None

    def teacher_token_matches(self, session_id: str, token: str | None) -> bool:
        """Constant-time check of a teacher token against the stored digest."""
        if not token:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT teacher_token_hash FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
        stored = (row["teacher_token_hash"] or "") if row else ""
        return bool(stored) and secrets.compare_digest(hash_token(token), stored)

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, code, title, status, created_at, ended_at FROM sessions"
                " ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def end_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET status='ended', ended_at=? WHERE id=?", (_now(), session_id)
            )
            self._conn.commit()

    # -- segments --------------------------------------------------------
    def next_seq(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM segments WHERE session_id=?",
                (session_id,),
            ).fetchone()
        return int(row["n"])

    def add_segment(self, session_id: str, **fields: Any) -> dict[str, Any]:
        seg_id = fields.pop("id", None) or uuid.uuid4().hex[:12]
        seq = fields.pop("seq", None) or self.next_seq(session_id)
        cols = {
            "id": seg_id,
            "session_id": session_id,
            "seq": seq,
            "start_s": float(fields.get("start_s", 0)),
            "end_s": float(fields.get("end_s", 0)),
            "en": fields.get("en", ""),
            "bn": fields.get("bn", ""),
            "confidence": float(fields.get("confidence", 0) or 0),
            "asr_ms": float(fields.get("asr_ms", 0) or 0),
            "translate_ms": float(fields.get("translate_ms", 0) or 0),
            "tts_ms": float(fields.get("tts_ms", 0) or 0),
            "total_ms": float(fields.get("total_ms", 0) or 0),
            "numbers_preserved": 1 if fields.get("numbers_preserved", True) else 0,
            "audio_path": fields.get("audio_path"),
            "created_at": _now(),
            "source_language": fields.get("source_language", "en"),
            "target_language": fields.get("target_language", "bn"),
            "source_text": fields.get("source_text", fields.get("en", "")),
            "translated_text": fields.get("translated_text", ""),
        }
        with self._lock:
            self._conn.execute(
                f"INSERT INTO segments ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                tuple(cols.values()),
            )
            self._conn.commit()
        return cols

    def update_segment(self, segment_id: str, **fields: Any) -> bool:
        """Write any subset of segment columns in one transaction (one commit).

        Replaces a chain of per-column UPDATE+commit calls on the hot path; the
        shared connection keeps its single lock, so this stays serialised.
        """
        sets, vals = [], []
        for key in _SEGMENT_UPDATABLE:
            if key not in fields or fields[key] is None:
                continue
            value = fields[key]
            sets.append(f"{key}=?")
            vals.append(int(value) if key == "numbers_preserved" else value)
        if not sets:
            return False
        vals.append(segment_id)
        with self._lock:
            cur = self._conn.execute(f"UPDATE segments SET {', '.join(sets)} WHERE id=?", vals)
            self._conn.commit()
            return cur.rowcount > 0

    def update_segment_text(self, segment_id: str, en: str | None = None, bn: str | None = None) -> bool:
        return self.update_segment(segment_id, en=en, bn=bn)

    def update_segment_metrics(self, segment_id: str, **fields: Any) -> None:
        self.update_segment(segment_id, **fields)

    def override_answer(self, answer_id: str, is_correct: bool) -> dict[str, Any] | None:
        """Teacher correction of an automatically graded answer."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM answers WHERE id=?", (answer_id,)).fetchone()
            if not row:
                return None
            self._conn.execute(
                "UPDATE answers SET is_correct=?, teacher_override=1, needs_review=0 WHERE id=?",
                (1 if is_correct else 0, answer_id),
            )
            self._conn.commit()
            updated = self._conn.execute("SELECT * FROM answers WHERE id=?", (answer_id,)).fetchone()
        return dict(updated) if updated else None

    def get_segment(self, segment_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
        return dict(row) if row else None

    def list_segments(self, session_id: str, after_seq: int = -1, limit: int = 5000) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM segments WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?",
                (session_id, after_seq, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def segment_count(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM segments WHERE session_id=?", (session_id,)
            ).fetchone()
        return int(row["n"])

    # -- materials -------------------------------------------------------
    def upsert_material(self, session_id: str, kind: str, content: dict[str, Any],
                        topic: str | None = None, status: str = "draft") -> str:
        mid = uuid.uuid4().hex[:12]
        now = _now()
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM materials WHERE session_id=? AND kind=?", (session_id, kind)
            ).fetchone()
            payload = json.dumps(content, ensure_ascii=False)
            if existing:
                mid = existing["id"]
                self._conn.execute(
                    "UPDATE materials SET content_json=?, status=?, topic=?, updated_at=? WHERE id=?",
                    (payload, status, topic, now, mid),
                )
            else:
                self._conn.execute(
                    "INSERT INTO materials (id, session_id, kind, content_json, status, topic, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (mid, session_id, kind, payload, status, topic, now, now),
                )
            self._conn.commit()
        return mid

    def get_material(self, session_id: str, kind: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM materials WHERE session_id=? AND kind=?", (session_id, kind)
            ).fetchone()
        if not row:
            return None
        out = dict(row)
        out["content"] = json.loads(out.pop("content_json"))
        return out

    def set_material_status(self, session_id: str, kind: str, status: str,
                            content: dict[str, Any] | None = None) -> bool:
        with self._lock:
            if content is not None:
                cur = self._conn.execute(
                    "UPDATE materials SET status=?, content_json=?, updated_at=? WHERE session_id=? AND kind=?",
                    (status, json.dumps(content, ensure_ascii=False), _now(), session_id, kind),
                )
            else:
                cur = self._conn.execute(
                    "UPDATE materials SET status=?, updated_at=? WHERE session_id=? AND kind=?",
                    (status, _now(), session_id, kind),
                )
            self._conn.commit()
            return cur.rowcount > 0

    # -- students --------------------------------------------------------
    def add_student(self, session_id: str, name: str) -> dict[str, Any]:
        sid = uuid.uuid4().hex[:12]
        token = secrets.token_urlsafe(24)
        digest = hash_token(token)
        now = _now()
        with self._lock:
            # Only the digest is stored (twice: once for the legacy UNIQUE column).
            self._conn.execute(
                "INSERT INTO students (id, session_id, name, token, token_hash, joined_at, last_seen)"
                " VALUES (?,?,?,?,?,?,?)",
                (sid, session_id, name, digest, digest, now, now),
            )
            self._conn.commit()
        return {"id": sid, "name": name, "token": token, "session_id": session_id}

    def student_by_token(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM students WHERE token_hash=?", (hash_token(token),)
            ).fetchone()
        return _strip(dict(row), _STUDENT_SECRETS) if row else None

    def touch_student(self, student_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE students SET last_seen=? WHERE id=?", (_now(), student_id))
            self._conn.commit()

    def list_students(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, joined_at, last_seen FROM students WHERE session_id=? ORDER BY joined_at",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- documents -------------------------------------------------------
    def add_document(self, session_id: str, name: str, kind: str, sha256: str,
                     pages: int, chunks: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        """Insert a document and its page-tagged chunks in one transaction.

        Returns ``(document, created)``; the same bytes uploaded twice in one
        session return the existing row instead of duplicating its chunks.
        """
        existing = self.get_document_by_hash(session_id, sha256)
        if existing:
            return existing, False
        doc_id = uuid.uuid4().hex[:12]
        now = _now()
        rows = [
            (
                uuid.uuid4().hex[:12],
                doc_id,
                session_id,
                int(c["page"]),
                str(c["text"]),
                (str(c["text_bn"]) if c.get("text_bn") else None),
            )
            for c in chunks
        ]
        with self._lock:
            self._conn.execute(
                "INSERT INTO documents (id, session_id, name, kind, pages, chars, sha256, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (doc_id, session_id, name, kind, pages,
                 sum(len(str(c["text"])) for c in chunks), sha256, now),
            )
            self._conn.executemany(
                "INSERT INTO doc_chunks (id, document_id, session_id, page, text, text_bn)"
                " VALUES (?,?,?,?,?,?)",
                rows,
            )
            self._conn.commit()
        return self.get_document(session_id, doc_id) or {}, True

    def get_document(self, session_id: str, document_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, kind, pages, chars, sha256, created_at FROM documents"
                " WHERE session_id=? AND id=?", (session_id, document_id)
            ).fetchone()
        return dict(row) if row else None

    def get_document_by_hash(self, session_id: str, sha256: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, kind, pages, chars, sha256, created_at FROM documents"
                " WHERE session_id=? AND sha256=?", (session_id, sha256)
            ).fetchone()
        return dict(row) if row else None

    def list_documents(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT d.id, d.name, d.kind, d.pages, d.chars, d.created_at,"
                " (SELECT COUNT(*) FROM doc_chunks c WHERE c.document_id=d.id) AS chunks"
                " FROM documents d WHERE d.session_id=? ORDER BY d.created_at",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_document(self, session_id: str, document_id: str) -> int:
        with self._lock:
            self._conn.execute(
                "DELETE FROM doc_chunks WHERE session_id=? AND document_id=?",
                (session_id, document_id),
            )
            cur = self._conn.execute(
                "DELETE FROM documents WHERE session_id=? AND id=?", (session_id, document_id)
            )
            self._conn.commit()
            return cur.rowcount

    def document_chunks(self, session_id: str) -> list[dict[str, Any]]:
        """All chunks of a session with their document name, for retrieval."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id, c.document_id, c.page, c.text, c.text_bn, d.name AS document"
                " FROM doc_chunks c JOIN documents d ON d.id=c.document_id"
                " WHERE c.session_id=? ORDER BY d.created_at, c.page, c.id",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- answers ---------------------------------------------------------
    def save_answer(self, **fields: Any) -> str:
        aid = uuid.uuid4().hex[:12]
        with self._lock:
            self._conn.execute(
                "INSERT INTO answers (id, session_id, quiz_id, student_id, question_id, answer,"
                " is_correct, score, feedback, needs_review, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(quiz_id, student_id, question_id) DO UPDATE SET"
                " answer=excluded.answer, is_correct=excluded.is_correct, score=excluded.score,"
                " feedback=excluded.feedback, needs_review=excluded.needs_review, created_at=excluded.created_at",
                (
                    aid,
                    fields["session_id"],
                    fields["quiz_id"],
                    fields["student_id"],
                    fields["question_id"],
                    fields["answer"],
                    fields.get("is_correct"),
                    fields.get("score"),
                    fields.get("feedback", ""),
                    1 if fields.get("needs_review") else 0,
                    _now(),
                ),
            )
            self._conn.commit()
        return aid

    def get_answer_by_id(self, answer_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM answers WHERE id=?", (answer_id,)).fetchone()
        return dict(row) if row else None

    def get_answer(self, quiz_id: str, student_id: str, question_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM answers WHERE quiz_id=? AND student_id=? AND question_id=?",
                (quiz_id, student_id, question_id),
            ).fetchone()
        return dict(row) if row else None

    def list_answers(self, session_id: str, student_id: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM answers WHERE session_id=?"
        params: list[Any] = [session_id]
        if student_id:
            q += " AND student_id=?"
            params.append(student_id)
        q += " ORDER BY created_at"
        with self._lock:
            rows = self._conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    # -- topic stats -----------------------------------------------------
    def bump_topic(self, session_id: str, student_id: str, topic: str, correct: bool) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO topic_stats (session_id, student_id, topic, attempts, correct, updated_at)"
                " VALUES (?,?,?,1,?,?)"
                " ON CONFLICT(session_id, student_id, topic) DO UPDATE SET"
                " attempts=attempts+1, correct=correct+excluded.correct, updated_at=excluded.updated_at",
                (session_id, student_id, topic, 1 if correct else 0, _now()),
            )
            self._conn.commit()

    def topic_stats(self, session_id: str, student_id: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT topic, SUM(attempts) AS attempts, SUM(correct) AS correct FROM topic_stats WHERE session_id=?"
        params: list[Any] = [session_id]
        if student_id:
            q += " AND student_id=?"
            params.append(student_id)
        q += " GROUP BY topic ORDER BY attempts DESC"
        with self._lock:
            rows = self._conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    # -- metrics ---------------------------------------------------------
    def record_metric(self, kind: str, value: float, session_id: str | None = None,
                      meta: dict[str, Any] | None = None) -> None:
        self.record_metrics([(kind, value, meta)], session_id)

    def record_metrics(
        self,
        items: Iterable[tuple[str, float] | tuple[str, float, dict[str, Any] | None]],
        session_id: str | None = None,
    ) -> None:
        """Insert several metrics in one transaction (one commit per stage).

        Both ``(kind, value)`` and ``(kind, value, meta)`` entries are accepted:
        the streaming pipeline sends the short form, callers that carry context
        send the long one.  Entries are validated before anything is written, so
        a malformed metric raises ``ValueError`` instead of being dropped.
        """
        if not items:
            return
        now = _now()
        rows: list[tuple[Any, ...]] = []
        for item in items:
            if not isinstance(item, (tuple, list)) or not 2 <= len(item) <= 3:
                raise ValueError(
                    f"metric entry must be (kind, value) or (kind, value, meta), got {item!r}"
                )
            kind, value = item[0], item[1]
            meta = item[2] if len(item) == 3 else None
            if not isinstance(kind, str) or not kind.strip():
                raise ValueError(f"metric kind must be a non-empty string, got {kind!r}")
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"metric {kind!r} value must be a number, got {value!r}"
                ) from exc
            if meta is not None and not isinstance(meta, dict):
                raise ValueError(f"metric {kind!r} meta must be a dict or None, got {meta!r}")
            rows.append((session_id, kind, number, json.dumps(meta or {}), now))
        with self._lock:
            self._conn.executemany(
                "INSERT INTO metrics (session_id, kind, value, meta_json, created_at)"
                " VALUES (?,?,?,?,?)",
                rows,
            )
            self._conn.commit()

    # Percentiles come from the most recent samples only: the work per call stays
    # bounded on a table that grows all year, and recent latency is what matters.
    METRIC_WINDOW = 1000

    def metric_summary(self, kind: str, session_id: str | None = None) -> dict[str, float]:
        where, params = "kind=?", [kind]
        if session_id:
            where += " AND session_id=?"
            params.append(session_id)
        with self._lock:
            total = int(self._conn.execute(
                f"SELECT COUNT(*) AS n FROM metrics WHERE {where}", params
            ).fetchone()["n"])
            if not total:
                return {"count": 0}
            vals = [r["value"] for r in self._conn.execute(
                f"SELECT value FROM metrics WHERE {where} ORDER BY created_at DESC LIMIT ?",
                [*params, self.METRIC_WINDOW],
            ).fetchall()]
        vals.sort()

        def pct(p: float) -> float:
            idx = min(len(vals) - 1, max(0, round((p / 100) * (len(vals) - 1))))
            return round(vals[idx], 1)

        return {
            "count": total,
            "mean": round(sum(vals) / len(vals), 1),
            "p50": pct(50),
            "p95": pct(95),
            "max": round(vals[-1], 1),
        }

    # -- retention -------------------------------------------------------
    def purge_session(self, session_id: str, audio_dir: Path | None = None) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for table in ("answers", "topic_stats", "materials", "segments", "students",
                          "metrics", "doc_chunks", "documents"):
                cur = self._conn.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
                counts[table] = cur.rowcount
            cur = self._conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            counts["sessions"] = cur.rowcount
            self._conn.commit()
        if audio_dir and audio_dir.is_dir():
            import shutil

            removed = 0
            for path in audio_dir.glob(f"{session_id}*"):
                if path.is_dir():
                    removed += sum(1 for f in path.rglob("*") if f.is_file())
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
                    removed += 1
            counts["audio_files"] = removed
        return counts

    def purge_older_than(
        self,
        days: int,
        audio_dir: Path | None = None,
        before_delete: Callable[[str], Any] | None = None,
    ) -> list[str]:
        """Delete ended sessions older than ``days``. Active sessions are kept.

        ``before_delete`` runs with the session id while the rows still exist, so
        callers can clean up files that are keyed by the transcript.
        """
        cutoff = _now() - days * 86400
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM sessions WHERE created_at < ? AND status != 'active'", (cutoff,)
            ).fetchall()
        purged: list[str] = []
        for row in rows:
            session_id = row["id"]
            if before_delete is not None:
                before_delete(session_id)
            if self.purge_session(session_id, audio_dir).get("sessions"):
                purged.append(session_id)
        return purged

    def export_session(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        quiz = self.get_material(session_id, "quiz")
        return {
            "session": session,
            "segments": self.list_segments(session_id),
            "summary": self.get_material(session_id, "summary"),
            "quiz": quiz,
            "documents": self.list_documents(session_id),
            "topic_stats": self.topic_stats(session_id),
            "exported_at": _now(),
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
