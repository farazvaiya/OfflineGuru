"""In-process pub/sub with per-client bounded queues.

Slow or reconnecting students must never stall the lecture pipeline, so each
subscriber has its own bounded queue.  When a queue is full the oldest event is
dropped and counted; the client can recover the full transcript over REST.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Subscriber:
    id: str
    role: str
    session_id: str
    queue: asyncio.Queue[dict[str, Any]]
    dropped: int = 0
    delivered: int = 0

    def push(self, event: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(event)
            self.delivered += 1
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:  # pragma: no cover - race
                pass
            self.dropped += 1
            try:
                self.queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - race
                pass


class EventBus:
    def __init__(self, queue_size: int = 64) -> None:
        self.queue_size = queue_size
        self._subs: dict[str, Subscriber] = {}
        self._ids = itertools.count(1)

    def subscribe(self, role: str, session_id: str) -> Subscriber:
        sid = f"{role}-{next(self._ids)}"
        sub = Subscriber(
            id=sid,
            role=role,
            session_id=session_id,
            queue=asyncio.Queue(maxsize=self.queue_size),
        )
        self._subs[sid] = sub
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subs.pop(sub.id, None)

    def publish(self, event: dict[str, Any], roles: tuple[str, ...] | None = None,
                session_id: str | None = None) -> int:
        """Publish to matching subscribers. Returns delivery count."""
        targets = 0
        for sub in list(self._subs.values()):
            if roles is not None and sub.role not in roles:
                continue
            if session_id is not None and sub.session_id != session_id:
                continue
            sub.push(event)
            targets += 1
        return targets

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for sub in self._subs.values():
            out[sub.role] = out.get(sub.role, 0) + 1
        return out

    def stats(self) -> list[dict[str, Any]]:
        return [
            {
                "id": s.id,
                "role": s.role,
                "delivered": s.delivered,
                "dropped": s.dropped,
                "queued": s.queue.qsize(),
            }
            for s in self._subs.values()
        ]
