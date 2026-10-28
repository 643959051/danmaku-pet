"""Shared fakes for the cloud/client tests (not collected as tests)."""

from __future__ import annotations

import time
from typing import Any

from danmaku_pet.decision.context import ConversationMemory
from danmaku_pet.shared.commands import PerformCommand, PerformResult
from danmaku_pet.shared.events import Event
from danmaku_pet.shared.plans import ActionPlan, DecisionResult


def make_plan(
    *,
    reply: str = "好呀",
    motion: str = "wave",
    emotion: str = "happy",
    priority: str = "normal",
    interrupt: bool = False,
    min_hold_ms: int = 0,
) -> ActionPlan:
    return ActionPlan(
        reply=reply,
        emotion=emotion,  # type: ignore[arg-type]
        motion=motion,  # type: ignore[arg-type]
        priority=priority,  # type: ignore[arg-type]
        interrupt=interrupt,
        min_hold_ms=min_hold_ms,
    )


def make_event(
    *,
    text: str = "你好",
    user: str = "观众",
    room_id: str = "room-a",
    source: str = "manual",
    gift_name: str | None = None,
    gift_id: str = "",
    gift_count: int = 0,
    message_id: str = "",
) -> Event:
    return Event(
        source=source,  # type: ignore[arg-type]
        user=user,
        text=text,
        gift_name=gift_name,
        gift_id=gift_id,
        gift_count=gift_count,
        room_id=room_id,
        message_id=message_id,
        timestamp=time.time(),
    )


def make_command(
    *,
    room_id: str = "room-a",
    plan: ActionPlan | None = None,
    event: Event | None = None,
    command_id: str = "cmd_test",
    expires_in: float = 10.0,
    created_at: float | None = None,
    read_aloud: bool = False,
) -> PerformCommand:
    now = created_at if created_at is not None else time.time()
    return PerformCommand(
        command_id=command_id,
        room_id=room_id,
        event=event or make_event(room_id=room_id),
        plan=plan or make_plan(),
        created_at=now,
        expires_at=now + expires_in if expires_in >= 0 else now - 1,
        read_aloud=read_aloud,
    )


class FakeEngine:
    name = "fake"

    def __init__(self, *, reply: str = "回应") -> None:
        self.calls = 0
        self.reply = reply
        self.last_event: Event | None = None

    async def decide(self, event: Event, memory: ConversationMemory) -> DecisionResult:
        self.calls += 1
        self.last_event = event
        return DecisionResult(
            plan=make_plan(reply=f"{self.reply} {event.text}"),
            source="fake",
            model="fake-model",
        )

    async def close(self) -> None:
        return None


class FakeGateway:
    mode = "fake"

    def __init__(self) -> None:
        self.sent: list[PerformCommand] = []
        self.interrupts: list[tuple[str, str]] = []
        self.accept = True
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def send(self, command: PerformCommand) -> bool:
        if self.accept:
            self.sent.append(command)
        return self.accept

    async def interrupt(self, room_id: str, reason: str) -> bool:
        self.interrupts.append((room_id, reason))
        return True

    async def close(self) -> None:
        self.started = False

    def status(self) -> dict[str, Any]:
        return {"mode": self.mode, "sent": len(self.sent)}


class FakeExecutor:
    """Executes commands instantly; records them for assertions."""

    def __init__(self, *, delay: float = 0.0, status: str = "completed") -> None:
        self.delay = delay
        self.status = status
        self.performed: list[PerformCommand] = []
        self.interrupts = 0
        self.active = 0
        self.max_active = 0

    async def perform(self, command: PerformCommand, *, on_started=None) -> PerformResult:
        self.performed.append(command)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if on_started is not None:
                await on_started(900)
            if self.delay:
                await asyncio_sleep(self.delay)
            return PerformResult(
                command_id=command.command_id,
                room_id=command.room_id,
                status=self.status,  # type: ignore[arg-type]
                speech_ms=900,
                client_id="test-client",
            )
        finally:
            self.active -= 1

    async def interrupt(self) -> None:
        self.interrupts += 1


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def completed_result(command: PerformCommand, *, speech_ms: int = 1200) -> PerformResult:
    return PerformResult(
        command_id=command.command_id,
        room_id=command.room_id,
        status="completed",
        speech_ms=speech_ms,
        client_id="test-client",
    )
