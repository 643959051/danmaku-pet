"""Client downlink connection: registration, heartbeat, reconnect, result uplink.

Transports:

* ``websocket`` — 开播电脑连云端 ``/ws/client``（开发与自建 WSS 备用方案）；
* ``static``    — 从本地 JSONL 回放模拟云端指令（不连网也能验收本地执行）；
* ``platform``  — 抖音云网关客户端通道，等平台给出规范后实现（当前明确报错）。

The client never exposes an inbound port and never stores a DeepSeek key.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any, Protocol

from danmaku_pet.client.dispatcher import CommandDispatcher
from danmaku_pet.config import Config
from danmaku_pet.shared.commands import PerformCommand, PerformResult
from danmaku_pet.shared.protocol import (
    PROTOCOL_VERSION,
    Envelope,
    ProtocolError,
    VersionMismatch,
    check_protocol_version,
    decode_message,
)

logger = logging.getLogger(__name__)


class ChannelError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class Transport(Protocol):
    mode: str

    async def connect(self) -> None: ...
    async def send(self, payload: dict[str, Any]) -> None: ...
    async def receive(self) -> dict[str, Any]: ...
    async def close(self) -> None: ...
    def status(self) -> dict[str, Any]: ...


# --------------------------------------------------------------------- websocket
class WebSocketTransport:
    mode = "websocket"

    def __init__(
        self,
        url: str,
        *,
        token: str,
        client_id: str,
        room_id: str,
        app_version: str,
        open_timeout: float = 12.0,
    ) -> None:
        self.url = url
        self.token = token
        self.client_id = client_id
        self.room_id = room_id
        self.app_version = app_version
        self.open_timeout = open_timeout
        self._ws: Any = None
        self._send_lock = asyncio.Lock()
        self.sent = 0

    async def connect(self) -> None:
        import websockets

        try:
            self._ws = await websockets.connect(
                self.url,
                open_timeout=self.open_timeout,
                close_timeout=5,
                max_size=2 * 1024 * 1024,
                ping_interval=20,
                ping_timeout=20,
            )
        except Exception as exc:  # noqa: BLE001 - reported and retried by caller
            raise ChannelError(f"连接云端失败：{exc}", retryable=True) from exc
        await self.send(
            Envelope(
                kind="hello",
                payload={
                    "client_id": self.client_id,
                    "room_id": self.room_id,
                    "token": self.token,
                    "app_version": self.app_version,
                    "platform_params": "redacted",
                },
            ).to_dict()
        )

    async def send(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            raise ChannelError("连接尚未建立", retryable=True)
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        async with self._send_lock:
            try:
                await self._ws.send(data)
                self.sent += 1
            except Exception as exc:  # noqa: BLE001
                raise ChannelError(f"发送失败：{exc}", retryable=True) from exc

    async def receive(self) -> dict[str, Any]:
        if self._ws is None:
            raise ChannelError("连接尚未建立", retryable=True)
        try:
            raw = await self._ws.recv()
        except Exception as exc:  # noqa: BLE001
            raise ChannelError(f"连接已断开：{exc}", retryable=True) from exc
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "replace")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ChannelError(f"云端发来非 JSON 帧：{exc}", retryable=True) from exc
        if not isinstance(value, dict):
            raise ChannelError("云端发来的帧不是对象", retryable=True)
        return value

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "url": self.url,
            "connected": self._ws is not None,
            "sent": self.sent,
        }


# ------------------------------------------------------------------------ static
class StaticCommandTransport:
    """Replay commands from a JSONL file (file may contain full envelopes or raw commands)."""

    mode = "static"

    def __init__(
        self,
        path: Path,
        *,
        interval_s: float = 1.0,
        record_results: bool = True,
    ) -> None:
        self.path = path
        self.interval_s = max(0.0, float(interval_s))
        self.record_results = record_results
        self._commands: list[dict[str, Any]] = []
        self._index = 0
        self._idle = asyncio.Event()
        self.sent_results: list[dict[str, Any]] = []
        self._results_path = path.with_name(path.stem + ".results.jsonl")

    async def connect(self) -> None:
        if not self.path.is_file():
            raise ChannelError(f"模拟指令文件不存在：{self.path}", retryable=False)
        self._commands = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ChannelError(f"模拟指令第 {len(self._commands) + 1} 行不是 JSON：{exc}", retryable=False) from exc
            if isinstance(value, dict) and value.get("kind") == "command":
                value = value.get("payload") or {}
            if isinstance(value, dict):
                self._commands.append(value)
        if not self._commands:
            raise ChannelError(f"模拟指令文件为空：{self.path}", retryable=False)
        logger.info("已载入 %d 条模拟云端指令：%s", len(self._commands), self.path)

    async def send(self, payload: dict[str, Any]) -> None:
        if payload.get("kind") != "result":
            return
        self.sent_results.append(payload.get("payload") or {})
        if self.record_results:
            with contextlib.suppress(OSError):
                with self._results_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(payload.get("payload") or {}, ensure_ascii=False) + "\n"
                    )

    async def receive(self) -> dict[str, Any]:
        if self._index >= len(self._commands):
            await self._idle.wait()
            raise ChannelError("模拟指令已全部播放", retryable=True)
        command = self._commands[self._index]
        self._index += 1
        if self.interval_s:
            await asyncio.sleep(self.interval_s)
        return Envelope(kind="command", payload=command).to_dict()

    async def close(self) -> None:
        return None

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "file": str(self.path),
            "total": len(self._commands),
            "played": self._index,
            "results": len(self.sent_results),
        }


# ---------------------------------------------------------------------- platform
class PlatformTransport:
    """Placeholder for the official 抖音云 gateway client channel.

    The real implementation depends on the client SDK / launch parameters the
    platform provides after 类目 and 权限确认.  Failing loudly is intentional: a
    fake "connected" state would hide a broken production link.
    """

    mode = "platform"

    def __init__(self, config: Config) -> None:
        self.config = config

    async def connect(self) -> None:
        raise ChannelError(
            "抖音云网关客户端通道尚未接入：请等待平台提供客户端 SDK/启动参数，"
            "当前可先用 --transport websocket（开发/备用）或 --transport static（本地联调）",
            retryable=False,
        )

    async def send(self, payload: dict[str, Any]) -> None:
        raise ChannelError("平台通道未连接", retryable=False)

    async def receive(self) -> dict[str, Any]:
        raise ChannelError("平台通道未连接", retryable=False)

    async def close(self) -> None:
        return None

    def status(self) -> dict[str, Any]:
        return {"mode": self.mode, "connected": False}


def create_transport(config: Config) -> Transport:
    if config.transport == "static":
        if config.static_commands is None:
            raise ChannelError("transport=static 需要 --static-commands <文件>", retryable=False)
        return StaticCommandTransport(config.static_commands)
    if config.transport == "platform":
        return PlatformTransport(config)
    return WebSocketTransport(
        config.cloud_url,
        token=config.client_token,
        client_id=config.client_id,
        room_id=config.room_id,
        app_version=_app_version(),
    )


def _app_version() -> str:
    try:
        from danmaku_pet import __version__

        return __version__
    except Exception:  # pragma: no cover
        return "0"


# -------------------------------------------------------------------- connection
class ClientConnection:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.state = "idle"
        self.last_error = ""
        self.connected_at: float | None = None
        self.last_message_at: float | None = None
        self.reconnects = 0
        self.server_info: dict[str, Any] = {}
        self.transport_status: dict[str, Any] = {}
        self._dispatcher: CommandDispatcher | None = None
        self._transport: Transport | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._outbox: deque[dict[str, Any]] = deque(maxlen=64)
        self._attempts = 0

    def bind_dispatcher(self, dispatcher: CommandDispatcher) -> None:
        self._dispatcher = dispatcher

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="client-connection")

    async def stop(self) -> None:
        self._stop.set()
        transport, self._transport = self._transport, None
        if transport is not None:
            await transport.close()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("关闭连接时出现异常")
        self.state = "offline"

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    # ------------------------------------------------------------------ uplink
    async def send_result(self, result: PerformResult) -> bool:
        payload = Envelope(kind="result", payload=result.to_dict()).to_dict()
        transport = self._transport
        if transport is None or self.state != "online":
            self._outbox.append(payload)
            return False
        try:
            await transport.send(payload)
            return True
        except ChannelError as exc:
            self.last_error = str(exc)
            self._outbox.append(payload)
            return False

    async def _flush_outbox(self) -> None:
        transport = self._transport
        if transport is None:
            return
        while self._outbox:
            payload = self._outbox[0]
            try:
                await transport.send(payload)
            except ChannelError as exc:
                self.last_error = str(exc)
                return
            self._outbox.popleft()

    # -------------------------------------------------------------------- loop
    async def _run(self) -> None:
        backoff = self.config.reconnect_min_s
        while not self._stop.is_set():
            self.reconnects = max(0, self._attempts)
            self._attempts += 1
            transport: Transport | None = None
            try:
                self.state = "connecting"
                transport = create_transport(self.config)
                self.transport_status = transport.status()
                await transport.connect()
                self._transport = transport
                self.state = "online"
                self.connected_at = time.time()
                self.last_error = ""
                logger.info("已连接云端：%s", transport.status().get("url", transport.mode))
                backoff = self.config.reconnect_min_s
                await self._flush_outbox()
                await self._session(transport)
            except ChannelError as exc:
                self.last_error = str(exc)
                if not exc.retryable:
                    self.state = "offline"
                    logger.error("客户端通道不可用：%s", exc)
                    return
                self.state = "reconnecting"
                logger.warning("云端连接断开：%s", exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep retrying
                self.last_error = str(exc)
                self.state = "reconnecting"
                logger.exception("云端连接异常")
            finally:
                if transport is not None:
                    # Also close a transport whose ``connect`` failed halfway:
                    # it may already hold an open socket/file handle.
                    self.transport_status = transport.status()
                    await transport.close()
                if self._transport is transport:
                    self._transport = None
            if self._stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(self.config.reconnect_max_s, max(backoff, 0.5) * 2)

    async def _session(self, transport: Transport) -> None:
        if transport.mode == "static":
            # A replayed file has no server to answer heartbeats; enforcing the
            # liveness timeout would reconnect and replay the file forever.
            while not self._stop.is_set():
                message = await transport.receive()
                self.last_message_at = time.time()
                await self._handle(message)
            return
        recv_task = asyncio.create_task(transport.receive(), name="recv-once")
        last_rx = time.monotonic()
        try:
            while not self._stop.is_set():
                done, _ = await asyncio.wait(
                    {recv_task}, timeout=self.config.heartbeat_interval_s
                )
                if not done:
                    if (
                        time.monotonic() - last_rx
                        > self.config.heartbeat_interval_s * 3
                    ):
                        raise ChannelError("心跳超时：云端长时间无响应", retryable=True)
                    await transport.send(
                        Envelope(
                            kind="heartbeat", payload={"client_time": time.time()}
                        ).to_dict()
                    )
                    continue
                message = recv_task.result()
                last_rx = time.monotonic()
                self.last_message_at = time.time()
                recv_task = asyncio.create_task(transport.receive(), name="recv-once")
                await self._handle(message)
        finally:
            recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await recv_task

    # ------------------------------------------------------------------ inbound
    async def _handle(self, raw: dict[str, Any]) -> None:
        try:
            envelope = decode_message(raw)
        except VersionMismatch as exc:
            self.last_error = str(exc)
            logger.error("协议版本不兼容：%s", exc)
            raise ChannelError(str(exc), retryable=False) from exc
        except ProtocolError as exc:
            logger.warning("云端消息格式错误，已忽略：%s", exc)
            return

        if envelope.kind == "welcome":
            self.server_info = envelope.payload
            logger.info(
                "云端握手完成：room=%s gateway=%s",
                envelope.payload.get("room_id"),
                envelope.payload.get("gateway_mode"),
            )
            return
        if envelope.kind == "pong":
            return
        if envelope.kind == "notice":
            logger.info("云端通知：%s", envelope.payload.get("message"))
            return
        if envelope.kind == "error":
            self.last_error = str(envelope.payload.get("message") or "云端返回错误")
            logger.warning("云端错误：%s", self.last_error)
            return
        if envelope.kind == "interrupt":
            if self._dispatcher is not None:
                await self._dispatcher.handle_interrupt(
                    str(envelope.payload.get("reason") or "云端打断当前表演")
                )
            return
        if envelope.kind == "command":
            await self._handle_command(envelope.payload)
            return
        logger.debug("忽略未知云端消息：%s", envelope.kind)

    async def _handle_command(self, payload: dict[str, Any]) -> None:
        try:
            command = PerformCommand.from_dict(payload)
        except (ValueError, TypeError) as exc:
            logger.warning("云端指令无法解析，已忽略：%s", exc)
            return
        if command.room_id != self.config.room_id:
            logger.warning(
                "指令房间不匹配（指令 %s / 本地 %s），已忽略",
                command.room_id,
                self.config.room_id,
            )
            return
        if self._dispatcher is None:
            logger.error("尚未绑定指令分发器，指令被丢弃")
            return
        outcome = await self._dispatcher.submit(command)
        logger.info("收到指令 %s -> %s", command.command_id, outcome)

    # ------------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        transport = self._transport
        return {
            "state": self.state,
            "last_error": self.last_error,
            "connected_at": self.connected_at,
            "last_message_at": self.last_message_at,
            "reconnects": self.reconnects,
            "outbox": len(self._outbox),
            "server_info": dict(self.server_info),
            "transport": transport.status() if transport is not None else self.transport_status,
            "cloud_url": self.config.cloud_url,
            "room_id": self.config.room_id,
            "client_id": self.config.client_id,
            "protocol_version": PROTOCOL_VERSION,
        }
