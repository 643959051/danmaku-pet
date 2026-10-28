"""Message de-duplication for official pushes.

Platform retries are normal (timeouts, redeploys, reconnect storms).  When the
message carries a stable id we dedup on it; otherwise a *controlled* composite
key is used.  The composite includes a one-second timestamp bucket, so two
identical comments from the same user in the same second collapse into one
performance — documented and intentional (改造计划 阶段 5).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from danmaku_pet.shared.events import Event
from danmaku_pet.shared.protocol import DedupCache


def event_dedup_key(event: Event) -> str:
    room = event.room_id or "-"
    if event.message_id:
        return f"{room}|id|{event.message_type}|{event.message_id}"
    bucket = int(float(event.timestamp))
    identity = event.user_id or event.user
    gift = event.gift_id or event.gift_name or ""
    return (
        f"{room}|composite|{event.message_type}|{identity}|{event.text}|"
        f"{gift}|{event.gift_count}|{bucket}"
    )


@dataclass(slots=True)
class MessageDeduplicator:
    ttl_s: float = 600.0
    maxsize: int = 5000
    clock: Any = time.time
    _cache: DedupCache = field(init=False)

    def __post_init__(self) -> None:
        self._cache = DedupCache(ttl_s=self.ttl_s, maxsize=self.maxsize, clock=self.clock)

    def accept(self, event: Event, *, now: float | None = None) -> bool:
        """``True`` when the event is new and may be processed."""

        return self._cache.check_and_add(event_dedup_key(event), now=now)

    def __len__(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        self._cache.clear()
