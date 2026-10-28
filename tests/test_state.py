from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from danmaku_pet.cloud.state import (
    MemoryStateStore,
    RedisStateStore,
    RoomState,
    create_state_store,
)
from tests.support import make_event


def test_room_state_json_roundtrip() -> None:
    state = RoomState.new("room-a", auto_reply=True)
    state.add_memory("观众", "回复", 10)
    state.add_pending(make_event(room_id="room-a").to_dict())
    state.add_entry({"command_id": "cmd-1", "status": "dispatched", "event": {}})
    state.stats["received"] = 3
    restored = RoomState.from_dict(state.to_dict())
    assert restored.room_id == "room-a"
    assert restored.auto_reply is True
    assert restored.memory == state.memory
    assert restored.pending == state.pending
    assert restored.stats["received"] == 3
    assert restored.recent[0]["command_id"] == "cmd-1"


def test_room_state_pending_is_bounded_and_deduplicated() -> None:
    state = RoomState.new("room-a")
    for index in range(40):
        event = make_event(text=f"m{index}", room_id="room-a")
        state.add_pending(event.to_dict())
    assert len(state.pending) == 30
    same = state.pending[-1]
    state.add_pending(same)
    assert len(state.pending) == 30


def test_room_state_memory_is_bounded() -> None:
    state = RoomState.new("room-a")
    for index in range(20):
        state.add_memory(f"u{index}", f"r{index}", 5)
    assert len(state.memory) == 5
    assert state.memory[-1]["user"] == "u19"


def test_room_snapshot_shape() -> None:
    state = RoomState.new("room-a")
    snapshot = state.snapshot([{"client_id": "c1"}])
    for key in ("room_id", "queue", "stats", "pending", "recent", "clients", "current"):
        assert key in snapshot
    assert snapshot["clients"][0]["client_id"] == "c1"


async def test_memory_store_persists_rooms_and_dedup() -> None:
    store = MemoryStateStore(room_ttl_s=60)
    await store.start()
    try:
        state = await store.get_room("room-a", default_auto_reply=True)
        assert state.auto_reply is True
        state.stats["received"] = 2
        await store.save_room(state)

        loaded = await store.get_room("room-a")
        assert loaded.stats["received"] == 2
        assert await store.dedup("same", 3600) is True
        assert await store.dedup("same", 3600) is False
        assert [item.room_id for item in await store.list_rooms()] == ["room-a"]
    finally:
        await store.close()


async def test_memory_store_persona_roundtrip() -> None:
    store = MemoryStateStore()
    await store.start()
    try:
        assert await store.load_persona() is None
        await store.save_persona({"name": "小盐"})
        assert (await store.load_persona())["name"] == "小盐"
        await store.cache_set("token", {"value": "abc"}, 60)
        assert await store.cache_get("token") == {"value": "abc"}
        assert await store.cache_get("missing") is None
    finally:
        await store.close()


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.closed = False

    async def ping(self) -> bool:
        return not self.closed

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None, px: int | None = None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        self.data[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.data.pop(key, None) is not None else 0

    async def scan_iter(self, match: str):
        prefix = match.rstrip("*")
        for key in list(self.data):
            if key.startswith(prefix):
                yield key

    async def eval(self, script: str, numkeys: int, key: str, token: str) -> int:
        if self.data.get(key) == token:
            del self.data[key]
            return 1
        return 0

    async def aclose(self) -> None:
        self.closed = True


async def test_redis_store_serializes_rooms_and_uses_atomic_dedup() -> None:
    client = FakeRedis()
    store = RedisStateStore("redis://unused", client=client)
    await store.start()
    try:
        state = RoomState.new("room-a", auto_reply=True)
        state.stats["received"] = 4
        await store.save_room(state)

        loaded = await store.get_room("room-a")
        assert loaded.stats["received"] == 4
        assert (await store.list_rooms())[0].room_id == "room-a"

        assert await store.dedup("cmd:1", 60) is True
        assert await store.dedup("cmd:1", 60) is False

        await store.save_persona({"name": "小盐"})
        assert (await store.load_persona()) == {"name": "小盐"}
        await store.cache_set("token", {"value": "abc"}, 60)
        assert await store.cache_get("token") == {"value": "abc"}

        async with store.lock("room-a"):
            assert any(key.startswith("dmp:lock:") for key in client.data)
        assert not any(key.startswith("dmp:lock:") for key in client.data)

        await store.delete_room("room-a")
        assert await store.list_rooms() == []
    finally:
        await store.close()
    assert client.closed is True


async def test_memory_store_lock_serializes_the_same_room() -> None:
    store = MemoryStateStore()
    await store.start()
    order: list[str] = []
    try:
        async with store.lock("room-a"):
            order.append("first")
        async with store.lock("room-a"):
            order.append("second")
        assert order == ["first", "second"]
    finally:
        await store.close()


def test_create_state_store_selects_backend() -> None:
    memory = create_state_store(SimpleNamespace(state_backend="memory"))
    assert isinstance(memory, MemoryStateStore)
    redis_store = create_state_store(
        SimpleNamespace(
            state_backend="redis",
            redis_url="redis://127.0.0.1:6379/0",
            room_ttl_s=60,
            lock_timeout_s=5,
        )
    )
    assert isinstance(redis_store, RedisStateStore)
    with pytest.raises(ValueError):
        create_state_store(SimpleNamespace(state_backend="unknown"))
