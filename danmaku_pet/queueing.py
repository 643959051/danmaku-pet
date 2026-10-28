"""Async event queue with 3-second staleness and gift preemption."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass

from danmaku_pet.models import Event


@dataclass(frozen=True, slots=True)
class QueuedEvent:
    event: Event
    read_aloud: bool = False


@dataclass(frozen=True, slots=True)
class DropInfo:
    event: Event
    reason: str


class EventQueue:
    """One instance per room: the queue itself is a room-isolation boundary."""

    def __init__(
        self,
        *,
        maxsize: int = 50,
        stale_after_s: float = 3.0,
        room_id: str = "",
    ) -> None:
        self.maxsize = maxsize
        self.stale_after_s = stale_after_s
        self.room_id = room_id
        self._items: deque[QueuedEvent] = deque()
        self._condition = asyncio.Condition()
        self._dropped_total = 0

    @property
    def pending(self) -> int:
        return len(self._items)

    @property
    def dropped_total(self) -> int:
        return self._dropped_total

    async def put(
        self,
        event: Event,
        *,
        preempt: bool = False,
        read_aloud: bool = False,
    ) -> list[DropInfo]:
        """Put an event; high-priority gifts may clear the normal backlog."""

        if self.room_id and event.room_id and event.room_id != self.room_id:
            raise ValueError(
                f"room mismatch: queue={self.room_id!r} event={event.room_id!r}"
            )
        queued = QueuedEvent(event=event, read_aloud=read_aloud)
        dropped: list[DropInfo] = []
        async with self._condition:
            if preempt:
                kept = deque(item for item in self._items if item.event.is_gift)
                for item in self._items:
                    if not item.event.is_gift:
                        dropped.append(
                            DropInfo(
                                event=item.event,
                                reason="高优先级礼物清空普通待处理队列",
                            )
                        )
                self._items = kept
                self._items.appendleft(queued)
            else:
                if len(self._items) >= self.maxsize:
                    victim = self._items.popleft()
                    dropped.append(
                        DropInfo(event=victim.event, reason="队列已满，丢弃最旧事件")
                    )
                self._items.append(queued)
            self._dropped_total += len(dropped)
            self._condition.notify_all()
        return dropped

    async def get(self) -> tuple[QueuedEvent, list[DropInfo]]:
        dropped: list[DropInfo] = []
        async with self._condition:
            while True:
                await self._condition.wait_for(lambda: bool(self._items))
                while self._items:
                    item = self._items.popleft()
                    event = item.event
                    age = event.age_seconds(time.time())
                    if age > self.stale_after_s:
                        dropped.append(
                            DropInfo(
                                event=event,
                                reason=f"超过 {self.stale_after_s:.1f}s 未处理（实际 {age:.2f}s）",
                            )
                        )
                        self._dropped_total += 1
                        continue
                    return item, dropped

    async def drain(self, reason: str = "服务关闭") -> list[DropInfo]:
        dropped: list[DropInfo] = []
        async with self._condition:
            while self._items:
                dropped.append(DropInfo(event=self._items.popleft().event, reason=reason))
            self._dropped_total += len(dropped)
        return dropped

    def snapshot(self) -> dict[str, object]:
        return {
            "room_id": self.room_id,
            "pending": len(self._items),
            "maxsize": self.maxsize,
            "stale_after_seconds": self.stale_after_s,
            "dropped_total": self._dropped_total,
            "items": [
                {
                    "event_id": item.event.event_id,
                    "source": item.event.source,
                    "user": item.event.user,
                    "text": item.event.display_text,
                    "age_seconds": round(item.event.age_seconds(time.time()), 2),
                    "read_aloud": item.read_aloud,
                }
                for item in list(self._items)[:8]
            ],
        }
