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


def test_unknown_source_rejected() -> None:
    with pytest.raises(ValueError):
        Event(source="invalid", user="A", text="x")  # type: ignore[arg-type]


def test_action_plan_is_strict_enum() -> None:
    with pytest.raises(Exception):
        ActionPlan(reply="x", emotion="ecstatic", motion="idle", priority="normal", interrupt=False)


def test_event_payload_contains_required_fields() -> None:
    event = Event(source="manual", user="A", text="x")
    payload = event.to_dict()
    for key in ("source", "user", "text", "gift_name", "gift_count", "timestamp"):
        assert key in payload
