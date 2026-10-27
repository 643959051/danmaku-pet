from __future__ import annotations

import time

from danmaku_pet.decision.gifts import decide_gift
from danmaku_pet.models import Event


def gift(name: str, count: int = 1) -> Event:
    return Event(
        source="gift_sim",
        user="阿星",
        text=f"送出 {name}",
        gift_name=name,
        gift_count=count,
        timestamp=time.time(),
    )


def test_carnival_is_high_priority_spin() -> None:
    result = decide_gift(gift("嘉年华"))
    assert result.source == "gift_script"
    assert result.plan.priority == "high"
    assert result.plan.motion == "spin"
    assert result.plan.interrupt is True
    assert "阿星" in result.plan.reply


def test_alias_and_count_are_reflected() -> None:
    result = decide_gift(gift("小心心", 3))
    assert "小心心 × 3" in result.plan.reply
    assert result.plan.motion == "shy"


def test_unknown_gift_uses_fallback() -> None:
    result = decide_gift(gift("宇宙飞船"))
    assert result.plan.priority == "high"
    assert result.plan.motion == "wave"
    assert result.raw["canonical"] == "礼物"


def test_expensive_gift_has_longer_hold() -> None:
    result = decide_gift(gift("火箭"))
    assert result.raw["min_hold_ms"] >= 2800
