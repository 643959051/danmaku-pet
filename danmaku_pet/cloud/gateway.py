"""Downlink gateway selection.

Three interchangeable implementations behind one small interface:

* ``mock``         — 开发用：不连接任何客户端，可选自动回执，便于只跑云端；
* ``wss``          — 备用/开发：自建 WebSocket（与正式链路隔离）；
* ``douyin_cloud`` — 正式：抖音云网关（路径全部来自配置）。

The formal path is never entangled with the backup path: switching
``GATEWAY_MODE`` only changes which object answers :meth:`GatewayManager.send`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Awaitable, Callable

from danmaku_pet.broadcaster import Broadcaster
from danmaku_pet.cloud.metrics import StageMetrics
from danmaku_pet.cloud.ws_gateway import WebSocketGateway
from danmaku_pet.cloud.ws_gateway import ClientSocket
from danmaku_pet.douyin.openapi import (
    DouyinAPIError,
    DouyinOpenAPI,
    PlatformNotConfigured,
)
from danmaku_pet.shared.commands import PerformCommand, PerformResult
from danmaku_pet.shared.plans import estimate_speech_ms

logger = logging.getLogger(__name__)

ResultHandler = Callable[[PerformResult], Awaitable[Any]]


class MockGateway:
    """No client needed: optionally ACKs every command so the pipeline is visible."""

    mode = "mock"

    def __init__(
        self,
        *,
        auto_ack: bool = True,
        ack_delay_s: float = 0.5,
        metrics: StageMetrics | None = None,
    ) -> None:
        self.auto_ack = bool(auto_ack)
        self.ack_delay_s = max(0.0, float(ack_delay_s))
        self.metrics = metrics
        self.sent = 0
        self.interrupts = 0
        self.outbox: deque[dict[str, Any]] = deque(maxlen=50)
        self._on_result: ResultHandler | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    def set_result_handler(self, handler: ResultHandler | None) -> None:
        self._on_result = handler

    async def send(self, command: PerformCommand) -> bool:
        self.sent += 1
        self.outbox.appendleft(
            {
                "at": time.time(),
                "command_id": command.command_id,
                "room_id": command.room_id,
                "expires_at": command.expires_at,
                "reply": command.plan.reply,
            }
        )
        if self.auto_ack and self._on_result is not None:
            self._schedule_ack(command)
        return True

    def _schedule_ack(self, command: PerformCommand) -> None:
        async def _ack() -> None:
            try:
                if self.ack_delay_s:
                    await asyncio.sleep(self.ack_delay_s)
                if command.is_expired():
                    status = "expired"
                else:
                    status = "completed"
                result = PerformResult(
                    command_id=command.command_id,
                    room_id=command.room_id,
                    status=status,
                    speech_ms=estimate_speech_ms(command.plan.reply),
                    client_id="mock-client",
                )
                if self.metrics is not None:
                    self.metrics.observe("performance", command.plan.min_hold_ms or 500)
                assert self._on_result is not None
                await self._on_result(result)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("mock 回执失败")

        task = asyncio.create_task(_ack(), name=f"mock-ack-{command.command_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def interrupt(self, room_id: str, reason: str) -> bool:
        self.interrupts += 1
        self.outbox.appendleft(
            {"at": time.time(), "interrupt": True, "room_id": room_id, "reason": reason}
        )
        return True

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "auto_ack": self.auto_ack,
            "ack_delay_seconds": self.ack_delay_s,
            "sent": self.sent,
            "interrupts": self.interrupts,
            "outbox": list(self.outbox)[:10],
        }

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()


class DouyinCloudGateway:
    """Formal downlink through the 抖音云网关 platform API."""

    mode = "douyin_cloud"

    def __init__(self, openapi: DouyinOpenAPI) -> None:
        self.openapi = openapi
        self.sent = 0
        self.failed = 0
        self.last_error = ""

    async def send(self, command: PerformCommand) -> bool:
        try:
            await self.openapi.send_perform_command(command)
        except PlatformNotConfigured as exc:
            self.failed += 1
            self.last_error = str(exc)
            logger.error("抖音云网关未配置，指令未下发: %s", exc)
            return False
        except DouyinAPIError as exc:
            self.failed += 1
            self.last_error = str(exc)
            logger.error("抖音云网关下发失败: %s", exc)
            return False
        self.sent += 1
        return True

    async def interrupt(self, room_id: str, reason: str) -> bool:
        try:
            await self.openapi.send_interrupt(room_id, reason)
            return True
        except (PlatformNotConfigured, DouyinAPIError) as exc:
            logger.warning("抖音云网关打断未下发: %s", exc)
            return False

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "sent": self.sent,
            "failed": self.failed,
            "last_error": self.last_error,
        }

    async def close(self) -> None:
        return None


class GatewayManager:
    def __init__(
        self,
        *,
        mode: str,
        broadcaster: Broadcaster,
        metrics: StageMetrics,
        openapi: DouyinOpenAPI | None = None,
        mock_auto_ack: bool = True,
        mock_ack_delay_s: float = 0.5,
    ) -> None:
        self.mode = mode
        self.broadcaster = broadcaster
        self.metrics = metrics
        self.ws = WebSocketGateway()
        self.mock = MockGateway(
            auto_ack=mock_auto_ack, ack_delay_s=mock_ack_delay_s, metrics=metrics
        )
        self.douyin = DouyinCloudGateway(openapi) if openapi is not None else None
        if mode == "douyin_cloud" and self.douyin is None:
            raise ValueError("gateway mode douyin_cloud requires a DouyinOpenAPI client")
        self.last_error = ""

    # ------------------------------------------------------------------ wiring
    def set_result_handler(self, handler: ResultHandler | None) -> None:
        self.mock.set_result_handler(handler)

    @property
    def active(self) -> Any:
        if self.mode == "wss":
            return self.ws
        if self.mode == "douyin_cloud" and self.douyin is not None:
            return self.douyin
        return self.mock

    async def start(self) -> None:
        logger.info("下行网关模式：%s", self.mode)

    # -------------------------------------------------------------------- send
    async def send(self, command: PerformCommand) -> bool:
        await self.broadcaster.send(
            "command",
            {
                "room_id": command.room_id,
                "gateway_mode": self.mode,
                "command": command.to_dict(),
            },
        )
        started = time.perf_counter()
        try:
            ok = bool(await self.active.send(command))
        except Exception as exc:  # noqa: BLE001 - a gateway bug must not kill the room
            self.last_error = str(exc)
            logger.exception("网关发送异常")
            ok = False
        self.metrics.observe_now("gateway_send", started)
        self.metrics.inc("commands_sent" if ok else "commands_failed")
        return ok

    async def interrupt(self, room_id: str, reason: str) -> bool:
        try:
            return bool(await self.active.interrupt(room_id, reason))
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            logger.exception("网关打断异常")
            return False

    # --------------------------------------------------------------------- ws
    def attach_client(self, socket: ClientSocket) -> list[ClientSocket]:
        return self.ws.attach(socket)

    def detach_client(self, *, client_id: str = "", room_id: str = "") -> None:
        self.ws.detach(client_id=client_id, room_id=room_id)

    def detach_socket(self, socket: ClientSocket) -> None:
        """Release exactly one socket (a reconnect may already own the room)."""

        self.ws.detach_socket(socket)

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mode": self.mode,
            "active": getattr(self.active, "mode", "?"),
            "last_error": self.last_error,
            "ws": self.ws.status(),
            "mock": self.mock.status(),
        }
        if self.douyin is not None:
            payload["douyin_cloud"] = self.douyin.status()
        return payload

    async def close(self) -> None:
        await self.mock.close()
        await self.ws.close_all()
        if self.douyin is not None:
            await self.douyin.close()
