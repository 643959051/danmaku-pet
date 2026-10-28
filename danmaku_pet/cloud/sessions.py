"""Client/room binding.

One local client per room in the first version.  A reconnect replaces the old
session for the same client id (and for the same room), so a restarted client
never steals a command from its previous incarnation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ClientSession:
    client_id: str
    room_id: str
    protocol_version: int = 1
    remote: str = ""
    user_agent: str = ""
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    commands_sent: int = 0
    results_received: int = 0
    last_result_status: str = ""

    def touch(self, now: float | None = None) -> None:
        self.last_seen = time.time() if now is None else float(now)

    def is_stale(self, now: float | None = None, *, timeout_s: float = 30.0) -> bool:
        current = time.time() if now is None else float(now)
        return current - self.last_seen > max(1.0, float(timeout_s))

    def snapshot(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "room_id": self.room_id,
            "protocol_version": self.protocol_version,
            "remote": self.remote,
            "user_agent": self.user_agent,
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
            "idle_seconds": round(max(0.0, time.time() - self.last_seen), 1),
            "commands_sent": self.commands_sent,
            "results_received": self.results_received,
            "last_result_status": self.last_result_status,
        }


class SessionRegistry:
    def __init__(self, *, idle_timeout_s: float = 30.0) -> None:
        self.idle_timeout_s = max(5.0, float(idle_timeout_s))
        self._sessions: dict[str, ClientSession] = {}

    def __len__(self) -> int:
        return len(self._sessions)

    def get(self, client_id: str) -> ClientSession | None:
        return self._sessions.get(str(client_id or "").strip())

    def by_room(self, room_id: str) -> list[ClientSession]:
        cleaned = str(room_id or "").strip()
        return [s for s in self._sessions.values() if s.room_id == cleaned]

    def bind(self, session: ClientSession) -> list[ClientSession]:
        """Register a session; return evicted sessions (same client or room)."""

        evicted: list[ClientSession] = []
        previous = self._sessions.get(session.client_id)
        if previous is not None and previous is not session:
            evicted.append(previous)
        for existing in list(self._sessions.values()):
            if existing.room_id == session.room_id and existing.client_id != session.client_id:
                evicted.append(existing)
        for item in evicted:
            self._sessions.pop(item.client_id, None)
        self._sessions[session.client_id] = session
        return evicted

    def unbind(self, client_id: str) -> ClientSession | None:
        return self._sessions.pop(str(client_id or "").strip(), None)

    def release(self, session: ClientSession) -> bool:
        """Remove ``session`` only if it is still the bound one for its client.

        A reconnecting client can be bound before the old socket's teardown
        runs; releasing by identity prevents the old socket from evicting it.
        """

        current = self._sessions.get(session.client_id)
        if current is session:
            self._sessions.pop(session.client_id, None)
            return True
        return False

    def touch(
        self,
        client_id: str,
        *,
        result_status: str = "",
        now: float | None = None,
    ) -> ClientSession | None:
        session = self.get(client_id)
        if session is None:
            return None
        session.touch(now)
        if result_status:
            session.results_received += 1
            session.last_result_status = result_status
        return session

    def mark_command_sent(self, room_id: str) -> None:
        for session in self.by_room(room_id):
            session.commands_sent += 1

    def stale(self, *, now: float | None = None) -> list[ClientSession]:
        return [s for s in self._sessions.values() if s.is_stale(now, timeout_s=self.idle_timeout_s)]

    def snapshot(self) -> list[dict[str, Any]]:
        return [session.snapshot() for session in self._sessions.values()]

    def clear(self) -> None:
        self._sessions.clear()
