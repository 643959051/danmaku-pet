from __future__ import annotations

from datetime import datetime, timedelta, timezone

from danmaku_pet.decision.prompts import (
    build_messages,
    build_system_prompt,
    format_local_time,
)
from danmaku_pet.models import Event
from danmaku_pet.persona import DEFAULT_PERSONA

CST = timezone(timedelta(hours=8))


def make_event(text: str = "小鱼，现在几点了") -> Event:
    return Event(source="manual", user="嘟嘟嘟", text=text)


def test_local_time_is_stamped_with_date_and_weekday() -> None:
    now = datetime(2026, 10, 28, 1, 14, tzinfo=CST)
    assert format_local_time(now) == "2026-10-28 01:14 周三"


def test_current_turn_carries_the_local_time() -> None:
    now = datetime(2026, 10, 28, 1, 14, tzinfo=CST)
    messages = build_messages(make_event(), [], DEFAULT_PERSONA, now=now)

    assert messages[0]["role"] == "system"
    assert messages[-1]["role"] == "user"
    assert "当前时间：2026-10-28 01:14 周三" in messages[-1]["content"]
    assert "当前事件" in messages[-1]["content"]


def test_system_prompt_stays_identical_when_the_clock_moves() -> None:
    """时间只进最后一条 user 消息，system 前缀保持不变（DeepSeek 前缀缓存）。"""

    morning = build_messages(
        make_event(), [], DEFAULT_PERSONA, now=datetime(2026, 10, 28, 1, 14, tzinfo=CST)
    )
    quarter_past = build_messages(
        make_event(), [], DEFAULT_PERSONA, now=datetime(2026, 10, 28, 1, 15, tzinfo=CST)
    )

    assert morning[0] == quarter_past[0]
    assert morning[-1]["content"] != quarter_past[-1]["content"]


def test_system_prompt_tells_the_model_to_answer_time_questions() -> None:
    prompt = build_system_prompt(DEFAULT_PERSONA)
    assert "问时间" in prompt
    assert "当前时间" in prompt
    # 明确禁止“让观众自己看表”这类回避
    assert "看下自己的表" in prompt
