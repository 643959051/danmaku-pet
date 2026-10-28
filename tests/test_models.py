from __future__ import annotations

import time

import pytest

from danmaku_pet.models import ActionPlan, Event


def test_manual_event_normalizes() -> None:
    event = Event(source="manual", user="  小明 ", text="  你好  ", timestamp=time.time())
    assert event.user == "小明"
    assert event.text == "你好"
    assert not event.is_gift
    assert event.display_text == "你好"
    assert event.age_seconds() >= 0


def test_gift_defaults() -> None:
    event = Event(source="gift_sim", user="A", text="", gift_name="玫瑰", timestamp=time.time())
    assert event.gift_count == 1
    assert event.display_text == "送出 玫瑰"
    assert event.is_gift
    assert event.message_type == "gift"


def test_unknown_source_rejected() -> None:
    with pytest.raises(ValueError):
        Event(source="invalid", user="A", text="x")  # type: ignore[arg-type]


def test_action_plan_is_strict_enum() -> None:
    with pytest.raises(Exception):
        ActionPlan(reply="x", emotion="ecstatic", motion="idle", priority="normal", interrupt=False)


def test_event_payload_contains_required_fields() -> None:
    event = Event(source="manual", user="A", text="x")
    payload = event.to_dict()
    for key in (
        "source",
        "user",
        "text",
        "gift_name",
        "gift_count",
        "timestamp",
        "room_id",
        "message_id",
        "message_type",
        "user_id",
        "gift_id",
        "is_gift",
    ):
        assert key in payload


def test_event_defaults_for_protocol_fields() -> None:
    event = Event(source="manual", user="A", text="x")
    assert event.room_id == ""
    assert event.message_id == ""
    assert event.user_id == ""
    assert event.gift_id == ""
    assert event.message_type == "comment"
    assert event.effective_message_id == event.event_id


def test_event_clamps_text_and_ids() -> None:
    event = Event(
        source="douyin_comment",
        user="u" * 100,
        text="t" * 900,
        room_id="r" * 300,
        message_id="m" * 300,
        user_id="i" * 300,
        gift_id="g" * 300,
    )
    assert len(event.user) <= 40
    assert len(event.text) <= 500
    assert len(event.room_id) <= 128
    assert len(event.message_id) <= 128
    assert len(event.user_id) <= 128
    assert len(event.gift_id) <= 128


def test_event_rejects_bad_gift_count() -> None:
    with pytest.raises(ValueError):
        Event(source="gift_sim", user="A", text="", gift_name="玫瑰", gift_count=-1)
