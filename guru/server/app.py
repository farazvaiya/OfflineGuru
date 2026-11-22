"""FastAPI application: teacher console, student client, REST + WebSocket APIs.

Security model (LAN-scoped, no cloud):
* The teacher creates a session and receives a random teacher token.
* Students join with a 6-character code + their name and receive their own token.
* Every student request is scoped to that student's own session and answers; one
  student can never read another student's submissions.
* Teacher-only material (quiz keys, other students' answers) is never sent over
  the student channel until an answer is graded.
* There is no Internet dependency and no fallback to any cloud API.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import logging.handlers
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import net
from ..asr import WhisperAsr
from ..audio import MicCapture, WavSource
from ..bilingual import BidirectionalPiperTts, BidirectionalTranslator, language_pair
from ..bus import EventBus
from ..config import Settings, load_settings
from ..education.documents import MAX_BYTES, Chunk, DocumentError, chunk_pages, extract_pages
from ..education.service import EducationService
from ..logsafe import install_log_redaction
from ..pipeline import ClassroomPipeline
from ..storage import Storage
from ..tts import NullTts

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous 0/O/1/I

# Serialises document extraction across requests (see the upload endpoint).
_UPLOAD_LOCK = threading.Lock()

# Upload-time bilingual indexing. The shipped Marian model is en->bn only, so a
# Bangla query cannot be translated into English; instead every English chunk
# gets a Bangla side once, and retrieval scores both. Bounded so a large deck can
# never turn one upload into an unbounded machine translation job.
BILINGUAL_TIME_BUDGET_S = 60.0
BILINGUAL_CHAR_BUDGET = 120_000
_BANGLA_RE = re.compile(r"[\u0980-\u09FF]")
_BN_TRANSLATE_BATCH = 16


# ---------------------------------------------------------------------------
# App context
# ---------------------------------------------------------------------------
@dataclass
class AppContext:
    settings: Settings
    storage: Storage
    bus: EventBus
    asr: Any
    translator: Any
    tts: Any
    pipeline: ClassroomPipeline
    education: EducationService
    session_id: str | None = None
    capture: MicCapture | None = None
    wav_task: asyncio.Task | None = None
    model_status: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Request bodies. Defined at module scope: with ``from __future__ import
# annotations`` FastAPI resolves string annotations against module globals, so
# classes defined inside create_app() are not resolvable (they were silently
# treated as query parameters).
# ---------------------------------------------------------------------------
class CreateSessionBody(BaseModel):
    title: str = Field(default="Class lecture", max_length=120)
    source_lang: str = Field(default="en", pattern="^(en|bn|auto)$")
    target_lang: str | None = Field(default=None, pattern="^(en|bn)$")


class StartBody(BaseModel):
    capture: str = Field(default="mic", pattern="^(mic|wav|none)$")
    wav_path: str | None = None
    speed: float = 1.0
    # Domain vocabulary for today's lesson (comma separated). Measured to fix
    # science-term recognition, e.g. "glucose" was heard as "Bluecoast" without it.
    hotwords: str | None = Field(default=None, max_length=300)


class EditSegmentBody(BaseModel):
    en: str | None = Field(default=None, max_length=4000)
    bn: str | None = Field(default=None, max_length=4000)


class GenerateQuizBody(BaseModel):
    count: int = Field(default=5, ge=1, le=12)
    include_short: bool = True
    # Mix in questions grounded in uploaded slides/PDFs (page-cited).
    use_documents: bool = True


class MaterialBody(BaseModel):
    status: str | None = Field(default=None, pattern="^(draft|approved|archived)$")
    content: dict[str, Any] | None = None


class OverrideBody(BaseModel):
    is_correct: bool


class JoinBody(BaseModel):
    code: str = Field(min_length=4, max_length=10)
    name: str = Field(min_length=1, max_length=40)


class AnswerBody(BaseModel):
    question_id: str = Field(min_length=1, max_length=40)
    answer: str = Field(max_length=2000)


def _configure_logging(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(
            logging.handlers.RotatingFileHandler(
                data_dir / "guru.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
        )
    except OSError:  # pragma: no cover - read-only data dir
        pass
    logging.basicConfig(
        level=os.environ.get("GURU_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )
    # Tokens travel as query parameters (browser audio/WebSocket cannot send
    # headers), so any log line that formats a URL would persist a credential.
    # Scrub every sink instead of turning logging off.
    install_log_redaction()


def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def _load_models(ctx: AppContext) -> None:
    """Load every available model once, in sequence; record failures, never raise.

    Loading the three models in threads was measured at 2.2-2.7 s with +549 MB
    resident, against 3.8 s with +270 MB here: twice the memory for ~1.4 s. On a
    school laptop that also runs ASR and translation live, the memory headroom
    matters more than the one-off seconds, so this stays sequential.
    """
    for name, obj in (("asr", ctx.asr), ("translator", ctx.translator), ("tts", ctx.tts)):
        t0 = time.perf_counter()
        try:
            obj.load()
            ctx.model_status[name] = {
                "loaded": True,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        except Exception as exc:  # noqa: BLE001  # a missing model is a supported state
            ctx.model_status[name] = {"loaded": False, "error": str(exc)}
            log.warning("model %s unavailable: %s", name, exc)


def _forget_session_audio(storage: Storage, tts: Any, session_id: str) -> int:
    """Drop cached TTS audio for a session's captions (files keyed by text hash)."""
    texts = [s.get("bn", "") for s in storage.list_segments(session_id)]
    return tts.forget_cached(texts)


def _bilingual_chunks(ctx: AppContext, chunks: list[Chunk]) -> tuple[list[dict[str, Any]], int]:
    """Return chunk rows for storage, each with its Bangla side where possible.

    Chunks that are already Bangla are their own ``text_bn``. English chunks are
    translated with the local Marian model in bounded batches. Any failure leaves
    ``text_bn`` empty, which simply falls back to lexical matching for that chunk:
    retrieval never depends on translation succeeding.
    """
    rows: list[dict[str, Any]] = [
        {"page": c.page, "text": c.text, "text_bn": None} for c in chunks
    ]
    for row, chunk in zip(rows, chunks):
        if _BANGLA_RE.search(chunk.text):
            row["text_bn"] = chunk.text
    if not ctx.settings.education.bilingual_index:
        return rows, sum(1 for r in rows if r["text_bn"])

    pending = [i for i, c in enumerate(chunks) if rows[i]["text_bn"] is None and c.text.strip()]
    if not pending:
        return rows, sum(1 for r in rows if r["text_bn"])
    if not getattr(ctx.translator, "available", False):
        log.info("bilingual indexing skipped: translation model unavailable")
        return rows, sum(1 for r in rows if r["text_bn"])

    started = time.monotonic()
    chars = 0
    for start in range(0, len(pending), _BN_TRANSLATE_BATCH):
        if time.monotonic() - started > BILINGUAL_TIME_BUDGET_S or chars > BILINGUAL_CHAR_BUDGET:
            log.warning(
                "bilingual indexing stopped at %d/%d chunks (budget);"
                " those chunks stay lexical-only",
                sum(1 for r in rows if r["text_bn"]),
                len(chunks),
            )
            break
        batch = pending[start : start + _BN_TRANSLATE_BATCH]
        texts = [chunks[i].text for i in batch]
        chars += sum(len(t) for t in texts)
        try:
            translations = ctx.translator.translate_batch(texts)
        except Exception:  # indexing must never fail an upload
            log.exception("bilingual indexing failed; keeping the lexical index")
            break
        for index, translated in zip(batch, translations):
            if translated.text and not translated.skipped:
                rows[index]["text_bn"] = translated.text
    return rows, sum(1 for r in rows if r["text_bn"])


def _purge_expired(storage: Storage, settings: Settings, data_dir: Path, tts: Any) -> None:
    """Delete sessions past ``storage.retention_days`` (0 disables retention)."""
    days = settings.storage.retention_days
    if days <= 0:
        return
    try:
        purged = storage.purge_older_than(
            days,
            data_dir / "audio",
            before_delete=lambda sid: _forget_session_audio(storage, tts, sid),
        )
    except Exception:  # pragma: no cover - cleanup must never stop the server
        log.exception("retention cleanup failed")
        return
    if purged:
        log.info("retention: removed %d session(s) older than %d days", len(purged), days)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    data_dir = settings.resolve(settings.storage.data_dir) or Path("data")
    _configure_logging(data_dir)

    storage = Storage(settings.resolve(settings.storage.db_path) or data_dir / "guru.db")
    bus = EventBus(queue_size=settings.server.ws_queue_size)

    asr = WhisperAsr(settings)
    translator = BidirectionalTranslator(settings)
    tts = BidirectionalPiperTts(settings) if settings.tts.enabled else NullTts()
    pipeline = ClassroomPipeline(settings, bus, storage, asr, translator, tts)
    education = EducationService(settings, storage, translator)

    ctx = AppContext(
        settings=settings,
        storage=storage,
        bus=bus,
        asr=asr,
        translator=translator,
        tts=tts,
        pipeline=pipeline,
        education=education,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await asyncio.to_thread(_purge_expired, storage, settings, data_dir, ctx.tts)
        await asyncio.to_thread(_load_models, ctx)
        task = asyncio.create_task(_education_loop(ctx))
        # One retention worker for the process lifetime: it keeps purging while a
        # long lecture runs, instead of waiting for the next restart. Exactly one
        # task is created here, so sweeps cannot overlap each other.
        interval = settings.storage.retention_interval_s
        retention_task = (
            asyncio.create_task(_retention_loop(ctx, data_dir, interval)) if interval > 0 else None
        )
        log.info("Offline Guru ready on %s:%s", settings.server.host, settings.server.port)
        try:
            yield
        finally:
            task.cancel()
            if retention_task is not None:
                retention_task.cancel()
            if ctx.wav_task:
                ctx.wav_task.cancel()
            if ctx.capture:
                ctx.capture.stop()
            pipeline.stop()
            storage.close()

    app = FastAPI(title="Offline Guru", version="0.1.0", lifespan=lifespan)
    app.state.ctx = ctx

    # The console polls the session and health endpoints every 5 s, and each probe
    # shells out to ``ip``/``nmcli`` and opens a TCP connection. Reuse the answer
    # for a few seconds; a stale IP for 30 s changes nothing about the classroom.
    net_cache: dict[str, Any] = {"at": 0.0, "status": None}

    async def current_network() -> Any:
        ttl = settings.server.network_cache_s
        cached = net_cache["status"]
        if cached is not None and time.monotonic() - net_cache["at"] < ttl:
            return cached
        status = await asyncio.to_thread(
            net.network_status, settings.server.port, settings.server.online_probe
        )
        net_cache["status"], net_cache["at"] = status, time.monotonic()
        return status

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def teacher_session(session_id: str, token: str | None) -> dict[str, Any]:
        session = storage.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        if settings.server.require_teacher_token and not token:
            raise HTTPException(401, "Teacher token required")
        # Only the digest is stored; the raw token never leaves the request.
        if token and not storage.teacher_token_matches(session_id, token):
            raise HTTPException(403, "Invalid teacher token")
        return session

    def require_health_access(request: Request, token: str | None) -> None:
        """Health carries model errors, interfaces and latency: teachers only.

        A supplied credential must be correct, whether or not the request came
        from loopback: a bad token is never quietly downgraded to the local
        allowance. Only a request carrying no token may use loopback, which is the
        pre-session setup path on the laptop itself.
        """
        if token:
            if ctx.session_id and storage.teacher_token_matches(ctx.session_id, token):
                return
            raise HTTPException(403, "Invalid teacher token")
        host = request.client.host if request.client else ""
        if host in {"127.0.0.1", "::1"}:
            return
        raise HTTPException(401, "Teacher token required for /api/health")

    def student_from_token(token: str | None) -> dict[str, Any]:
        if not token:
            raise HTTPException(401, "Student token required")
        student = storage.student_by_token(token)
        if not student:
            raise HTTPException(401, "Unknown student token")
        return student

    def require_pin(pin: str | None) -> None:
        expected = os.environ.get("GURU_TEACHER_PIN")
        if expected and not secrets.compare_digest(pin or "", expected):
            raise HTTPException(403, "Teacher PIN required")

    # ------------------------------------------------------------------
    # pages
    # ------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def student_page() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "student.html").read_text(encoding="utf-8"))

    @app.get("/teacher", response_class=HTMLResponse)
    async def teacher_page() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "teacher.html").read_text(encoding="utf-8"))

    @app.get("/qr.svg")
    async def qr_svg(data: str = Query(..., max_length=300)) -> Response:
        if not data.startswith("http://"):
            raise HTTPException(400, "Only http:// join URLs are supported offline")
        return Response(content=net.qr_svg(data), media_type="image/svg+xml")

    # ------------------------------------------------------------------
    # health / diagnostics
    # ------------------------------------------------------------------
    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "time": time.time(), "version": "0.1.0"}

    @app.get("/api/health")
    async def health(request: Request, token: str | None = Query(default=None),
                     x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_health_access(request, token or x_teacher_token)
        import psutil

        mem = psutil.virtual_memory()
        netstat = await current_network()
        return {
            "ok": True,
            "uptime_s": round(time.time() - ctx.started_at, 1),
            "session": ctx.pipeline.health(),
            "models": ctx.model_status,
            "resources": {
                "ram_total_mb": round(mem.total / 1e6),
                "ram_available_mb": round(mem.available / 1e6),
                "cpu_percent": psutil.cpu_percent(interval=None),
                "load1": os.getloadavg()[0] if hasattr(os, "getloadavg") else None,
                "disk_free_gb": round(psutil.disk_usage(str(data_dir)).free / 1e9, 1),
            },
            "network": netstat.to_dict(),
            "clients": {"bus": ctx.bus.counts(), "subscribers": ctx.bus.stats()},
            "metrics": {
                "asr_ms": storage.metric_summary("asr_ms"),
                "translate_ms": storage.metric_summary("translate_ms"),
                "tts_ms": storage.metric_summary("tts_ms"),
                "e2e_ms": storage.metric_summary("e2e_ms"),
            },
            "warnings": _health_warnings(ctx),
        }

    # ------------------------------------------------------------------
    # teacher API
    # ------------------------------------------------------------------
    @app.post("/api/teacher/session")
    async def create_session(body: CreateSessionBody,
                             x_teacher_pin: str | None = Header(default=None)) -> dict[str, Any]:
        require_pin(x_teacher_pin)
        if ctx.session_id and ctx.pipeline.status != "idle":
            raise HTTPException(409, "A lecture is already running. Stop it before creating a new session.")
        code = _new_code()
        while storage.get_session_by_code(code):
            code = _new_code()
        token = secrets.token_urlsafe(24)
        try:
            source_lang, routed_target = language_pair(body.source_lang)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        target_lang = body.target_lang or routed_target
        if source_lang != "auto" and target_lang != routed_target:
            raise HTTPException(422, "target_lang must be the opposite language of source_lang")
        if source_lang == "auto":
            source_lang, target_lang = "auto", "auto"
        sid = storage.create_session(body.title, token, code, source_lang, target_lang)
        ctx.session_id = sid
        netstat = await current_network()
        url = net.join_url(netstat.primary_ip, settings.server.port)
        return {
            "session": storage.get_session(sid),
            "teacher_token": token,
            "join_code": code,
            "join_url": url,
            "network": netstat.to_dict(),
        }

    @app.post("/api/teacher/session/{session_id}/start")
    async def start_session(session_id: str, body: StartBody | None = None,
                            x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        # An older cached dashboard POSTs /start with no body at all; treat that
        # as the default mic start instead of rejecting it with a 422.
        body = body or StartBody()
        session = teacher_session(session_id, x_teacher_token)
        if ctx.pipeline.status == "running":
            raise HTTPException(409, "Already running")
        ctx.session_id = session_id
        if body.hotwords is not None and hasattr(ctx.asr, "cfg"):
            ctx.asr.cfg.hotwords = body.hotwords.strip()[:300]
        wav_path: Path | None = None
        if body.capture == "wav":
            candidate = body.wav_path or settings.audio.input_wav
            if not candidate or not Path(candidate).is_file():
                raise HTTPException(400, "wav_path must point to an existing 16 kHz mono file")
            wav_path = Path(candidate)

        ctx.pipeline.configure_languages(session.get("source_lang", "en"))
        if body.capture != "none":
            await asyncio.to_thread(_load_models, ctx)
            if not ctx.model_status.get("asr", {}).get("loaded"):
                detail = ctx.model_status.get("asr", {}).get("error", "model missing")
                raise HTTPException(503, f"ASR unavailable: {detail}")
            try:
                await asyncio.to_thread(ctx.pipeline.prepare_direction)
            except Exception as exc:
                raise HTTPException(503, f"Translation/TTS direction unavailable: {exc}") from exc

        ctx.pipeline.start(session_id, asyncio.get_running_loop())
        load_errors = {k: v for k, v in ctx.model_status.items() if not v.get("loaded")}
        if body.capture == "mic":
            cap = MicCapture(device=settings.audio.device, sample_rate=settings.audio.sample_rate)
            cap.start(ctx.pipeline.submit_audio)
            ctx.capture = cap
        elif body.capture == "wav":
            assert wav_path is not None
            ctx.wav_task = asyncio.create_task(_replay_wav(ctx, wav_path, body.speed))
        return {
            "session": session,
            "pipeline": ctx.pipeline.health(),
            "models": ctx.model_status,
            "warnings": [f"{k} unavailable: {v.get('error')}" for k, v in load_errors.items()],
        }

    @app.post("/api/teacher/session/{session_id}/pause")
    async def pause_session(session_id: str, x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        teacher_session(session_id, x_teacher_token)
        ctx.pipeline.pause()
        return ctx.pipeline.health()

    @app.post("/api/teacher/session/{session_id}/resume")
    async def resume_session(session_id: str, x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        teacher_session(session_id, x_teacher_token)
        ctx.pipeline.resume()
        return ctx.pipeline.health()

    @app.post("/api/teacher/session/{session_id}/stop")
    async def stop_session(session_id: str, x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        teacher_session(session_id, x_teacher_token)
        if ctx.capture:
            ctx.capture.stop()
            ctx.capture = None
        if ctx.wav_task:
            ctx.wav_task.cancel()
            ctx.wav_task = None
        await asyncio.to_thread(ctx.pipeline.stop)
        # Preserve the final drafts; teacher can review before ending the session.
        try:
            await asyncio.to_thread(ctx.education.generate_summary, session_id, True)
        except Exception:
            log.exception("final summary generation failed")
        return {"pipeline": ctx.pipeline.health(), "session": storage.get_session(session_id)}

    @app.post("/api/teacher/session/{session_id}/end")
    async def end_session(session_id: str, x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        teacher_session(session_id, x_teacher_token)
        if ctx.pipeline.status != "idle":
            raise HTTPException(409, "Stop the lecture before ending the session")
        storage.end_session(session_id)
        return {"session": storage.get_session(session_id)}

    @app.get("/api/teacher/session/{session_id}")
    async def session_state(session_id: str,
                             x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        session = teacher_session(session_id, x_teacher_token)
        netstat = await current_network()
        summary = storage.get_material(session_id, "summary")
        quiz = storage.get_material(session_id, "quiz")
        return {
            "session": session,
            "pipeline": ctx.pipeline.health(),
            "network": netstat.to_dict(),
            "join_url": net.join_url(netstat.primary_ip, settings.server.port),
            "segments": storage.segment_count(session_id),
            "students": _student_rows(storage.list_students(session_id)),
            "materials": {
                "summary": _material_meta(summary),
                "quiz": _material_meta(quiz),
            },
            "documents": storage.list_documents(session_id),
            "metrics": {
                "asr_ms": storage.metric_summary("asr_ms", session_id),
                "translate_ms": storage.metric_summary("translate_ms", session_id),
                "tts_ms": storage.metric_summary("tts_ms", session_id),
                "e2e_ms": storage.metric_summary("e2e_ms", session_id),
            },
            "models": ctx.model_status,
        }

    @app.get("/api/teacher/session/{session_id}/segments")
    async def teacher_segments(session_id: str, after_seq: int = -1,
                                x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        teacher_session(session_id, x_teacher_token)
        segs = storage.list_segments(session_id, after_seq=after_seq)
        return {"segments": [_public_segment(s) for s in segs]}

    @app.patch("/api/teacher/segment/{segment_id}")
    async def edit_segment(segment_id: str, body: EditSegmentBody,
                            x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        seg = storage.get_segment(segment_id)
        if not seg:
            raise HTTPException(404, "Segment not found")
        teacher_session(seg["session_id"], x_teacher_token)
        storage.update_segment_text(segment_id, en=body.en, bn=body.bn)
        updated = storage.get_segment(segment_id) or seg
        ctx.bus.publish(
            {"type": "segment.updated", "session_id": seg["session_id"], "segment": _public_segment(updated)},
            session_id=seg["session_id"],
        )
        return {"segment": _public_segment(updated)}

    @app.post("/api/teacher/session/{session_id}/summary")
    async def generate_summary(session_id: str,
                                x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        teacher_session(session_id, x_teacher_token)
        summary = await asyncio.to_thread(ctx.education.generate_summary, session_id, False)
        return {"material": _material_meta(storage.get_material(session_id, "summary")),
                "content": summary}

    @app.post("/api/teacher/session/{session_id}/quiz")
    async def generate_quiz(session_id: str, body: GenerateQuizBody,
                            x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        teacher_session(session_id, x_teacher_token)
        quiz = await asyncio.to_thread(
            ctx.education.generate_quiz, session_id, body.count, body.include_short,
            body.use_documents,
        )
        return {"material": _material_meta(storage.get_material(session_id, "quiz")), "content": quiz}

    @app.get("/api/teacher/session/{session_id}/materials/{kind}")
    async def get_material(session_id: str, kind: str,
                            x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        teacher_session(session_id, x_teacher_token)
        if kind not in {"summary", "quiz"}:
            raise HTTPException(400, "kind must be summary or quiz")
        material = storage.get_material(session_id, kind)
        if not material:
            raise HTTPException(404, "Material not generated yet")
        return material

    @app.patch("/api/teacher/session/{session_id}/materials/{kind}")
    async def update_material(session_id: str, kind: str, body: MaterialBody,
                              x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        teacher_session(session_id, x_teacher_token)
        if kind not in {"summary", "quiz"}:
            raise HTTPException(400, "kind must be summary or quiz")
        if body.status:
            storage.set_material_status(session_id, kind, body.status, body.content)
        elif body.content is not None:
            storage.set_material_status(session_id, kind, "draft", body.content)
        material = storage.get_material(session_id, kind)
        if not material:
            raise HTTPException(404, "Material not found")
        ctx.bus.publish({"type": "materials.updated", "session_id": session_id, "kind": kind,
                         "status": material["status"]}, session_id=session_id)
        return material

    # ------------------------------------------------------------------
    # teaching documents (Phase 4: local ingestion + retrieval)
    # ------------------------------------------------------------------
    @app.post("/api/teacher/session/{session_id}/documents")
    async def upload_document(session_id: str, request: Request,
                              name: str = Query(..., min_length=1, max_length=120),
                              x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        """Ingest a PDF/PPTX/TXT/MD sent as the raw request body.

        The body is read directly instead of as multipart form data: that would
        add a dependency, and the teacher console uploads one file at a time.
        """
        teacher_session(session_id, x_teacher_token)
        # Live captions keep CPU priority: extraction is pure Python and competes
        # with the ASR worker for the interpreter (+4% on a proxy loop, measured in
        # NEXT_PHASE_REPORT.md), and a pathological file should not stack up.
        if ctx.pipeline.status == "running" and not settings.education.allow_upload_while_live:
            raise HTTPException(
                409, "Pause or stop the lecture before uploading; live captions keep CPU priority."
            )
        if len(storage.list_documents(session_id)) >= settings.education.max_documents_per_session:
            raise HTTPException(
                409, f"This session already has {settings.education.max_documents_per_session} documents"
            )
        declared = int(request.headers.get("content-length") or 0)
        if declared > MAX_BYTES:
            raise HTTPException(413, f"File is larger than {MAX_BYTES // (1024 * 1024)} MB")
        data = await request.body()
        # One extraction at a time: two concurrent uploads would double the load
        # the ASR thread has to share the interpreter with.
        if not _UPLOAD_LOCK.acquire(blocking=False):
            raise HTTPException(429, "Another document is still being processed; try again shortly.")
        try:
            started = time.perf_counter()
            pages = await asyncio.to_thread(extract_pages, data, name)
            chunks = await asyncio.to_thread(chunk_pages, pages)
            processing_ms = round((time.perf_counter() - started) * 1000, 1)
            index_started = time.perf_counter()
            chunk_rows, bilingual = await asyncio.to_thread(_bilingual_chunks, ctx, chunks)
            index_ms = round((time.perf_counter() - index_started) * 1000, 1)
        except DocumentError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            _UPLOAD_LOCK.release()
        safe_name = Path(name).name[:120]
        doc, created = storage.add_document(
            session_id,
            safe_name,
            Path(safe_name).suffix.lower().lstrip("."),
            hashlib.sha256(data).hexdigest(),
            len(pages),
            chunk_rows,
        )
        ctx.bus.publish({"type": "materials.updated", "session_id": session_id,
                         "kind": "documents", "status": "ready"}, roles=("teacher",),
                        session_id=session_id)
        return {"document": doc, "duplicate": not created, "chunks": len(chunks),
                "processing_ms": processing_ms, "bilingual_chunks": bilingual,
                "index_ms": index_ms}

    @app.get("/api/teacher/session/{session_id}/documents")
    async def list_documents(session_id: str,
                             x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        teacher_session(session_id, x_teacher_token)
        return {"documents": storage.list_documents(session_id)}

    @app.get("/api/teacher/session/{session_id}/documents/search")
    async def search_documents(session_id: str, q: str = Query(..., min_length=1, max_length=200),
                               k: int = Query(5, ge=1, le=20),
                               x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        """Preview what the quiz/summary will cite for a question."""
        teacher_session(session_id, x_teacher_token)
        hits = await asyncio.to_thread(ctx.education.search_materials, session_id, q, k)
        return {"query": q, "hits": hits,
                "chunks_searched": len(storage.document_chunks(session_id))}

    @app.delete("/api/teacher/session/{session_id}/documents/{document_id}")
    async def delete_document(session_id: str, document_id: str, confirm: bool = False,
                              x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        teacher_session(session_id, x_teacher_token)
        if not confirm:
            raise HTTPException(400, "Pass confirm=true to delete this document")
        removed = storage.delete_document(session_id, document_id)
        if not removed:
            raise HTTPException(404, "Document not found")
        return {"deleted": removed}

    @app.get("/api/teacher/session/{session_id}/answers")
    async def all_answers(session_id: str,
                          x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        teacher_session(session_id, x_teacher_token)
        students = {s["id"]: s["name"] for s in storage.list_students(session_id)}
        rows = storage.list_answers(session_id)
        for r in rows:
            r["student_name"] = students.get(r["student_id"], "unknown")
        return {"answers": rows, "topic_stats": storage.topic_stats(session_id)}

    @app.post("/api/teacher/answer/{answer_id}/override")
    async def override_answer(answer_id: str, body: OverrideBody,
                               x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        answer = storage.get_answer_by_id(answer_id)
        if not answer:
            raise HTTPException(404, "Answer not found")
        teacher_session(answer["session_id"], x_teacher_token)
        storage.override_answer(answer_id, body.is_correct)
        return {"ok": True}

    @app.get("/api/teacher/audio/{segment_id}")
    async def teacher_audio(segment_id: str, token: str = Query(...)) -> FileResponse:
        """Audio for teacher preview; the <audio> element cannot send headers."""
        seg = storage.get_segment(segment_id)
        if not seg:
            raise HTTPException(404, "Audio not found")
        teacher_session(seg["session_id"], token)
        path = seg.get("audio_path")
        if not path or not Path(path).is_file():
            raise HTTPException(404, "Audio not available for this caption")
        return FileResponse(path, media_type="audio/wav",
                            headers={"Cache-Control": "private, max-age=600"})

    @app.get("/api/teacher/session/{session_id}/export")
    async def export_session(session_id: str,
                              x_teacher_token: str | None = Header(default=None)) -> Response:
        teacher_session(session_id, x_teacher_token)
        bundle = storage.export_session(session_id)
        payload = json.dumps(bundle, ensure_ascii=False, indent=2)
        return Response(
            content=payload,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="offline-guru-{session_id}.json"'
            },
        )

    @app.delete("/api/teacher/session/{session_id}")
    async def purge_session(session_id: str, confirm: bool = False,
                             x_teacher_token: str | None = Header(default=None)) -> dict[str, Any]:
        teacher_session(session_id, x_teacher_token)
        if not confirm:
            raise HTTPException(400, "Pass confirm=true to delete all session data")
        if ctx.pipeline.status != "idle":
            raise HTTPException(409, "Stop the lecture first")
        # Cache files are keyed by caption text, so they must go before the rows do.
        cached = _forget_session_audio(storage, ctx.tts, session_id)
        counts = storage.purge_session(session_id, data_dir / "audio")
        counts["tts_cache_files"] = cached
        return {"deleted": counts}

    # ------------------------------------------------------------------
    # student API
    # ------------------------------------------------------------------
    @app.post("/api/student/join")
    async def join(body: JoinBody) -> dict[str, Any]:
        session = storage.get_session_by_code(body.code)
        if not session:
            raise HTTPException(404, "Class code not found or session ended")
        name = " ".join(body.name.split())[:40]
        if not name:
            raise HTTPException(400, "Please enter your name")
        if len(storage.list_students(session["id"])) >= settings.server.max_students:
            raise HTTPException(429, "This class is full")
        student = storage.add_student(session["id"], name)
        return {
            "student": {"id": student["id"], "name": student["name"], "token": student["token"]},
            "session": {
                "id": session["id"],
                "title": session["title"],
                "source_lang": session["source_lang"],
                "target_lang": session["target_lang"],
            },
            "segments": [_public_segment(s) for s in storage.list_segments(session["id"], limit=300)],
            "quiz": ctx.education.student_quiz(session["id"]),
        }

    def student_ctx(token: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
        student = student_from_token(token)
        storage.touch_student(student["id"])
        session = storage.get_session(student["session_id"])
        if not session:
            raise HTTPException(404, "Session no longer exists")
        return student, session

    @app.get("/api/student/state")
    async def student_state(token: str = Query(...), after_seq: int = Query(-1),
                            limit: int = Query(400, ge=1, le=1000)) -> dict[str, Any]:
        student, session = student_ctx(token)
        return {
            "student": {"id": student["id"], "name": student["name"]},
            "session": {"id": session["id"], "title": session["title"], "status": session["status"]},
            # ``after_seq`` lets a reconnecting client fetch only what it missed;
            # the defaults reproduce the original full-transcript response.
            "segments": [
                _public_segment(s)
                for s in storage.list_segments(session["id"], after_seq=after_seq, limit=limit)
            ],
            "quiz": ctx.education.student_quiz(session["id"]),
            "feedback": ctx.education.student_feedback(session["id"], student["id"]),
            "summary": _student_summary(storage.get_material(session["id"], "summary")),
            "live": ctx.pipeline.session_id == session["id"] and ctx.pipeline.status == "running",
        }

    @app.get("/api/student/audio/{segment_id}")
    async def student_audio(segment_id: str, token: str = Query(...)) -> FileResponse:
        _student, session = student_ctx(token)
        seg = storage.get_segment(segment_id)
        if not seg or seg["session_id"] != session["id"]:
            raise HTTPException(404, "Audio not found")
        path = seg.get("audio_path")
        if not path or not Path(path).is_file():
            raise HTTPException(404, "Audio not available for this caption")
        return FileResponse(path, media_type="audio/wav",
                            headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/api/student/answer")
    async def submit_answer(body: AnswerBody, token: str = Query(...)) -> dict[str, Any]:
        student, session = student_ctx(token)
        if not _rate_limit_ok(student["id"]):
            raise HTTPException(429, "Too many submissions, please slow down")
        try:
            result = ctx.education.submit_answer(
                session["id"], student["id"], body.question_id, body.answer.strip()
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "Unknown question") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        ctx.bus.publish({"type": "answer.submitted", "session_id": session["id"],
                         "student_id": student["id"]}, roles=("teacher",),
                        session_id=session["id"])
        return result

    @app.post("/api/student/audio-preference")
    async def audio_preference(enabled: bool = Query(...), token: str = Query(...)) -> dict[str, Any]:
        student, _session = student_ctx(token)
        return {"ok": True, "audio_enabled": enabled, "student_id": student["id"]}

    # ------------------------------------------------------------------
    # WebSockets
    # ------------------------------------------------------------------
    @app.websocket("/ws/teacher")
    async def ws_teacher(ws: WebSocket, session_id: str, token: str) -> None:
        session = storage.get_session(session_id)
        if not session or not storage.teacher_token_matches(session_id, token):
            await ws.close(code=4401)
            return
        await ws.accept()
        sub = ctx.bus.subscribe("teacher", session_id)
        await ws.send_json({"type": "hello", "role": "teacher", "session": {
            "id": session["id"], "title": session["title"], "code": session["code"]}})
        try:
            await _pump(ws, sub, settings.server.heartbeat_s)
        finally:
            ctx.bus.unsubscribe(sub)

    @app.websocket("/ws/student")
    async def ws_student(ws: WebSocket, token: str) -> None:
        student = storage.student_by_token(token)
        if not student:
            await ws.close(code=4401)
            return
        await ws.accept()
        sub = ctx.bus.subscribe("student", student["session_id"])
        await ws.send_json({
            "type": "hello",
            "role": "student",
            "student": {"id": student["id"], "name": student["name"]},
            "live": ctx.pipeline.session_id == student["session_id"] and ctx.pipeline.status == "running",
        })
        try:
            await _pump(ws, sub, settings.server.heartbeat_s)
        finally:
            ctx.bus.unsubscribe(sub)

    app.state.ctx = ctx
    return app


# ---------------------------------------------------------------------------
# helpers used by routes
# ---------------------------------------------------------------------------
_answer_buckets: dict[str, list[float]] = {}


def _rate_limit_ok(student_id: str, limit: int = 30, window_s: float = 60.0) -> bool:
    now = time.time()
    bucket = [t for t in _answer_buckets.get(student_id, []) if now - t < window_s]
    if len(bucket) >= limit:
        _answer_buckets[student_id] = bucket
        return False
    bucket.append(now)
    _answer_buckets[student_id] = bucket
    return True


def _public_segment(seg: dict[str, Any]) -> dict[str, Any]:
    """Student-visible segment, including the real-time playback contract.

    The legacy keys (id/seq/en/bn/audio_url) are kept so an older cached client
    keeps working; the explicit fields below are what the ordered player uses.
    """
    audio_url = f"/api/student/audio/{seg['id']}" if seg.get("audio_path") else None
    audio_status = seg.get("audio_status") or ("ready" if audio_url else "pending")
    return {
        "id": seg["id"],
        "seq": seg["seq"],
        "start_s": round(float(seg.get("start_s", 0)), 2),
        "end_s": round(float(seg.get("end_s", 0)), 2),
        "en": seg.get("en", ""),
        "bn": seg.get("bn", ""),
        "audio_url": audio_url,
        "confidence": round(float(seg.get("confidence", 0) or 0), 2),
        "numbers_preserved": bool(seg.get("numbers_preserved", 1)),
        "asr_ms": seg.get("asr_ms"),
        "translate_ms": seg.get("translate_ms"),
        "tts_ms": seg.get("tts_ms"),
        "e2e_ms": round(float(seg.get("total_ms", 0) or 0) or 0, 0),
        # -- real-time contract --
        "segment_id": seg["id"],
        "sequence_number": seg["seq"],
        "source_text": seg.get("source_text") or seg.get("en", "") or seg.get("bn", ""),
        "translated_text": seg.get("translated_text") or (seg.get("bn", "") if seg.get("target_language", "bn") == "bn" else seg.get("en", "")),
        "source_language": seg.get("source_language", "en"),
        "target_language": seg.get("target_language", "bn"),
        "source_start_time": round(float(seg.get("start_s", 0)), 2),
        "source_end_time": round(float(seg.get("end_s", 0)), 2),
        "translation_timestamp": seg.get("translation_at") or None,
        "audio_status": audio_status,
        "audio_reference": audio_url,
        "segment_status": seg.get("segment_status") or "final",
    }


def _material_meta(material: dict[str, Any] | None) -> dict[str, Any] | None:
    if not material:
        return None
    return {
        "id": material["id"],
        "kind": material["kind"],
        "status": material["status"],
        "updated_at": material["updated_at"],
    }


def _student_summary(material: dict[str, Any] | None) -> dict[str, Any] | None:
    if not material or material["status"] != "approved":
        return None
    content = material["content"]
    return {
        "title": content.get("title", ""),
        "summary_bn": content.get("summary_bn", []),
        "key_terms": content.get("key_terms", []),
        "generated_by": content.get("generated_by", ""),
    }


def _student_rows(students: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = time.time()
    return [
        {**s, "online": now - float(s.get("last_seen") or 0) < 90}
        for s in students
    ]


def _health_warnings(ctx: AppContext) -> list[str]:
    import psutil

    warns: list[str] = []
    mem = psutil.virtual_memory()
    if mem.available < 400e6:
        warns.append(f"Low memory: {mem.available / 1e6:.0f} MB available.")
    for name, st in ctx.model_status.items():
        if not st.get("loaded"):
            warns.append(f"{name} unavailable: {st.get('error')}")
    if ctx.pipeline.stats.asr_queue_drops:
        warns.append(f"{ctx.pipeline.stats.asr_queue_drops} audio segments dropped (CPU too slow).")
    return warns


async def _education_loop(ctx: AppContext) -> None:
    """Periodically draft summaries in the background; never blocks translation."""
    while True:
        await asyncio.sleep(6)
        sid = ctx.pipeline.session_id
        if not sid or ctx.pipeline.status == "idle":
            continue
        try:
            changed = await asyncio.to_thread(ctx.education.maybe_refresh, sid)
            if changed:
                ctx.bus.publish({"type": "materials.updated", "session_id": sid,
                                 "kind": "summary", "status": "draft"}, roles=("teacher",),
                                session_id=sid)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("education loop error")


async def _retention_loop(ctx: AppContext, data_dir: Path, interval_s: float) -> None:
    """Re-run retention every ``interval_s`` seconds for the process lifetime.

    Cancelled on shutdown. Each sweep runs in a worker thread so SQLite deletes
    and file removal never block the event loop that is serving captions.
    """
    while True:
        await asyncio.sleep(interval_s)
        try:
            await asyncio.to_thread(_purge_expired, ctx.storage, ctx.settings, data_dir, ctx.tts)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - a sweep must never kill the server
            log.exception("background retention failed")


async def _replay_wav(ctx: AppContext, path: Path, speed: float) -> None:
    """Replay a file in real time on a worker thread (used for demos/tests)."""

    def run() -> None:
        for chunk in WavSource(path, speed=speed).frames():
            ctx.pipeline.submit_audio(np.asarray(chunk))
            if ctx.pipeline.status == "idle":
                return

    await asyncio.to_thread(run)


async def _pump(ws: WebSocket, sub: Any, heartbeat_s: float) -> None:
    """Pump bus events to a WebSocket until either side disconnects."""

    async def sender() -> None:
        while True:
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=heartbeat_s)
                await ws.send_json(event)
                sub.queue.task_done()
            except TimeoutError:
                await ws.send_json({"type": "ping", "t": time.time()})

    async def receiver() -> None:
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_json({"type": "pong", "t": time.time()})

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, (asyncio.CancelledError,)):
                log.debug("ws closed: %s", exc)
    finally:
        for t in tasks:
            t.cancel()


app = create_app()


def main() -> None:
    import uvicorn

    settings = app.state.ctx.settings
    uvicorn.run(app, host=settings.server.host, port=settings.server.port, log_level="info")


if __name__ == "__main__":
    main()
