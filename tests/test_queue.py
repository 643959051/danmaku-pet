from __future__ import annotations

import time

import pytest

from danmaku_pet.models import Event
from danmaku_pet.queueing import EventQueue


def event(source: str = "manual", text: str = "x", timestamp: float | None = None) -> Event:
    return Event(
        source=source,  # type: ignore[arg-type]
        user="A",
        text=text,
        gift_name="玫瑰" if source == "gift_sim" else None,
        gift_count=1 if source == "gift_sim" else 0,
        timestamp=timestamp if timestamp is not None else time.time(),
    )


@pytest.mark.asyncio
async def test_stale_event_is_dropped_before_fresh_one() -> None:
    queue = EventQueue(stale_after_s=3)
    stale = event(text="old", timestamp=time.time() - 9)
    fresh = event(text="new")
    await queue.put(stale)
    await queue.put(fresh)
    item, drops = await queue.get()
    assert item.event.event_id == fresh.event_id
    assert len(drops) == 1
    assert drops[0].event.event_id == stale.event_id


@pytest.mark.asyncio
async def test_read_aloud_flag_travels_with_event() -> None:
    queue = EventQueue(stale_after_s=3)
    item = event(text="念我")
    await queue.put(item, read_aloud=True)
    queued, _ = await queue.get()
    assert queued.event.event_id == item.event_id
    assert queued.read_aloud is True


@pytest.mark.asyncio
async def test_preempt_clears_normals_and_keeps_gifts() -> None:
    queue = EventQueue(stale_after_s=3)
    first = event()
    second = event()
    old_gift = event("gift_sim", "old gift")
    await queue.put(first)
    await queue.put(second)
    await queue.put(old_gift)
    gift = event("gift_sim", "new gift")
    drops = await queue.put(gift, preempt=True)
    assert len(drops) == 2
    item, _ = await queue.get()
    assert item.event.event_id == gift.event_id
    item2, _ = await queue.get()
    assert item2.event.event_id == old_gift.event_id


@pytest.mark.asyncio
async def test_full_queue_drops_oldest() -> None:
    queue = EventQueue(maxsize=2, stale_after_s=100)
    first = event(text="1")
    second = event(text="2")
    third = event(text="3")
    await queue.put(first)
    await queue.put(second)
    drops = await queue.put(third)
    assert drops[0].event.event_id == first.event_id
    item, _ = await queue.get()
    assert item.event.event_id == second.event_id
