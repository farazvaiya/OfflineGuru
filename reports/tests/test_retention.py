"""Retention must clean expired sessions while the server keeps running.

The recorded limitation was that cleanup only ran at startup, so a server that
stayed up for a whole school day never removed anything. These tests cover the
sweep itself (expired deleted, active kept) and the background loop that now
drives it.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from guru.config import Settings
from guru.server.app import _purge_expired, _retention_loop
from guru.storage import Storage
from guru.tts import NullTts


@pytest.fixture
def env(tmp_path):
    settings = Settings()
    settings.storage.data_dir = str(tmp_path / "data")
    settings.storage.db_path = str(tmp_path / "data" / "test.db")
    settings.storage.retention_days = 1
    core = Storage(settings.storage.db_path)
    data_dir = tmp_path / "data"
    yield settings, core, data_dir
    core.close()


def _age(storage: Storage, session_id: str, days: float) -> None:
    """Backdate a session without adding a test-only API to Storage."""
    with storage._lock:
        storage._conn.execute(
            "UPDATE sessions SET created_at=? WHERE id=?",
            (time.time() - days * 86400, session_id),
        )
        storage._conn.commit()


def test_expired_ended_session_is_purged_and_active_sessions_are_not(env):
    settings, storage, data_dir = env
    expired = storage.create_session("old", "t1", "OLD111")
    storage.end_session(expired)
    _age(storage, expired, 5)

    active = storage.create_session("live", "t2", "LIVE22")
    _age(storage, active, 5)  # old but still active: must survive

    _purge_expired(storage, settings, data_dir, NullTts())

    assert storage.get_session(expired) is None
    assert storage.get_session(active) is not None


def test_recent_ended_session_is_kept(env):
    settings, storage, data_dir = env
    recent = storage.create_session("today", "t3", "NEW333")
    storage.end_session(recent)

    _purge_expired(storage, settings, data_dir, NullTts())

    assert storage.get_session(recent) is not None


def test_retention_disabled_keeps_everything(env):
    settings, storage, data_dir = env
    settings.storage.retention_days = 0
    old = storage.create_session("old", "t4", "OLD444")
    storage.end_session(old)
    _age(storage, old, 400)

    _purge_expired(storage, settings, data_dir, NullTts())

    assert storage.get_session(old) is not None


def test_background_loop_purges_without_restart(env):
    settings, storage, data_dir = env
    expired = storage.create_session("old", "t5", "OLD555")
    storage.end_session(expired)
    _age(storage, expired, 10)
    ctx = SimpleNamespace(storage=storage, settings=settings, tts=NullTts())

    async def run() -> None:
        task = asyncio.create_task(_retention_loop(ctx, data_dir, 0.05))
        try:
            await asyncio.sleep(0.3)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())

    assert storage.get_session(expired) is None


def test_background_loop_is_cancellable_before_the_first_sweep(env):
    settings, storage, data_dir = env
    keep = storage.create_session("live", "t6", "LIVE66")
    ctx = SimpleNamespace(storage=storage, settings=settings, tts=NullTts())

    async def run() -> None:
        task = asyncio.create_task(_retention_loop(ctx, data_dir, 3600))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())

    assert storage.get_session(keep) is not None


def test_purge_removes_documents_answers_and_segments(env):
    settings, storage, data_dir = env
    sid = storage.create_session("class", "t7", "DOC777")
    storage.add_segment(sid, en="chlorophyll captures light", bn="ক্লোরোফিল আলো ধরে")
    storage.add_document(sid, "slides.pptx", "pptx", "abc123", 1,
                         [{"page": 1, "text": "Photosynthesis", "text_bn": "সালোকসংশ্লেষণ"}])
    storage.end_session(sid)
    _age(storage, sid, 3)

    _purge_expired(storage, settings, data_dir, NullTts())

    assert storage.get_session(sid) is None
    assert storage.list_segments(sid) == []
    assert storage.list_documents(sid) == []
    assert storage.document_chunks(sid) == []
