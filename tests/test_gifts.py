from __future__ import annotations

import time

from danmaku_pet.decision.gifts import choose_rule, decide_gift
from danmaku_pet.models import Event


def gift(name: str, count: int = 1, gift_id: str = "") -> Event:
    return Event(
        source="gift_sim",
        user="阿星",
        text=f"送出 {name}",
        gift_name=name,
        gift_id=gift_id,
        gift_count=count,
        timestamp=time.time(),
    )


def test_carnival_is_high_priority_spin() -> None:
    result = decide_gift(gift("嘉年华"))
    assert result.source == "gift_script"
    assert result.plan.priority == "high"
    assert result.plan.motion == "spin"
    assert result.plan.interrupt is True
    assert result.plan.min_hold_ms == 3200
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
    assert result.plan.min_hold_ms >= 2800


def test_gift_id_mapping_takes_priority_over_name() -> None:
    # 平台只保证 gift_id 稳定；名称可能是地区化/可变的。
    event = gift("神秘礼物", gift_id="gid-carnival")
    result = decide_gift(event, gift_ids={"gid-carnival": "嘉年华"})
    assert result.plan.motion == "spin"
    assert result.raw["canonical"] == "嘉年华"
    assert result.raw["gift_id"] == "gid-carnival"


def test_choose_rule_prefers_mapped_id() -> None:
    assert choose_rule("未知", "id-1", gift_ids={"id-1": "火箭"}).canonical == "火箭"
    assert choose_rule("未知", "id-2", gift_ids={"id-1": "火箭"}).canonical == "礼物"
