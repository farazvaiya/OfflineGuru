from __future__ import annotations

import asyncio

from guru.bus import EventBus


def test_publish_filters_by_role_and_session():
    async def scenario():
        bus = EventBus(queue_size=4)
        teacher = bus.subscribe("teacher", "s1")
        student1 = bus.subscribe("student", "s1")
        student2 = bus.subscribe("student", "s2")

        n = bus.publish({"type": "x"}, roles=("teacher",), session_id="s1")
        assert n == 1
        assert teacher.queue.qsize() == 1
        assert student1.queue.qsize() == 0
        assert student2.queue.qsize() == 0

        # Session-scoped fan-out reaches both roles of that session only.
        n = bus.publish({"type": "y"}, session_id="s1")
        assert n == 2
        assert student1.queue.qsize() == 1
        assert student2.queue.qsize() == 0

        bus.unsubscribe(student1)
        assert bus.counts() == {"teacher": 1, "student": 1}

    asyncio.run(scenario())


def test_slow_client_drops_oldest_and_counts():
    async def scenario():
        bus = EventBus(queue_size=2)
        sub = bus.subscribe("student", "s1")
        for i in range(5):
            bus.publish({"i": i}, session_id="s1")
        # 2 events fit cleanly; 3 arrived to a full queue, evicted the oldest,
        # and were counted as dropped.
        assert sub.dropped == 3
        assert sub.delivered == 2
        assert sub.queue.qsize() == 2
        # The two newest events survive.
        items = [sub.queue.get_nowait()["i"] for _ in range(2)]
        assert items == [3, 4]

    asyncio.run(scenario())


def test_publish_with_no_subscribers_is_zero():
    bus = EventBus()
    assert bus.publish({"type": "x"}) == 0


def test_stats_shape():
    async def scenario():
        bus = EventBus(queue_size=8)
        bus.subscribe("student", "s1")
        bus.publish({"a": 1}, session_id="s1")
        stats = bus.stats()
        assert stats[0]["role"] == "student"
        assert stats[0]["delivered"] == 1
        assert stats[0]["dropped"] == 0
        assert stats[0]["queued"] == 1

    asyncio.run(scenario())


def test_ready_audio_is_fanned_out_independently_to_every_student():
    bus = EventBus()
    a = bus.subscribe("student", "s1")
    b = bus.subscribe("student", "s1")
    foreign = bus.subscribe("student", "s2")
    event = {"type": "audio.ready", "session_id": "s1", "segment_id": "seg-a",
             "seq": 1, "audio_url": "/api/student/audio/seg-a"}

    assert bus.publish(event, session_id="s1") == 2
    assert a.queue.get_nowait() == event
    assert b.queue.get_nowait() == event
    assert foreign.queue.empty()
