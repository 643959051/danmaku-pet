"""Self-hosted WSS downlink — development / backup only.

The formal production path is the 抖音云网关 (``gateway.py``).  This channel is
intentionally isolated behind ``GATEWAY_MODE=wss`` so switching back and forth
never touches the decision or execution code.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from danmaku_pet.shared.commands import PerformCommand
from danmaku_pet.shared.protocol import PROTOCOL_VERSION, Envelope

logger = logging.getLogger(__name__)


class ClientSocket:
    """A connected local client, with a write lock and room binding."""

    def __init__(self, websocket: Any, *, client_id: str = "", room_id: str = "") -> None:
        self.websocket = websocket
        self.client_id = client_id
        self.room_id = room_id
        self.connected_at = time.time()
        self.last_seen = time.time()
        self._lock = asyncio.Lock()

    def bind(self, client_id: str, room_id: str) -> None:
        self.client_id = client_id
        self.room_id = room_id
        self.last_seen = time.time()

    async def send(self, payload: dict[str, Any]) -> bool:
        async with self._lock:
            try:
                await self.websocket.send_json(payload)
                self.last_seen = time.time()
                return True
            except Exception as exc:  # noqa: BLE001 - socket state is the truth
                logger.info("向客户端 %s 发送失败: %s", self.client_id or "?", exc)
                return False

    async def send_envelope(
        self, kind: str, payload: dict[str, Any], *, version: int = PROTOCOL_VERSION
    ) -> bool:
        return await self.send(
            Envelope(kind=kind, payload=payload, protocol_version=version).to_dict()
        )

    async def close(self, code: int = 1000, reason: str = "") -> None:
        try:
            await self.websocket.close(code=code, reason=reason)
        except Exception:
            pass


class WebSocketGateway:
    mode = "wss"

    def __init__(self) -> None:
        self._sockets: dict[str, ClientSocket] = {}
        self._by_client: dict[str, ClientSocket] = {}
        self.sent = 0
        self.failed = 0
        self.interrupts = 0

    # ----------------------------------------------------------------- attach
    def attach(self, socket: ClientSocket) -> list[ClientSocket]:
        """Register a socket; evict any previous socket for the same room."""

        evicted: list[ClientSocket] = []
        if socket.client_id:
            old = self._by_client.get(socket.client_id)
            if old is not None and old is not socket:
                evicted.append(old)
        if socket.room_id:
            old_room = self._sockets.get(socket.room_id)
            if old_room is not None and old_room is not socket:
                evicted.append(old_room)
        for item in evicted:
            self._remove(item)
        if socket.client_id:
            self._by_client[socket.client_id] = socket
        if socket.room_id:
            self._sockets[socket.room_id] = socket
        return evicted

    def _remove(self, socket: ClientSocket) -> None:
        if socket.client_id and self._by_client.get(socket.client_id) is socket:
            self._by_client.pop(socket.client_id, None)
        if socket.room_id and self._sockets.get(socket.room_id) is socket:
            self._sockets.pop(socket.room_id, None)

    def detach(self, *, client_id: str = "", room_id: str = "") -> None:
        socket = self._by_client.get(client_id) if client_id else self._sockets.get(room_id)
        if socket is not None:
            self._remove(socket)

    def detach_socket(self, socket: ClientSocket) -> None:
        """Release exactly this socket (a reconnect may already own the room)."""

        self._remove(socket)

    def socket_for(self, room_id: str) -> ClientSocket | None:
        return self._sockets.get(room_id)

    def connected_rooms(self) -> list[str]:
        return list(self._sockets)

    # ------------------------------------------------------------------- send
    async def send(self, command: PerformCommand) -> bool:
        socket = self._sockets.get(command.room_id)
        if socket is None:
            logger.info("房间 %s 没有已连接的客户端，指令未下发", command.room_id)
            self.failed += 1
            return False
        ok = await socket.send_envelope(
            "command",
            command.to_dict(),
            version=command.protocol_version,
        )
        if ok:
            self.sent += 1
        else:
            self.failed += 1
            self._remove(socket)
        return ok

    async def interrupt(self, room_id: str, reason: str) -> bool:
        socket = self._sockets.get(room_id)
        if socket is None:
            return False
        self.interrupts += 1
        return await socket.send_envelope("interrupt", {"room_id": room_id, "reason": reason})

    # ---------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "connected_rooms": self.connected_rooms(),
            "clients": [
                {
                    "client_id": socket.client_id,
                    "room_id": socket.room_id,
                    "connected_at": socket.connected_at,
                    "last_seen": socket.last_seen,
                }
                for socket in self._sockets.values()
            ],
            "sent": self.sent,
            "failed": self.failed,
            "interrupts": self.interrupts,
        }

    async def close_all(self, reason: str = "server shutdown") -> None:
        for socket in list(self._sockets.values()):
            await socket.close(code=1001, reason=reason)
        self._sockets.clear()
        self._by_client.clear()
