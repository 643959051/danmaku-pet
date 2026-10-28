from __future__ import annotations

import asyncio
import time

import pytest

from danmaku_pet.broadcaster import Broadcaster
from danmaku_pet.cloud.gateway import GatewayManager, MockGateway
from danmaku_pet.cloud.metrics import StageMetrics
from danmaku_pet.cloud.ws_gateway import ClientSocket, WebSocketGateway
from danmaku_pet.shared.commands import PerformResult
from tests.support import make_command


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed: list[tuple[int, str]] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


async def test_mock_gateway_auto_ack_reports_completed() -> None:
    results: list[PerformResult] = []

    async def on_result(result: PerformResult) -> None:
        results.append(result)

    gateway = MockGateway(auto_ack=True, ack_delay_s=0.01)
    gateway.set_result_handler(on_result)
    command = make_command(expires_in=10)
    assert await gateway.send(command) is True
    for _ in range(100):
        if results:
            break
        await asyncio.sleep(0.01)
    assert results and results[0].status == "completed"
    assert results[0].command_id == command.command_id
    assert gateway.status()["sent"] == 1
    await gateway.close()


async def test_mock_gateway_reports_expired_for_stale_commands() -> None:
    results: list[PerformResult] = []

    async def on_result(result: PerformResult) -> None:
        results.append(result)

    gateway = MockGateway(auto_ack=True, ack_delay_s=0.15)
    gateway.set_result_handler(on_result)
    command = make_command(created_at=time.time() - 30, expires_in=10)
    await gateway.send(command)
    for _ in range(100):
        if results:
            break
        await asyncio.sleep(0.01)
    assert results[0].status == "expired"
    await gateway.close()


async def test_ws_gateway_routes_by_room_and_evicts_duplicates() -> None:
    gateway = WebSocketGateway()
    socket_a = ClientSocket(FakeSocket(), client_id="c-a", room_id="room-a")
    socket_b = ClientSocket(FakeSocket(), client_id="c-b", room_id="room-b")
    gateway.attach(socket_a)
    gateway.attach(socket_b)

    command_a = make_command(room_id="room-a")
    assert await gateway.send(command_a) is True
    payload = socket_a.websocket.sent[-1]
    assert payload["kind"] == "command"
    assert payload["payload"]["room_id"] == "room-a"

    # Same client id reconnects: the old socket is evicted and closed.
    replacement_ws = FakeSocket()
    replacement = ClientSocket(replacement_ws, client_id="c-a", room_id="room-a")
    evicted = gateway.attach(replacement)
    assert socket_a in evicted
    assert gateway.socket_for("room-a") is replacement

    assert await gateway.send(command_a) is True
    assert replacement_ws.sent[-1]["kind"] == "command"
    assert await gateway.interrupt("room-b", "stop") is True
    assert socket_b.websocket.sent[-1]["kind"] == "interrupt"
    assert await gateway.send(make_command(room_id="room-missing")) is False


async def test_ws_gateway_detach_socket_does_not_remove_the_new_connection() -> None:
    gateway = WebSocketGateway()
    old = ClientSocket(FakeSocket(), client_id="c", room_id="room-a")
    new = ClientSocket(FakeSocket(), client_id="c", room_id="room-a")
    gateway.attach(old)
    gateway.attach(new)
    gateway.detach_socket(old)
    assert gateway.socket_for("room-a") is new


async def test_gateway_manager_selects_mock_and_broadcasts() -> None:
    broadcaster = Broadcaster()
    metrics = StageMetrics()
    manager = GatewayManager(
        mode="mock",
        broadcaster=broadcaster,
        metrics=metrics,
        mock_auto_ack=False,
    )
    await manager.start()
    assert manager.active.mode == "mock"
    assert await manager.send(make_command()) is True
    assert await manager.interrupt("room-a", "stop") is True
    status = manager.status()
    assert status["mode"] == "mock"
    assert status["mock"]["sent"] == 1
    assert metrics.snapshot()["counters"]["commands_sent"] == 1
    await manager.close()


def test_douyin_cloud_mode_requires_an_openapi_client() -> None:
    with pytest.raises(ValueError):
        GatewayManager(
            mode="douyin_cloud",
            broadcaster=Broadcaster(),
            metrics=StageMetrics(),
            openapi=None,
        )


async def test_gateway_manager_ws_mode_uses_the_ws_gateway() -> None:
    manager = GatewayManager(
        mode="wss",
        broadcaster=Broadcaster(),
        metrics=StageMetrics(),
        mock_auto_ack=False,
    )
    await manager.start()
    assert manager.active.mode == "wss"
    # No client connected: the send fails but does not raise.
    assert await manager.send(make_command()) is False
    await manager.close()
