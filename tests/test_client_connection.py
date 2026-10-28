from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from danmaku_pet.client import connection as connection_module
from danmaku_pet.client.connection import (
    ChannelError,
    ClientConnection,
    PlatformTransport,
    StaticCommandTransport,
)
from danmaku_pet.config import Config
from danmaku_pet.shared.commands import PerformResult
from danmaku_pet.shared.protocol import Envelope
from tests.support import make_command


class FakeDispatcher:
    def __init__(self) -> None:
        self.commands: list = []
        self.interrupts: list[str] = []

    async def submit(self, command) -> str:
        self.commands.append(command)
        return "queued"

    async def handle_interrupt(self, reason: str = "") -> None:
        self.interrupts.append(reason)


class FakeTransport:
    mode = "fake"

    def __init__(self, messages: list[dict] | None = None, fail_times: int = 0) -> None:
        self.messages = list(messages or [])
        self.fail_times = fail_times
        self.sent: list[dict] = []
        self.closed = False
        self._idle = asyncio.Event()

    async def connect(self) -> None:
        if self.fail_times:
            self.fail_times -= 1
            raise ChannelError("connect refused", retryable=True)

    async def send(self, payload: dict) -> None:
        self.sent.append(payload)

    async def receive(self) -> dict:
        if self.messages:
            return self.messages.pop(0)
        await self._idle.wait()
        raise AssertionError("unreachable")

    async def close(self) -> None:
        self.closed = True

    def status(self) -> dict:
        return {"mode": self.mode, "sent": len(self.sent)}


def client_config(workspace: Path) -> Config:
    config = Config.load(use_env_file=False)
    config.room_id = "room-a"
    config.client_id = "client-1"
    config.client_token = "token-1"
    config.cloud_url = "ws://127.0.0.1:9/ws/client"
    config.transport = "websocket"
    config.reconnect_min_s = 0.01
    config.reconnect_max_s = 0.02
    config.heartbeat_interval_s = 0.05
    config.temp_dir = workspace
    return config


async def wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met before timeout")


def patch_factory(monkeypatch, transports: list[FakeTransport]) -> None:
    queue = list(transports)

    def factory(config):
        return queue.pop(0) if queue else FakeTransport()

    monkeypatch.setattr(connection_module, "create_transport", factory)


def command_message(**overrides) -> dict:
    command = make_command(**overrides)
    return Envelope(kind="command", payload=command.to_dict()).to_dict()


def welcome_message(version: int = 1) -> dict:
    return Envelope(
        kind="welcome",
        payload={"room_id": "room-a", "gateway_mode": "wss"},
        protocol_version=version,
    ).to_dict()


async def test_connect_and_dispatch_a_command(monkeypatch, workspace: Path) -> None:
    transport = FakeTransport([welcome_message(), command_message()])
    patch_factory(monkeypatch, [transport])
    config = client_config(workspace)
    connection = ClientConnection(config)
    dispatcher = FakeDispatcher()
    connection.bind_dispatcher(dispatcher)  # type: ignore[arg-type]
    await connection.start()
    try:
        await wait_until(lambda: len(dispatcher.commands) == 1)
        assert dispatcher.commands[0].room_id == "room-a"
        assert connection.state == "online"
        assert connection.server_info["gateway_mode"] == "wss"
    finally:
        await connection.stop()


async def test_command_for_another_room_is_ignored(monkeypatch, workspace: Path) -> None:
    transport = FakeTransport([welcome_message(), command_message(room_id="room-b")])
    patch_factory(monkeypatch, [transport])
    config = client_config(workspace)
    connection = ClientConnection(config)
    dispatcher = FakeDispatcher()
    connection.bind_dispatcher(dispatcher)  # type: ignore[arg-type]
    await connection.start()
    try:
        await wait_until(lambda: connection.state == "online")
        await asyncio.sleep(0.1)
        assert dispatcher.commands == []
    finally:
        await connection.stop()


async def test_interrupt_message_reaches_the_dispatcher(monkeypatch, workspace: Path) -> None:
    transport = FakeTransport(
        [
            welcome_message(),
            Envelope(kind="interrupt", payload={"reason": "主播停止"}).to_dict(),
        ]
    )
    patch_factory(monkeypatch, [transport])
    connection = ClientConnection(client_config(workspace))
    dispatcher = FakeDispatcher()
    connection.bind_dispatcher(dispatcher)  # type: ignore[arg-type]
    await connection.start()
    try:
        await wait_until(lambda: bool(dispatcher.interrupts))
        assert dispatcher.interrupts == ["主播停止"]
    finally:
        await connection.stop()


async def test_version_mismatch_stops_the_connection(monkeypatch, workspace: Path) -> None:
    transport = FakeTransport([welcome_message(version=9)])
    patch_factory(monkeypatch, [transport])
    connection = ClientConnection(client_config(workspace))
    connection.bind_dispatcher(FakeDispatcher())  # type: ignore[arg-type]
    await connection.start()
    await wait_until(lambda: connection.state == "offline")
    assert "协议版本" in connection.last_error
    await connection.stop()


async def test_send_result_before_connect_goes_to_the_outbox(workspace: Path) -> None:
    connection = ClientConnection(client_config(workspace))
    result = PerformResult(command_id="c", room_id="room-a", status="completed")
    assert await connection.send_result(result) is False
    assert connection.status()["outbox"] == 1


async def test_outbox_is_flushed_after_reconnect(monkeypatch, workspace: Path) -> None:
    transport = FakeTransport([welcome_message()])
    patch_factory(monkeypatch, [transport])
    connection = ClientConnection(client_config(workspace))
    connection.bind_dispatcher(FakeDispatcher())  # type: ignore[arg-type]
    result = PerformResult(command_id="c", room_id="room-a", status="completed")
    assert await connection.send_result(result) is False
    await connection.start()
    try:
        await wait_until(lambda: any(item.get("kind") == "result" for item in transport.sent))
        assert connection.status()["outbox"] == 0
    finally:
        await connection.stop()


async def test_reconnect_after_a_failed_connect(monkeypatch, workspace: Path) -> None:
    first = FakeTransport([], fail_times=1)
    second = FakeTransport([welcome_message()])
    patch_factory(monkeypatch, [first, second])
    connection = ClientConnection(client_config(workspace))
    connection.bind_dispatcher(FakeDispatcher())  # type: ignore[arg-type]
    await connection.start()
    try:
        await wait_until(lambda: connection.state == "online")
        assert connection.reconnects >= 1
        assert first.closed is True
    finally:
        await connection.stop()


async def test_static_transport_does_not_trigger_heartbeat_reconnect(
    monkeypatch, workspace: Path
) -> None:
    path = workspace / "static-commands.jsonl"
    command = make_command()
    path.write_text(json.dumps(command.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")
    transport = StaticCommandTransport(path, interval_s=0)
    patch_factory(monkeypatch, [transport])
    config = client_config(workspace)
    config.transport = "static"
    config.static_commands = path
    config.heartbeat_interval_s = 0.01
    connection = ClientConnection(config)
    dispatcher = FakeDispatcher()
    connection.bind_dispatcher(dispatcher)  # type: ignore[arg-type]
    await connection.start()
    try:
        await wait_until(lambda: len(dispatcher.commands) == 1)
        await asyncio.sleep(0.2)
        assert connection.state == "online"
        assert connection.reconnects == 0
        assert len(dispatcher.commands) == 1, "静态文件不应被重复回放"
    finally:
        await connection.stop()


async def test_platform_transport_fails_fast(workspace: Path) -> None:
    config = client_config(workspace)
    transport = PlatformTransport(config)
    with pytest.raises(ChannelError) as exc:
        await transport.connect()
    assert exc.value.retryable is False
    assert "抖音云网关" in str(exc.value)


async def test_static_transport_replays_commands_and_records_results(workspace: Path) -> None:
    path = workspace / "commands.jsonl"
    command = make_command(room_id="room-a")
    path.write_text(json.dumps(command.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")
    transport = StaticCommandTransport(path, interval_s=0)
    await transport.connect()
    message = await transport.receive()
    assert message["kind"] == "command"
    assert message["payload"]["command_id"] == command.command_id
    await transport.send(
        Envelope(
            kind="result",
            payload=PerformResult(
                command_id=command.command_id, room_id="room-a", status="completed"
            ).to_dict(),
        ).to_dict()
    )
    results = path.with_name(path.stem + ".results.jsonl")
    assert results.is_file()
    assert json.loads(results.read_text(encoding="utf-8").strip())["status"] == "completed"
    await transport.close()


async def test_static_transport_rejects_missing_file(workspace: Path) -> None:
    transport = StaticCommandTransport(workspace / "missing.jsonl")
    with pytest.raises(ChannelError) as exc:
        await transport.connect()
    assert exc.value.retryable is False
