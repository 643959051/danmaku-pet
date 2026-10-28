"""External room state for the stateless cloud service.

Two implementations behind one small interface:

* ``MemoryStateStore`` — local development and unit tests only;
* ``RedisStateStore``  — production: room context/pending/dedup/persona live in
  Redis, so an instance can be replaced at any time.

The room state is a JSON document per room with a TTL.  Live WebSocket sockets
are deliberately **not** stored here: the formal path uses the 抖音云 gateway
for room → client routing; the self-hosted WSS channel remains a single-instance
development/backup option.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from danmaku_pet.decision.context import ConversationMemory
from danmaku_pet.shared.protocol import DedupCache

ROOM_PREFIX = "dmp:room:"
LOCK_PREFIX = "dmp:lock:"
DEDUP_PREFIX = "dmp:dedup:"
PERSONA_KEY = "dmp:persona"

MAX_PENDING = 30
MAX_RECENT = 20
STAT_KEYS = (
    "received",
    "dispatched",
    "completed",
    "interrupted",
    "errors",
    "dropped",
    "duplicates",
    "timeouts",
    "decision_ms_total",
    "speech_ms_total",
)
ACTIVE_STATUSES = {"deciding", "dispatched", "started"}


def _default_stats() -> dict[str, int]:
    return {key: 0 for key in STAT_KEYS}


@dataclass(slots=True)
class RoomState:
    room_id: str
    memory: list[dict[str, str]] = field(default_factory=list)
    pending: list[dict[str, Any]] = field(default_factory=list)
    last_danmaku: dict[str, Any] | None = None
    auto_reply: bool = False
    paused: bool = False
    stats: dict[str, int] = field(default_factory=_default_stats)
    recent: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_event_at: float = field(default_factory=time.time)

    @classmethod
    def new(cls, room_id: str, *, auto_reply: bool = False) -> "RoomState":
        now = time.time()
        return cls(
            room_id=room_id,
            auto_reply=bool(auto_reply),
            created_at=now,
            updated_at=now,
            last_event_at=now,
        )

    # ------------------------------------------------------------------ json
    def to_dict(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "memory": list(self.memory),
            "pending": list(self.pending),
            "last_danmaku": self.last_danmaku,
            "auto_reply": self.auto_reply,
            "paused": self.paused,
            "stats": dict(self.stats),
            "recent": list(self.recent),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_event_at": self.last_event_at,
        }

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, default_auto_reply: bool = False
    ) -> "RoomState":
        if not isinstance(data, dict) or not data.get("room_id"):
            raise ValueError("invalid room state")
        stats = _default_stats()
        for key, value in (data.get("stats") or {}).items():
            if key in stats:
                try:
                    stats[key] = int(value)
                except (TypeError, ValueError):
                    pass
        now = time.time()

        def number(key: str) -> float:
            try:
                value = float(data.get(key) or 0)
            except (TypeError, ValueError):
                return now
            return value if value > 0 else now

        return cls(
            room_id=str(data["room_id"]),
            memory=[
                {"user": str(item.get("user", "")), "reply": str(item.get("reply", ""))}
                for item in (data.get("memory") or [])
                if isinstance(item, dict)
            ][-64:],
            pending=[item for item in (data.get("pending") or []) if isinstance(item, dict)][
                -MAX_PENDING:
            ],
            last_danmaku=data.get("last_danmaku") if isinstance(data.get("last_danmaku"), dict) else None,
            auto_reply=bool(data.get("auto_reply", default_auto_reply)),
            paused=bool(data.get("paused", False)),
            stats=stats,
            recent=[item for item in (data.get("recent") or []) if isinstance(item, dict)][
                :MAX_RECENT
            ],
            created_at=number("created_at"),
            updated_at=number("updated_at"),
            last_event_at=number("last_event_at"),
        )

    # --------------------------------------------------------------- helpers
    def conversation(self, max_rounds: int) -> ConversationMemory:
        memory = ConversationMemory(max_rounds)
        for item in self.memory[-max_rounds:]:
            memory.add(item.get("user", ""), item.get("reply", ""))
        return memory

    def add_memory(self, user: str, reply: str, max_rounds: int) -> None:
        user = (user or "").strip()[:300]
        reply = (reply or "").strip()[:300]
        if not user or not reply:
            return
        self.memory.append({"user": user, "reply": reply})
        if len(self.memory) > max_rounds:
            self.memory = self.memory[-max_rounds:]

    def add_pending(self, event: dict[str, Any]) -> None:
        event_id = str(event.get("event_id") or "")
        self.pending = [item for item in self.pending if item.get("event_id") != event_id]
        self.pending.append(event)
        if len(self.pending) > MAX_PENDING:
            self.pending = self.pending[-MAX_PENDING:]

    def remove_pending(self, event_id: str) -> bool:
        before = len(self.pending)
        self.pending = [item for item in self.pending if item.get("event_id") != event_id]
        return len(self.pending) != before

    def pending_snapshot(self) -> list[dict[str, Any]]:
        now = time.time()
        items: list[dict[str, Any]] = []
        for event in reversed(self.pending):
            try:
                age = max(0.0, now - float(event.get("timestamp") or now))
            except (TypeError, ValueError):
                age = 0.0
            items.append({"event": event, "waiting_seconds": round(age, 1)})
        return items

    def find_danmaku(self, event_id: str | None = None) -> dict[str, Any] | None:
        if event_id:
            if self.last_danmaku and self.last_danmaku.get("event_id") == event_id:
                return self.last_danmaku
            for item in self.pending:
                if item.get("event_id") == event_id:
                    return item
            for entry in self.recent:
                event = entry.get("event") if isinstance(entry, dict) else None
                if isinstance(event, dict) and event.get("event_id") == event_id:
                    return event
            return None
        if self.pending:
            return self.pending[-1]
        return self.last_danmaku

    def add_entry(self, entry: dict[str, Any]) -> None:
        self.recent.insert(0, entry)
        if len(self.recent) > MAX_RECENT:
            self.recent = self.recent[:MAX_RECENT]

    def find_entry(self, command_id: str) -> dict[str, Any] | None:
        for entry in self.recent:
            if entry.get("command_id") == command_id:
                return entry
        return None

    def current_entry(self) -> dict[str, Any] | None:
        for entry in self.recent:
            if entry.get("status") in ACTIVE_STATUSES:
                return entry
        return None

    def idle(self, clients: int = 0) -> bool:
        return not clients and not self.pending and self.current_entry() is None

    def snapshot(self, clients: list[dict[str, Any]] | None = None, *, queue_max: int = 50) -> dict[str, Any]:
        current = self.current_entry()
        stats = dict(self.stats)
        completed = max(1, int(stats.get("completed", 0)))
        stats["avg_decision_ms"] = round(float(stats.get("decision_ms_total", 0)) / completed, 1)
        stats["avg_speech_ms"] = round(float(stats.get("speech_ms_total", 0)) / completed, 1)
        clients = clients or []
        return {
            "room_id": self.room_id,
            "auto_reply": self.auto_reply,
            "paused": self.paused,
            "created_at": self.created_at,
            "last_event_at": self.last_event_at,
            "idle": self.idle(len(clients)),
            "queue": {
                "room_id": self.room_id,
                "pending": 0,
                "maxsize": queue_max,
                "stale_after_seconds": 0,
                "dropped_total": self.stats.get("dropped", 0),
                "items": [],
            },
            "stats": stats,
            "current": current,
            "current_command_id": current.get("command_id") if current else None,
            "pending": self.pending_snapshot(),
            "recent": list(self.recent),
            "last_danmaku": self.last_danmaku,
            "memory_rounds": len(self.memory),
            "memory": list(self.memory),
            "clients": clients,
        }


class StateStore(Protocol):
    backend: str

    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def get_room(self, room_id: str, *, default_auto_reply: bool = False) -> RoomState: ...
    async def save_room(self, state: RoomState) -> None: ...
    async def delete_room(self, room_id: str) -> None: ...
    async def list_rooms(self) -> list[RoomState]: ...
    async def dedup(self, key: str, ttl_s: float) -> bool: ...
    async def load_persona(self) -> dict[str, Any] | None: ...
    async def save_persona(self, persona: dict[str, Any]) -> None: ...
    def lock(self, room_id: str) -> Any: ...
    async def ping(self) -> bool: ...


class MemoryStateStore:
    """Dev/test only: state disappears with the process."""

    backend = "memory"

    def __init__(self, *, room_ttl_s: float = 1800.0, max_rooms: int = 50) -> None:
        self.room_ttl_s = max(60.0, float(room_ttl_s))
        self.max_rooms = max(1, int(max_rooms))
        self._rooms: dict[str, RoomState] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._dedup = DedupCache(ttl_s=3600, maxsize=20_000)
        self._persona: dict[str, Any] | None = None
        self._closed = False

    async def start(self) -> None:
        self._closed = False

    async def close(self) -> None:
        self._closed = True
        self._rooms.clear()
        self._locks.clear()

    async def ping(self) -> bool:
        return not self._closed

    async def get_room(self, room_id: str, *, default_auto_reply: bool = False) -> RoomState:
        self._prune()
        state = self._rooms.get(room_id)
        if state is None:
            state = RoomState.new(room_id, auto_reply=default_auto_reply)
            self._rooms[room_id] = state
        return state

    async def save_room(self, state: RoomState) -> None:
        self._prune()
        if len(self._rooms) >= self.max_rooms and state.room_id not in self._rooms:
            # Drop the oldest idle room instead of rejecting the live one.
            idle = [item for item in self._rooms.values() if item.idle()]
            if idle:
                oldest = min(idle, key=lambda item: item.last_event_at)
                self._rooms.pop(oldest.room_id, None)
        state.updated_at = time.time()
        self._rooms[state.room_id] = state

    async def delete_room(self, room_id: str) -> None:
        self._rooms.pop(room_id, None)
        self._locks.pop(room_id, None)

    async def list_rooms(self) -> list[RoomState]:
        self._prune()
        return sorted(self._rooms.values(), key=lambda item: item.last_event_at, reverse=True)

    async def dedup(self, key: str, ttl_s: float) -> bool:
        # The memory cache has its own TTL; ttl_s is kept for API parity.
        return self._dedup.check_and_add(key)

    async def load_persona(self) -> dict[str, Any] | None:
        return dict(self._persona) if self._persona else None

    async def save_persona(self, persona: dict[str, Any]) -> None:
        self._persona = dict(persona)

    @asynccontextmanager
    async def lock(self, room_id: str) -> AsyncIterator[None]:
        lock = self._locks.setdefault(room_id, asyncio.Lock())
        async with lock:
            yield

    def _prune(self) -> None:
        cutoff = time.time() - self.room_ttl_s
        for room_id, state in list(self._rooms.items()):
            if state.updated_at < cutoff and state.idle():
                self._rooms.pop(room_id, None)
                self._locks.pop(room_id, None)


class RedisStateStore:
    """Production: room documents, dedup and persona live in Redis."""

    backend = "redis"

    def __init__(
        self,
        url: str,
        *,
        room_ttl_s: float = 1800.0,
        lock_timeout_s: float = 5.0,
        lock_ttl_s: float = 60.0,
        client: Any = None,
    ) -> None:
        self.url = url
        self.room_ttl_s = max(60.0, float(room_ttl_s))
        self.lock_timeout_s = max(1.0, float(lock_timeout_s))
        self.lock_ttl_s = max(self.lock_timeout_s, float(lock_ttl_s))
        self._client: Any = client

    async def start(self) -> None:
        if self._client is None:
            if not self.url:
                raise RuntimeError("REDIS_URL is required for RedisStateStore")
            import redis.asyncio as redis  # lazy: the memory backend needs no redis

            self._client = redis.from_url(self.url, decode_responses=True)
        await self._client.ping()

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    async def ping(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    async def get_room(self, room_id: str, *, default_auto_reply: bool = False) -> RoomState:
        assert self._client is not None
        raw = await self._client.get(f"{ROOM_PREFIX}{room_id}")
        if not raw:
            return RoomState.new(room_id, auto_reply=default_auto_reply)
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return RoomState.new(room_id, auto_reply=default_auto_reply)
        try:
            return RoomState.from_dict(data, default_auto_reply=default_auto_reply)
        except ValueError:
            return RoomState.new(room_id, auto_reply=default_auto_reply)

    async def save_room(self, state: RoomState) -> None:
        assert self._client is not None
        state.updated_at = time.time()
        await self._client.setex(
            f"{ROOM_PREFIX}{state.room_id}",
            int(self.room_ttl_s),
            json.dumps(state.to_dict(), ensure_ascii=False),
        )

    async def delete_room(self, room_id: str) -> None:
        assert self._client is not None
        await self._client.delete(f"{ROOM_PREFIX}{room_id}")

    async def list_rooms(self) -> list[RoomState]:
        assert self._client is not None
        rooms: list[RoomState] = []
        async for key in self._client.scan_iter(match=f"{ROOM_PREFIX}*"):
            raw = await self._client.get(key)
            if not raw:
                continue
            try:
                rooms.append(RoomState.from_dict(json.loads(raw)))
            except (TypeError, ValueError):
                continue
        return sorted(rooms, key=lambda item: item.last_event_at, reverse=True)

    async def dedup(self, key: str, ttl_s: float) -> bool:
        assert self._client is not None
        # SET NX EX is atomic across instances.
        result = await self._client.set(
            f"{DEDUP_PREFIX}{key}", "1", nx=True, ex=max(1, int(ttl_s))
        )
        return bool(result)

    async def load_persona(self) -> dict[str, Any] | None:
        assert self._client is not None
        raw = await self._client.get(PERSONA_KEY)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    async def save_persona(self, persona: dict[str, Any]) -> None:
        assert self._client is not None
        await self._client.set(PERSONA_KEY, json.dumps(persona, ensure_ascii=False))

    @asynccontextmanager
    async def lock(self, room_id: str) -> AsyncIterator[None]:
        assert self._client is not None
        key = f"{LOCK_PREFIX}{room_id}"
        token = uuid.uuid4().hex
        deadline = time.monotonic() + self.lock_timeout_s
        while True:
            acquired = await self._client.set(
                key, token, nx=True, px=int(self.lock_ttl_s * 1000)
            )
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f"房间 {room_id} 的状态锁等待超时")
            await asyncio.sleep(0.02)
        try:
            yield
        finally:
            script = (
                "if redis.call('get', KEYS[1]) == ARGV[1] "
                "then return redis.call('del', KEYS[1]) else return 0 end"
            )
            try:
                await self._client.eval(script, 1, key, token)
            except Exception:
                pass


def create_state_store(config: Any) -> StateStore:
    backend = str(getattr(config, "state_backend", "memory")).lower()
    if backend == "redis":
        return RedisStateStore(
            str(getattr(config, "redis_url", "")),
            room_ttl_s=float(getattr(config, "room_ttl_s", 1800.0)),
            lock_timeout_s=float(getattr(config, "lock_timeout_s", 5.0)),
            lock_ttl_s=float(getattr(config, "lock_ttl_s", 60.0)),
        )
    if backend == "memory":
        return MemoryStateStore(room_ttl_s=float(getattr(config, "room_ttl_s", 1800.0)))
    raise ValueError(f"unsupported STATE_BACKEND: {backend}")
