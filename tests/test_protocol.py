from __future__ import annotations

import time

import pytest

from danmaku_pet.models import Event
from danmaku_pet.shared.commands import PerformCommand, PerformResult, speech_text_for
from danmaku_pet.shared.plans import ActionPlan
from danmaku_pet.shared.protocol import (
    DedupCache,
    ProtocolError,
    VersionMismatch,
    check_protocol_version,
    decode_message,
    encode_message,
    make_command_id,
)
from tests.support import make_command, make_event, make_plan


def test_event_roundtrip_keeps_protocol_fields() -> None:
    event = make_event(
        text="你好",
        user="小满",
        room_id="room-1",
        source="douyin_comment",
        message_id="m-1",
    )
    restored = Event.from_dict(event.to_dict())
    assert restored == event
    assert restored.message_type == "comment"
    assert restored.effective_message_id == "m-1"


def test_gift_event_infers_message_type_and_defaults() -> None:
    event = Event(source="manual", user="A", text="", gift_name="玫瑰")
    assert event.message_type == "gift"
    assert event.is_gift
    assert event.gift_count == 1
    assert Event.from_dict(event.to_dict()).message_type == "gift"


def test_event_from_dict_rejects_missing_source() -> None:
    with pytest.raises(ValueError):
        Event.from_dict({"user": "A", "text": "x"})


def test_command_roundtrip_and_expiry() -> None:
    command = make_command(expires_in=10, read_aloud=True)
    restored = PerformCommand.from_dict(command.to_dict())
    assert restored == command
    assert restored.is_expired(now=command.created_at + 1) is False
    assert restored.is_expired(now=command.created_at + 11) is True
    assert restored.remaining_ms(now=command.created_at) == 10_000


def test_command_requires_ids_and_plan() -> None:
    with pytest.raises(ValueError):
        PerformCommand(
            command_id="",
            room_id="r",
            event=make_event(),
            plan=make_plan(),
            created_at=time.time(),
            expires_at=time.time() + 1,
        )
    with pytest.raises(ValueError):
        make_command(expires_in=-1)


def test_result_validation_and_terminal() -> None:
    result = PerformResult(command_id="c", room_id="r", status="completed", speech_ms=1200)
    assert result.terminal is True and result.ok is True
    assert PerformResult.from_dict(result.to_dict()) == result
    assert PerformResult(command_id="c", room_id="r", status="started").terminal is False
    with pytest.raises(ValueError):
        PerformResult(command_id="c", room_id="r", status="done")


def test_speech_text_reads_danmaku_only_when_requested() -> None:
    event = make_event(text="你好呀", user="小满")
    plan = make_plan(reply="嗨")
    assert speech_text_for(event, plan, False) == "嗨"
    assert speech_text_for(event, plan, True) == "小满说：你好呀。嗨"
    gift = make_event(text="送出 火箭", gift_name="火箭", gift_count=1, source="gift_sim")
    assert speech_text_for(gift, plan, True) == "嗨"


def test_protocol_version_check() -> None:
    assert check_protocol_version(1) == 1
    with pytest.raises(VersionMismatch):
        check_protocol_version(2)
    with pytest.raises(VersionMismatch):
        check_protocol_version("x")


def test_encode_decode_envelope() -> None:
    line = encode_message("command", {"a": 1}, message_id="m1")
    envelope = decode_message(line)
    assert envelope.kind == "command"
    assert envelope.payload == {"a": 1}
    assert envelope.protocol_version == 1
    assert envelope.message_id == "m1"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        '{"kind":"nope","protocol_version":1,"payload":{}}',
        '{"kind":"command","protocol_version":9,"payload":{}}',
        '{"kind":"command","protocol_version":1,"payload":[]}',
    ],
)
def test_decode_rejects_bad_frames(raw: str) -> None:
    with pytest.raises(ProtocolError):
        decode_message(raw)


def test_command_id_is_stable_across_retries() -> None:
    first = make_event(text="你好", message_id="msg-42")
    retry = Event(
        source=first.source,
        user=first.user,
        text=first.text,
        message_id="msg-42",
        timestamp=first.timestamp + 1,
    )
    assert first.event_id != retry.event_id
    assert make_command_id("room-a", first) == make_command_id("room-a", retry)
    assert make_command_id("room-b", first) != make_command_id("room-a", first)


def test_dedup_cache_ttl_and_eviction() -> None:
    clock = {"now": 100.0}
    cache = DedupCache(ttl_s=10, maxsize=3, clock=lambda: clock["now"])
    assert cache.check_and_add("a") is True
    assert cache.check_and_add("a") is False
    clock["now"] += 11
    assert cache.check_and_add("a") is True
    for key in ("b", "c", "d", "e"):
        cache.check_and_add(key)
    assert len(cache) <= 3


def test_action_plan_min_hold_defaults_and_roundtrip() -> None:
    raw = {
        "reply": "好",
        "emotion": "happy",
        "motion": "nod",
        "priority": "normal",
        "interrupt": False,
    }
    plan = ActionPlan.from_dict(raw)
    assert plan.min_hold_ms == 0
    assert ActionPlan.from_dict(plan.to_dict()) == plan
