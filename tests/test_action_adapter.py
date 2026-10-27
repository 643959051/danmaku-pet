from __future__ import annotations

import time

from danmaku_pet.execution.action_adapter import MOTION_MAP, ActionAdapter
from danmaku_pet.models import MOTIONS, ActionPlan, Event


def plan(motion: str, emotion: str = "neutral") -> ActionPlan:
    return ActionPlan(
        reply="测试回复",
        emotion=emotion,  # type: ignore[arg-type]
        motion=motion,  # type: ignore[arg-type]
        priority="normal",
        interrupt=False,
    )


def event() -> Event:
    return Event(source="manual", user="A", text="hi", timestamp=time.time())


def test_all_motions_have_a_mapping() -> None:
    assert set(MOTION_MAP) == set(MOTIONS)
    table = ActionAdapter.mapping_table()
    assert len(table) == 9


def test_spin_uses_dafeiyu_success_pulse() -> None:
    cue = ActionAdapter().build_cue(plan("spin"), event(), 1200)
    assert cue.mode == "pulse"
    assert cue.native_state == "SUCCESS"
    assert cue.transform == "spin"
    assert cue.hold_ms >= 3000


def test_idle_with_speech_uses_talk_state() -> None:
    cue = ActionAdapter().build_cue(plan("idle"), event(), 2600)
    assert cue.mode == "talk"
    assert cue.native_state == "WORKING"
    assert cue.hold_ms > 2600


def test_action_never_ends_before_speech() -> None:
    cue = ActionAdapter().build_cue(plan("nod"), event(), 5000)
    assert cue.hold_ms >= 5300


def test_gift_min_hold_override() -> None:
    cue = ActionAdapter().build_cue(plan("spin"), event(), 1000, min_hold_override=4200)
    assert cue.hold_ms == 4200
