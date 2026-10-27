from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from danmaku_pet.broadcaster import Broadcaster
from danmaku_pet.decision.context import ConversationMemory
from danmaku_pet.execution.action_adapter import ActionAdapter
from danmaku_pet.models import ActionPlan, AudioClip, DecisionResult, Event
from danmaku_pet.orchestrator import Orchestrator


class FakeEngine:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, event: Event, memory: ConversationMemory) -> DecisionResult:
        self.calls += 1
        return DecisionResult(
            plan=ActionPlan(
                reply=f"回应 {event.text}",
                emotion="happy",
                motion="nod",
                priority="normal",
                interrupt=False,
            ),
            source="fake",
        )

    async def close(self) -> None:
        return None


class FakeTTS:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def synthesize(self, text: str) -> AudioClip:
        self.texts.append(text)
        return AudioClip(path=None, duration_ms=900, text=text)

    def status(self) -> dict[str, object]:
        return {"engine": "fake"}


class FakePlayer:
    def measure(self, path) -> int:
        return 900


class FakeBackend:
    def __init__(self) -> None:
        self.player = FakePlayer()
        self.performances = 0

    async def start(self) -> None:
        return None

    async def perform(self, plan, event, audio, cue) -> None:
        self.performances += 1
        await asyncio.sleep(0.01)

    async def interrupt(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def status(self) -> dict[str, object]:
        return {"desktop_pet": {}, "audio": {}}


@pytest.mark.asyncio
async def test_end_to_end_manual_event_completes() -> None:
    config = SimpleNamespace(max_context_rounds=10, queue_max=50, stale_after_s=3.0)
    broadcaster = Broadcaster()
    engine = FakeEngine()
    tts = FakeTTS()
    backend = FakeBackend()
    orchestrator = Orchestrator(
        config, broadcaster, engine, tts, backend, ActionAdapter()  # type: ignore[arg-type]
    )
    await orchestrator.start()
    await orchestrator.submit(Event(source="manual", user="A", text="你好", timestamp=time.time()))
    for _ in range(100):
        if orchestrator.stats["completed"] == 1:
            break
        await asyncio.sleep(0.01)
    assert orchestrator.stats["completed"] == 1
    assert backend.performances == 1
    assert tts.texts == ["回应 你好"]
    assert len(orchestrator.memory) == 1
    await orchestrator.shutdown()


@pytest.mark.asyncio
async def test_duplicate_events_are_serialized() -> None:
    config = SimpleNamespace(max_context_rounds=10, queue_max=50, stale_after_s=3.0)
    orchestrator = Orchestrator(
        config,
        Broadcaster(),
        FakeEngine(),
        FakeTTS(),
        FakeBackend(),
        ActionAdapter(),  # type: ignore[arg-type]
    )
    await orchestrator.start()
    for index in range(3):
        await orchestrator.submit(
            Event(source="manual", user="A", text=f"m{index}", timestamp=time.time())
        )
    for _ in range(200):
        if orchestrator.stats["completed"] == 3:
            break
        await asyncio.sleep(0.01)
    assert orchestrator.stats["completed"] == 3
    assert len(orchestrator.memory) == 3
    await orchestrator.shutdown()


@pytest.mark.asyncio
async def test_manual_reply_to_last_danmaku() -> None:
    config = SimpleNamespace(max_context_rounds=10, queue_max=50, stale_after_s=3.0)
    orchestrator = Orchestrator(
        config,
        Broadcaster(),
        FakeEngine(),
        FakeTTS(),
        FakeBackend(),
        ActionAdapter(),  # type: ignore[arg-type]
    )
    await orchestrator.start()
    first = Event(source="danmaku_sim", user="小满", text="你好", timestamp=time.time())
    await orchestrator.submit(first)
    for _ in range(100):
        if orchestrator.stats["completed"] == 1:
            break
        await asyncio.sleep(0.01)
    assert orchestrator.last_danmaku is not None
    replied = await orchestrator.reply_to()
    assert replied.source == "manual"
    assert replied.user == "小满"
    assert replied.text == "你好"
    for _ in range(100):
        if orchestrator.stats["completed"] == 2:
            break
        await asyncio.sleep(0.01)
    assert orchestrator.stats["completed"] == 2
    await orchestrator.shutdown()


@pytest.mark.asyncio
async def test_reply_without_any_danmaku_raises() -> None:
    config = SimpleNamespace(max_context_rounds=10, queue_max=50, stale_after_s=3.0)
    orchestrator = Orchestrator(
        config,
        Broadcaster(),
        FakeEngine(),
        FakeTTS(),
        FakeBackend(),
        ActionAdapter(),  # type: ignore[arg-type]
    )
    await orchestrator.start()
    with pytest.raises(LookupError):
        await orchestrator.reply_to()
    await orchestrator.shutdown()


@pytest.mark.asyncio
async def test_auto_reply_off_keeps_danmaku_pending_until_manual_reply() -> None:
    config = SimpleNamespace(
        max_context_rounds=10, queue_max=50, stale_after_s=3.0, auto_reply=False
    )
    tts = FakeTTS()
    orchestrator = Orchestrator(
        config,
        Broadcaster(),
        FakeEngine(),
        tts,
        FakeBackend(),
        ActionAdapter(),  # type: ignore[arg-type]
    )
    await orchestrator.start()
    incoming = Event(source="danmaku_sim", user="小满", text="在吗", timestamp=time.time())
    await orchestrator.ingest(incoming)
    assert orchestrator.stats["completed"] == 0
    assert len(orchestrator.pending) == 1
    assert orchestrator.pending_snapshot()[0]["event"]["text"] == "在吗"
    assert tts.texts == []
    await orchestrator.reply_to()
    for _ in range(100):
        if orchestrator.stats["completed"] == 1:
            break
        await asyncio.sleep(0.01)
    assert orchestrator.stats["completed"] == 1
    assert len(orchestrator.pending) == 0
    await orchestrator.shutdown()


@pytest.mark.asyncio
async def test_auto_reply_on_reads_danmaku_before_reply() -> None:
    config = SimpleNamespace(
        max_context_rounds=10, queue_max=50, stale_after_s=3.0, auto_reply=True
    )
    tts = FakeTTS()
    orchestrator = Orchestrator(
        config,
        Broadcaster(),
        FakeEngine(),
        tts,
        FakeBackend(),
        ActionAdapter(),  # type: ignore[arg-type]
    )
    await orchestrator.start()
    incoming = Event(source="danmaku_sim", user="小满", text="你好", timestamp=time.time())
    await orchestrator.ingest(incoming)
    for _ in range(100):
        if orchestrator.stats["completed"] == 1:
            break
        await asyncio.sleep(0.01)
    assert orchestrator.stats["completed"] == 1
    assert len(orchestrator.pending) == 0
    assert tts.texts[0] == "小满说：你好。回应 你好"
    await orchestrator.shutdown()

