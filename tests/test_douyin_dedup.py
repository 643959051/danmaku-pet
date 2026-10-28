from __future__ import annotations

import time

from danmaku_pet.douyin.dedup import MessageDeduplicator, event_dedup_key
from danmaku_pet.models import Event


def event(
    *,
    text: str = "你好",
    user: str = "小满",
    user_id: str = "u-1",
    message_id: str = "",
    timestamp: float | None = None,
    room_id: str = "room-a",
) -> Event:
    return Event(
        source="douyin_comment",
        user=user,
        user_id=user_id,
        text=text,
        room_id=room_id,
        message_id=message_id,
        timestamp=timestamp if timestamp is not None else time.time(),
    )


def test_stable_message_id_wins() -> None:
    dedup = MessageDeduplicator(ttl_s=600)
    first = event(message_id="msg-1")
    retry = event(message_id="msg-1", timestamp=time.time() + 100)
    assert dedup.accept(first) is True
    assert dedup.accept(retry) is False


def test_different_rooms_do_not_collide() -> None:
    dedup = MessageDeduplicator()
    assert dedup.accept(event(message_id="msg-1", room_id="room-a")) is True
    assert dedup.accept(event(message_id="msg-1", room_id="room-b")) is True


def test_composite_fallback_collapses_same_second_duplicates() -> None:
    now = time.time()
    now -= now % 1  # align to a second boundary so +0.2 stays in the same bucket
    dedup = MessageDeduplicator()
    assert dedup.accept(event(text="同一个", timestamp=now)) is True
    assert dedup.accept(event(text="同一个", timestamp=now + 0.2)) is False
    assert dedup.accept(event(text="同一个", timestamp=now + 2.0)) is True


def test_composite_distinguishes_users_and_text() -> None:
    now = time.time()
    dedup = MessageDeduplicator()
    assert dedup.accept(event(text="A", user_id="u-1", timestamp=now)) is True
    assert dedup.accept(event(text="B", user_id="u-1", timestamp=now)) is True
    assert dedup.accept(event(text="A", user_id="u-2", timestamp=now)) is True


def test_composite_includes_gift_fields() -> None:
    now = time.time()
    first = Event(
        source="douyin_gift",
        user="A",
        user_id="u-1",
        text="送出 玫瑰",
        gift_name="玫瑰",
        gift_id="g-1",
        gift_count=1,
        room_id="r",
        timestamp=now,
    )
    second = Event(
        source="douyin_gift",
        user="A",
        user_id="u-1",
        text="送出 玫瑰",
        gift_name="玫瑰",
        gift_id="g-1",
        gift_count=2,
        room_id="r",
        timestamp=now,
    )
    assert event_dedup_key(first) != event_dedup_key(second)
    dedup = MessageDeduplicator()
    assert dedup.accept(first) is True
    assert dedup.accept(second) is True


def test_ttl_expiry_allows_a_replay() -> None:
    clock = {"now": 1000.0}
    dedup = MessageDeduplicator(ttl_s=10, clock=lambda: clock["now"])
    assert dedup.accept(event(message_id="m"), now=clock["now"]) is True
    assert dedup.accept(event(message_id="m"), now=clock["now"]) is False
    clock["now"] += 11
    assert dedup.accept(event(message_id="m"), now=clock["now"]) is True
