from __future__ import annotations

import time
from typing import Any

import pytest

from danmaku_pet.broadcaster import Broadcaster
from danmaku_pet.cloud.config import CloudConfig
from danmaku_pet.cloud.content_safety import ContentGuard
from danmaku_pet.cloud.metrics import StageMetrics
from danmaku_pet.cloud.orchestrator import CloudOrchestrator
from danmaku_pet.cloud.sessions import SessionRegistry
from danmaku_pet.cloud.state import MemoryStateStore
from danmaku_pet.decision.validator import FALLBACK_REPLY
from danmaku_pet.douyin.content_security import ContentSecurityVerdict
from danmaku_pet.shared.commands import PerformResult
from tests.support import (
    FakeEngine,
    FakeGateway,
    completed_result,
    make_event,
    make_plan,
)


class FailingEngine:
    name = "failing"

    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, event, memory):
        self.calls += 1
        raise RuntimeError("deepseek unreachable")

    async def close(self) -> None:
        return None


class FakeContentSecurity:
    def __init__(self, verdict: ContentSecurityVerdict) -> None:
        self.verdict = verdict
        self.calls = 0

    async def check(self, text: str, *, user: str = "") -> ContentSecurityVerdict:
        self.calls += 1
        return self.verdict


def build_orchestrator(
    config: CloudConfig,
    *,
    engine: Any | None = None,
    gateway: FakeGateway | None = None,
    store: MemoryStateStore | None = None,
    content_security: Any | None = None,
):
    broadcaster = Broadcaster()
    metrics = StageMetrics()
    sessions = SessionRegistry(idle_timeout_s=30)
    guard = ContentGuard(
        blocked_words=config.blocked_words,
        max_comment_chars=config.max_comment_chars,
        max_reply_chars=config.max_reply_chars,
    )
    engine = engine or FakeEngine()
    gateway = gateway or FakeGateway()
    store = store or MemoryStateStore(room_ttl_s=config.room_ttl_s)
    orchestrator = CloudOrchestrator(
        config=config,
        broadcaster=broadcaster,
        engine=engine,
        gateway=gateway,  # type: ignore[arg-type]
        sessions=sessions,
        guard=guard,
        metrics=metrics,
        store=store,
        content_security=content_security,
    )
    return orchestrator, gateway, engine, guard, metrics, store


async def state_of(store: MemoryStateStore, room_id: str = "room-a"):
    return await store.get_room(room_id)


async def test_comment_goes_to_pending_when_auto_reply_is_off(
    cloud_config: CloudConfig,
) -> None:
    orchestrator, gateway, engine, _, _, store = build_orchestrator(cloud_config)
    await orchestrator.start()
    try:
        await orchestrator.handle_event(make_event(text="在吗", message_id="m1"))
        state = await store.get_room("room-a")
        assert len(state.pending) == 1
        assert state.pending[0]["text"] == "在吗"
        assert gateway.sent == []
        assert engine.calls == 0
    finally:
        await orchestrator.shutdown()


async def test_manual_reply_creates_command_and_records_context(
    cloud_config: CloudConfig,
) -> None:
    orchestrator, gateway, engine, _, _, store = build_orchestrator(cloud_config)
    await orchestrator.start()
    try:
        await orchestrator.handle_event(
            make_event(text="你好", user="小满", message_id="m1")
        )
        await orchestrator.reply_to("room-a")

        assert len(gateway.sent) == 1
        command = gateway.sent[0]
        assert engine.calls == 1
        assert command.room_id == "room-a"
        assert command.event.user == "小满"
        assert command.read_aloud is False
        assert 7.0 <= command.lifetime_s <= 9.0

        state = await store.get_room("room-a")
        assert state.memory and state.memory[0]["reply"] == "回应 你好"
        assert state.stats["dispatched"] == 1

        await orchestrator.handle_result(completed_result(command))
        state = await store.get_room("room-a")
        assert state.stats["completed"] == 1
        assert len(state.memory) == 1
    finally:
        await orchestrator.shutdown()


async def test_auto_reply_reads_danmaku_aloud(cloud_config: CloudConfig) -> None:
    cloud_config.auto_reply = True
    orchestrator, gateway, _, _, _, _ = build_orchestrator(cloud_config)
    await orchestrator.start()
    try:
        await orchestrator.handle_event(make_event(text="你好", user="小满", message_id="m1"))
        assert len(gateway.sent) == 1
        assert gateway.sent[0].read_aloud is True
    finally:
        await orchestrator.shutdown()


async def test_gift_bypasses_model_and_does_not_wait_for_the_client(
    cloud_config: CloudConfig,
) -> None:
    cloud_config.auto_reply = True
    orchestrator, gateway, engine, _, _, store = build_orchestrator(cloud_config)
    await orchestrator.start()
    try:
        await orchestrator.handle_event(make_event(text="先聊会", message_id="m1"))
        gift = make_event(
            text="送出 嘉年华",
            user="阿星",
            source="gift_sim",
            gift_name="嘉年华",
            gift_count=1,
            message_id="g1",
        )
        await orchestrator.handle_event(gift)

        # 请求内完成：两条指令立即下发，客户端负责抢占；云端不再等待回执。
        assert len(gateway.sent) == 2
        assert engine.calls == 1, "礼物不应调用模型"
        gift_command = gateway.sent[1]
        assert gift_command.plan.priority == "high"
        assert gift_command.plan.interrupt is True
        assert gift_command.plan.motion == "spin"
        assert gift_command.plan.min_hold_ms == 3200
        assert 20.0 <= gift_command.lifetime_s <= 35.0

        # 客户端回执把统计补齐：普通表演被打断，礼物完成。
        await orchestrator.handle_result(
            PerformResult(
                command_id=gateway.sent[0].command_id,
                room_id="room-a",
                status="interrupted",
                error_code="interrupted",
            )
        )
        await orchestrator.handle_result(completed_result(gift_command))
        state = await store.get_room("room-a")
        assert state.stats["interrupted"] == 1
        assert state.stats["completed"] == 1
        assert len(state.memory) == 1, "礼物不写入对话上下文"
    finally:
        await orchestrator.shutdown()


async def test_duplicate_message_is_not_dispatched_twice(cloud_config: CloudConfig) -> None:
    cloud_config.auto_reply = True
    orchestrator, gateway, _, _, _, store = build_orchestrator(cloud_config)
    await orchestrator.start()
    try:
        await orchestrator.handle_event(make_event(text="重复弹幕", message_id="dup-1"))
        assert len(gateway.sent) == 1
        await orchestrator.handle_result(completed_result(gateway.sent[0]))

        await orchestrator.handle_event(make_event(text="重复弹幕", message_id="dup-1"))
        state = await store.get_room("room-a")
        assert state.stats["duplicates"] == 1
        assert len(gateway.sent) == 1
    finally:
        await orchestrator.shutdown()


async def test_official_push_dedup_uses_the_external_store(
    cloud_config: CloudConfig,
) -> None:
    cloud_config.auto_reply = True
    orchestrator, gateway, _, _, _, _ = build_orchestrator(cloud_config)
    await orchestrator.start()
    try:
        event = make_event(
            text="官方弹幕",
            source="douyin_comment",
            message_id="official-1",
        )
        assert await orchestrator.handle_official_event(event) is True
        assert await orchestrator.handle_official_event(event) is False
        assert len(gateway.sent) == 1
    finally:
        await orchestrator.shutdown()


async def test_rooms_are_isolated(cloud_config: CloudConfig) -> None:
    cloud_config.auto_reply = True
    orchestrator, gateway, _, _, _, store = build_orchestrator(cloud_config)
    await orchestrator.start()
    try:
        await orchestrator.handle_event(
            make_event(text="A 的弹幕", user="A", room_id="room-a")
        )
        await orchestrator.handle_event(
            make_event(text="B 的弹幕", user="B", room_id="room-b")
        )
        assert {command.room_id for command in gateway.sent} == {"room-a", "room-b"}

        room_a = await store.get_room("room-a")
        room_b = await store.get_room("room-b")
        assert room_a is not room_b
        for command in gateway.sent:
            await orchestrator.handle_result(completed_result(command))
        room_a = await store.get_room("room-a")
        room_b = await store.get_room("room-b")
        assert room_a.stats["completed"] == 1
        assert room_b.stats["completed"] == 1
        assert room_a.last_danmaku is not None and room_a.last_danmaku["user"] == "A"
        assert room_b.last_danmaku is not None and room_b.last_danmaku["user"] == "B"
        assert len(room_a.memory) == 1 and len(room_b.memory) == 1
    finally:
        await orchestrator.shutdown()


async def test_two_events_dispatch_immediately_without_worker_waiting(
    cloud_config: CloudConfig,
) -> None:
    """客户端负责单动作排序；云端绝不为等待回执而挂起请求/后台任务。"""

    cloud_config.auto_reply = True
    orchestrator, gateway, _, _, _, store = build_orchestrator(cloud_config)
    await orchestrator.start()
    try:
        await orchestrator.handle_event(make_event(text="第一条", message_id="m1"))
        await orchestrator.handle_event(make_event(text="第二条", message_id="m2"))
        assert len(gateway.sent) == 2
        state = await store.get_room("room-a")
        assert state.stats["dispatched"] == 2
    finally:
        await orchestrator.shutdown()


async def test_blocked_comment_is_dropped(cloud_config: CloudConfig) -> None:
    cloud_config.blocked_words = ("敏感",)
    orchestrator, gateway, engine, _, _, store = build_orchestrator(cloud_config)
    await orchestrator.start()
    try:
        await orchestrator.handle_event(make_event(text="这是敏感内容", message_id="m1"))
        state = await store.get_room("room-a")
        assert state.stats["dropped"] == 1
        assert engine.calls == 0
        assert gateway.sent == []
    finally:
        await orchestrator.shutdown()


async def test_platform_unsafe_nickname_never_reaches_tts(
    cloud_config: CloudConfig,
) -> None:
    cloud_config.auto_reply = True
    security = FakeContentSecurity(
        ContentSecurityVerdict(
            False, status="unsafe", unsafe_fields=("user",), reason="nickname"
        )
    )
    orchestrator, gateway, engine, _, _, store = build_orchestrator(
        cloud_config, content_security=security
    )
    await orchestrator.start()
    try:
        await orchestrator.handle_event(
            make_event(text="正常弹幕", user="坏昵称", message_id="m1")
        )
        state = await store.get_room("room-a")
        assert security.calls == 1
        assert state.stats["dropped"] == 1
        assert gateway.sent == []
        assert engine.calls == 0, "未通过内容安全的内容不应进入模型/TTS"
    finally:
        await orchestrator.shutdown()


async def test_content_security_unavailable_is_fail_closed_by_default(
    cloud_config: CloudConfig,
) -> None:
    cloud_config.auto_reply = True
    security = FakeContentSecurity(
        ContentSecurityVerdict(False, status="unavailable", reason="down")
    )
    orchestrator, gateway, _, _, _, store = build_orchestrator(
        cloud_config, content_security=security
    )
    await orchestrator.start()
    try:
        await orchestrator.handle_event(make_event(text="弹幕", message_id="m1"))
        state = await store.get_room("room-a")
        assert state.stats["dropped"] == 1
        assert gateway.sent == []
    finally:
        await orchestrator.shutdown()


async def test_content_security_unavailable_can_fail_open_without_tts(
    cloud_config: CloudConfig,
) -> None:
    cloud_config.auto_reply = True
    cloud_config.douyin.content_security_fail_closed = False
    security = FakeContentSecurity(
        ContentSecurityVerdict(False, status="unavailable", reason="down")
    )
    orchestrator, gateway, _, _, _, _ = build_orchestrator(
        cloud_config, content_security=security
    )
    await orchestrator.start()
    try:
        await orchestrator.handle_event(make_event(text="弹幕", message_id="m1"))
        assert len(gateway.sent) == 1
        assert gateway.sent[0].read_aloud is False, "检测不可用时也不能转语音"
    finally:
        await orchestrator.shutdown()


async def test_manual_reply_rejects_unsafe_content(cloud_config: CloudConfig) -> None:
    cloud_config.auto_reply = False
    orchestrator, gateway, _, _, _, _ = build_orchestrator(cloud_config)
    await orchestrator.start()
    try:
        await orchestrator.handle_event(make_event(text="正常弹幕", message_id="m1"))
        orchestrator.content_security = FakeContentSecurity(
            ContentSecurityVerdict(
                False, status="unsafe", unsafe_fields=("comment",), reason="bad"
            )
        )
        with pytest.raises(ValueError):
            await orchestrator.reply_to("room-a")
        assert gateway.sent == []
    finally:
        await orchestrator.shutdown()


async def test_blocked_reply_is_replaced(cloud_config: CloudConfig) -> None:
    cloud_config.auto_reply = True
    cloud_config.blocked_words = ("敏感",)
    engine = FakeEngine(reply="含有敏感词")
    orchestrator, gateway, _, guard, metrics, _ = build_orchestrator(
        cloud_config, engine=engine
    )
    await orchestrator.start()
    try:
        await orchestrator.handle_event(make_event(text="普通弹幕", message_id="m1"))
        assert len(gateway.sent) == 1
        assert "敏感" not in gateway.sent[0].plan.reply
        assert gateway.sent[0].plan.reply == guard.fallback_reply
        assert metrics.snapshot()["counters"].get("replies_sanitized") == 1
    finally:
        await orchestrator.shutdown()


async def test_decision_failure_degrades_to_fallback_plan(cloud_config: CloudConfig) -> None:
    cloud_config.auto_reply = True
    engine = FailingEngine()
    orchestrator, gateway, _, _, metrics, _ = build_orchestrator(
        cloud_config, engine=engine
    )
    await orchestrator.start()
    try:
        await orchestrator.handle_event(
            make_event(text="DeepSeek 挂了也要有反应", message_id="m1")
        )
        assert len(gateway.sent) == 1
        assert gateway.sent[0].plan.reply == FALLBACK_REPLY
        assert engine.calls == 1
        assert metrics.snapshot()["counters"].get("decisions_fallback") == 1
    finally:
        await orchestrator.shutdown()


async def test_normal_full_width_reply_is_left_untouched(cloud_config: CloudConfig) -> None:
    cloud_config.auto_reply = True
    engine = FakeEngine(reply="好呀！")
    orchestrator, gateway, _, _, metrics, _ = build_orchestrator(
        cloud_config, engine=engine
    )
    await orchestrator.start()
    try:
        await orchestrator.handle_event(make_event(text="你好", message_id="m1"))
        assert gateway.sent[0].plan.reply == "好呀！ 你好"
        assert metrics.snapshot()["counters"].get("replies_sanitized") is None
    finally:
        await orchestrator.shutdown()


async def test_paused_room_drops_events_until_resumed(cloud_config: CloudConfig) -> None:
    cloud_config.auto_reply = True
    orchestrator, gateway, _, _, _, store = build_orchestrator(cloud_config)
    await orchestrator.start()
    try:
        await orchestrator.handle_event(make_event(text="第一条", message_id="m1"))
        assert len(gateway.sent) == 1
        await orchestrator.handle_result(completed_result(gateway.sent[0]))

        await orchestrator.set_paused("room-a", True)
        await orchestrator.handle_event(make_event(text="暂停期间", message_id="m2"))
        state = await store.get_room("room-a")
        assert state.stats["dropped"] >= 1
        assert len(gateway.sent) == 1

        await orchestrator.set_paused("room-a", False)
        await orchestrator.handle_event(make_event(text="恢复之后", message_id="m3"))
        assert len(gateway.sent) == 2
    finally:
        await orchestrator.shutdown()


async def test_interrupt_marks_current_and_notifies_gateway(
    cloud_config: CloudConfig,
) -> None:
    cloud_config.auto_reply = True
    orchestrator, gateway, _, _, _, store = build_orchestrator(cloud_config)
    await orchestrator.start()
    try:
        await orchestrator.handle_event(make_event(text="长表演", message_id="m1"))
        assert len(gateway.sent) == 1
        sent = await orchestrator.interrupt("room-a")
        assert sent is True
        assert gateway.interrupts and gateway.interrupts[0][0] == "room-a"
        state = await store.get_room("room-a")
        assert state.stats["interrupted"] == 1
        current = state.current_entry()
        assert current is None or current["status"] == "interrupted"
    finally:
        await orchestrator.shutdown()


async def test_state_survives_a_new_orchestrator_instance(cloud_config: CloudConfig) -> None:
    """模拟平台换实例：共享同一个外部状态存储，请求仍能读到上下文/待回复。"""

    cloud_config.auto_reply = False
    store = MemoryStateStore(room_ttl_s=cloud_config.room_ttl_s)
    first, _, _, _, _, _ = build_orchestrator(cloud_config, store=store)
    await first.start()
    await first.handle_event(make_event(text="跨实例弹幕", message_id="m1"))

    second, gateway, _, _, _, _ = build_orchestrator(cloud_config, store=store)
    await second.start()
    try:
        state = await store.get_room("room-a")
        assert len(state.pending) == 1
        await second.reply_to("room-a")
        assert len(gateway.sent) == 1
        assert gateway.sent[0].event.text == "跨实例弹幕"
    finally:
        await second.shutdown()
        await first.shutdown()


async def test_unknown_result_is_reported_unhandled(cloud_config: CloudConfig) -> None:
    orchestrator, _, _, _, _, _ = build_orchestrator(cloud_config)
    await orchestrator.start()
    try:
        handled = await orchestrator.handle_result(
            PerformResult(command_id="missing", room_id="room-a", status="completed")
        )
        assert handled is False
    finally:
        await orchestrator.shutdown()


async def test_room_isolation_rejects_mismatched_queue_put(cloud_config: CloudConfig) -> None:
    from danmaku_pet.queueing import EventQueue

    queue = EventQueue(room_id="room-a")
    with pytest.raises(ValueError):
        await queue.put(make_event(room_id="room-b"))
