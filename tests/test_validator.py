from __future__ import annotations

import time

from danmaku_pet.decision.validator import normalize_plan
from danmaku_pet.models import Event


def event(**kwargs) -> Event:
    return Event(source="manual", user="A", text="hello", timestamp=time.time(), **kwargs)


def test_invalid_motion_falls_back_to_nod_for_happy() -> None:
    plan, warnings = normalize_plan(
        {"reply": "好呀", "emotion": "happy", "motion": "dance", "priority": "normal", "interrupt": False},
        event(),
    )
    assert plan.motion == "nod"
    assert any("motion" in item for item in warnings)


def test_invalid_motion_falls_back_to_idle_for_sad() -> None:
    plan, _ = normalize_plan(
        {"reply": "抱抱", "emotion": "sad", "motion": "cry", "priority": "normal", "interrupt": False},
        event(),
    )
    assert plan.motion == "idle"


def test_invalid_emotion_becomes_neutral() -> None:
    plan, _ = normalize_plan(
        {"reply": "嗯", "emotion": "excited", "motion": "nod", "priority": "normal", "interrupt": False},
        event(),
    )
    assert plan.emotion == "neutral"


def test_gift_priority_is_forced_high_and_interrupt_true() -> None:
    gift = Event(
        source="gift_sim",
        user="A",
        text="送出玫瑰",
        gift_name="玫瑰",
        gift_count=1,
        timestamp=time.time(),
    )
    plan, _ = normalize_plan(
        {"reply": "谢谢", "emotion": "happy", "motion": "wave", "priority": "normal", "interrupt": False},
        gift,
    )
    assert plan.priority == "high"
    assert plan.interrupt is True


def test_empty_reply_gets_fallback() -> None:
    plan, warnings = normalize_plan(
        {"reply": "   ", "emotion": "neutral", "motion": "idle", "priority": "normal", "interrupt": False},
        event(),
    )
    assert plan.reply
    assert warnings


def test_reply_whitespace_and_length_are_sanitized() -> None:
    plan, _ = normalize_plan(
        {"reply": "a\nb" + "x" * 300, "emotion": "neutral", "motion": "idle", "priority": "normal"},
        event(),
    )
    assert "\n" not in plan.reply
    assert len(plan.reply) <= 240
