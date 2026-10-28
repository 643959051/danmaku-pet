from __future__ import annotations

import asyncio
import time

import pytest

from danmaku_pet.client.dispatcher import CommandDispatcher
from danmaku_pet.shared.commands import PerformCommand, PerformResult
from tests.support import FakeExecutor, make_command, make_plan


class ResultSink:
    def __init__(self) -> None:
        self.results: list[PerformResult] = []

    async def __call__(self, result: PerformResult) -> None:
        self.results.append(result)

    def statuses(self, command_id: str) -> list[str]:
        return [item.status for item in self.results if item.command_id == command_id]


async def wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met before timeout")


def build(executor: FakeExecutor | None = None) -> tuple[CommandDispatcher, ResultSink, FakeExecutor]:
    sink = ResultSink()
    executor = executor or FakeExecutor()
    dispatcher = CommandDispatcher(
        executor,  # type: ignore[arg-type]
        send_result=sink,
        client_id="test-client",
        grace_s=0.0,
    )
    return dispatcher, sink, executor


async def test_command_lifecycle_reports_accepted_started_completed() -> None:
    dispatcher, sink, executor = build()
    await dispatcher.start()
    try:
        command = make_command()
        assert await dispatcher.submit(command) == "queued"
        await wait_until(lambda: "completed" in sink.statuses(command.command_id))
        assert sink.statuses(command.command_id) == ["accepted", "started", "completed"]
        assert len(executor.performed) == 1
        assert dispatcher.status()["stats"]["completed"] == 1
    finally:
        await dispatcher.stop()


async def test_duplicate_command_is_never_performed_twice() -> None:
    dispatcher, sink, executor = build()
    await dispatcher.start()
    try:
        command = make_command()
        assert await dispatcher.submit(command) == "queued"
        assert await dispatcher.submit(command) == "duplicate"
        await wait_until(lambda: "completed" in sink.statuses(command.command_id))
        assert len(executor.performed) == 1
        assert dispatcher.status()["stats"]["duplicates"] == 1
    finally:
        await dispatcher.stop()


async def test_expired_command_reports_expired_without_performing() -> None:
    dispatcher, sink, executor = build()
    await dispatcher.start()
    try:
        command = make_command(created_at=time.time() - 30, expires_in=10)
        assert await dispatcher.submit(command) == "expired"
        await wait_until(lambda: bool(sink.results))
        assert [item.status for item in sink.results] == ["expired"]
        assert executor.performed == []
    finally:
        await dispatcher.stop()


async def test_gift_preempts_and_marks_the_old_command_interrupted() -> None:
    executor = FakeExecutor(delay=0.3)
    dispatcher, sink, executor = build(executor)
    await dispatcher.start()
    try:
        normal = make_command(command_id="cmd-normal")
        await dispatcher.submit(normal)
        await wait_until(lambda: "started" in sink.statuses(normal.command_id))

        gift = make_command(
            command_id="cmd-gift",
            plan=make_plan(
                motion="spin", priority="high", interrupt=True, min_hold_ms=3200
            ),
        )
        assert await dispatcher.submit(gift) == "queued"
        await wait_until(lambda: "completed" in sink.statuses(gift.command_id))

        assert "interrupted" in sink.statuses(normal.command_id)
        assert executor.interrupts >= 1
        assert executor.max_active == 1, "礼物抢占时也不应并行执行两个动作"
        assert [item.command_id for item in executor.performed] == [
            "cmd-normal",
            "cmd-gift",
        ]
    finally:
        await dispatcher.stop()


async def test_commands_execute_in_order() -> None:
    dispatcher, sink, executor = build()
    await dispatcher.start()
    try:
        commands = [make_command(command_id=f"cmd-{index}") for index in range(3)]
        for command in commands:
            await dispatcher.submit(command)
        await wait_until(
            lambda: all("completed" in sink.statuses(item.command_id) for item in commands)
        )
        assert [item.command_id for item in executor.performed] == [
            "cmd-0",
            "cmd-1",
            "cmd-2",
        ]
    finally:
        await dispatcher.stop()


async def test_interrupt_stops_current_and_clears_the_queue() -> None:
    executor = FakeExecutor(delay=0.3)
    dispatcher, sink, executor = build(executor)
    await dispatcher.start()
    try:
        first = make_command(command_id="cmd-first")
        second = make_command(command_id="cmd-second")
        await dispatcher.submit(first)
        await wait_until(lambda: "started" in sink.statuses(first.command_id))
        await dispatcher.submit(second)

        await dispatcher.handle_interrupt("主播停止")
        await wait_until(lambda: "interrupted" in sink.statuses(first.command_id))
        assert "interrupted" in sink.statuses(second.command_id)
        assert len(executor.performed) == 1
        assert executor.interrupts >= 1
        assert dispatcher.status()["queue"] == []
    finally:
        await dispatcher.stop()


async def test_queue_overflow_marks_the_oldest_as_not_performed() -> None:
    executor = FakeExecutor(delay=0.2)
    dispatcher, sink, executor = build(executor)
    dispatcher.max_queue = 1
    await dispatcher.start()
    try:
        first = make_command(command_id="cmd-1")
        second = make_command(command_id="cmd-2")
        third = make_command(command_id="cmd-3")
        await dispatcher.submit(first)
        await wait_until(lambda: "started" in sink.statuses(first.command_id))
        await dispatcher.submit(second)
        await dispatcher.submit(third)
        await wait_until(
            lambda: any(
                item.status == "expired" and item.error_code == "queue_overflow"
                for item in sink.results
            )
        )
        overflow = [item for item in sink.results if item.error_code == "queue_overflow"]
        assert overflow[0].command_id == "cmd-2"
    finally:
        await dispatcher.stop()


async def test_executor_error_is_reported_as_error() -> None:
    executor = FakeExecutor(status="error")
    dispatcher, sink, _ = build(executor)
    await dispatcher.start()
    try:
        command = make_command()
        await dispatcher.submit(command)
        await wait_until(lambda: "error" in sink.statuses(command.command_id))
        assert sink.statuses(command.command_id)[-1] == "error"
        assert dispatcher.status()["stats"]["errors"] == 1
    finally:
        await dispatcher.stop()


async def test_stop_marks_the_dispatcher_not_running() -> None:
    dispatcher, _, _ = build()
    await dispatcher.start()
    await dispatcher.stop()
    assert dispatcher.status()["running"] is False
    assert await dispatcher.submit(make_command()) == "closed"
