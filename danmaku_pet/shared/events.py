"""Normalized event contract shared by the cloud backend and the local client.

An ``Event`` is produced by an input adapter (manual box, simulator, official
Douyin comment/gift push) and consumed by the cloud decision layer.  It is also
embedded in every :class:`~danmaku_pet.shared.commands.PerformCommand`, so the
client can display the original danmaku next to the reply.

The 阶段 2 fields are all optional so that old call sites and the simulator keep
working:

    room_id, message_id, message_type, user_id, gift_id
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, get_args

Source = Literal["manual", "danmaku_sim", "gift_sim", "douyin_comment", "douyin_gift"]
SOURCES: tuple[str, ...] = get_args(Source)

MessageType = Literal["comment", "gift"]
MESSAGE_TYPES: tuple[str, ...] = get_args(MessageType)

GIFT_SOURCES = frozenset({"gift_sim", "douyin_gift"})

MAX_TEXT_LEN = 500
MAX_USER_LEN = 40
MAX_NAME_LEN = 60
MAX_ID_LEN = 128


def _clean(value: Any, limit: int) -> str:
    return str(value if value is not None else "").strip()[:limit]


@dataclass(frozen=True, slots=True)
class Event:
    """Normalized input event. This is the only input contract downstream."""

    source: Source
    user: str
    text: str
    gift_name: str | None = None
    gift_count: int = 0
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    # --- shared protocol v2 (all optional for backward compatibility) -----
    room_id: str = ""
    message_id: str = ""
    message_type: MessageType | str = ""
    user_id: str = ""
    gift_id: str = ""

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"unsupported source: {self.source!r}")
        if not isinstance(self.timestamp, (int, float)) or self.timestamp <= 0:
            raise ValueError("timestamp must be a positive epoch number")
        if self.gift_count < 0:
            raise ValueError("gift_count must be >= 0")

        object.__setattr__(self, "user", _clean(self.user, MAX_USER_LEN) or "观众")
        object.__setattr__(self, "text", _clean(self.text, MAX_TEXT_LEN))
        object.__setattr__(self, "room_id", _clean(self.room_id, MAX_ID_LEN))
        object.__setattr__(self, "message_id", _clean(self.message_id, MAX_ID_LEN))
        object.__setattr__(self, "user_id", _clean(self.user_id, MAX_ID_LEN))
        object.__setattr__(self, "gift_id", _clean(self.gift_id, MAX_ID_LEN))
        object.__setattr__(self, "event_id", _clean(self.event_id, MAX_ID_LEN) or uuid.uuid4().hex[:12])
        if self.gift_name is not None:
            object.__setattr__(
                self, "gift_name", _clean(self.gift_name, MAX_NAME_LEN) or None
            )

        # message_type is derived when the caller did not state it.  A gift is
        # recognized by its source or by carrying a gift name, which keeps the
        # old ``Event(source="gift_sim", gift_name="玫瑰")`` call sites working.
        declared = str(self.message_type or "").strip().lower()
        if declared not in MESSAGE_TYPES:
            declared = "gift" if self.source in GIFT_SOURCES or self.gift_name is not None else "comment"
        object.__setattr__(self, "message_type", declared)

        if self.is_gift:
            if not self.gift_name:
                object.__setattr__(self, "gift_name", "礼物")
            if self.gift_count == 0:
                object.__setattr__(self, "gift_count", 1)

    @property
    def is_gift(self) -> bool:
        return self.message_type == "gift"

    @property
    def effective_message_id(self) -> str:
        """Stable platform id when available, otherwise the local event id."""

        return self.message_id or self.event_id

    @property
    def display_text(self) -> str:
        if self.is_gift:
            count = f" × {self.gift_count}" if self.gift_count > 1 else ""
            return self.text or f"送出 {self.gift_name}{count}"
        return self.text

    def age_seconds(self, now: float | None = None) -> float:
        return max(0.0, (now if now is not None else time.time()) - float(self.timestamp))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["is_gift"] = self.is_gift
        return data

    def to_llm_dict(self) -> dict[str, Any]:
        """Only the fields the model needs; room/ids stay local."""

        return {
            "source": self.source,
            "user": self.user,
            "text": self.text,
            "gift_name": self.gift_name,
            "gift_count": self.gift_count,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Event":
        """Rebuild an event from its wire representation, validating types."""

        if not isinstance(data, Mapping):
            raise ValueError("event payload must be an object")
        raw_source = data.get("source")
        if not raw_source:
            raise ValueError("event payload is missing 'source'")
        try:
            gift_count = int(data.get("gift_count") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid gift_count: {data.get('gift_count')!r}") from exc
        try:
            timestamp = float(data.get("timestamp") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid timestamp: {data.get('timestamp')!r}") from exc
        gift_name = data.get("gift_name")
        return cls(
            source=str(raw_source),
            user=data.get("user", ""),
            text=data.get("text", ""),
            gift_name=None if gift_name is None else str(gift_name),
            gift_count=gift_count,
            timestamp=timestamp if timestamp > 0 else time.time(),
            event_id=str(data.get("event_id") or uuid.uuid4().hex[:12]),
            room_id=str(data.get("room_id") or ""),
            message_id=str(data.get("message_id") or ""),
            message_type=str(data.get("message_type") or ""),
            user_id=str(data.get("user_id") or ""),
            gift_id=str(data.get("gift_id") or ""),
        )
