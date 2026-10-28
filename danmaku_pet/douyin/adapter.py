"""Official message -> unified :class:`Event`.

Field names are extracted through small candidate lists instead of being
hardcoded to one document version: the platform sample we get in 阶段 5 may
differ from the names seen in the wild.  Missing mandatory data raises
:class:`MessageFormatError`, which the receiver turns into a 4xx so the platform
can retry rather than the service crashing.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from danmaku_pet.shared.events import Event


class MessageFormatError(ValueError):
    """The payload cannot be turned into an Event."""


def _first(payload: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _nested(payload: Mapping[str, Any], keys: tuple[str, ...]) -> Mapping[str, Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _timestamp(payload: Mapping[str, Any]) -> float:
    common = _nested(payload, ("common", "common_info", "commonInfo"))
    raw = _first(common, ("createTime", "create_time", "timestamp", "msgTime", "msg_time"))
    if raw is None:
        raw = _first(payload, ("create_time", "createTime", "timestamp", "msg_time"))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return time.time()
    if value > 10_000_000_000:  # milliseconds
        value /= 1000.0
    return value if value > 0 else time.time()


def _room_id(payload: Mapping[str, Any], fallback: str) -> str:
    common = _nested(payload, ("common", "common_info", "commonInfo"))
    raw = _first(payload, ("room_id", "roomId", "roomID"))
    if raw is None:
        raw = _first(common, ("roomId", "room_id", "roomID"))
    return str(raw or fallback or "").strip()


def _message_id(payload: Mapping[str, Any]) -> str:
    common = _nested(payload, ("common", "common_info", "commonInfo"))
    raw = _first(payload, ("message_id", "messageId", "msg_id", "msgId"))
    if raw is None:
        raw = _first(common, ("msgId", "messageId", "message_id"))
    if raw is None:
        raw = _first(payload, ("msg_uid", "msgUid", "id"))
    return str(raw or "").strip()


class DouyinEventAdapter:
    def __init__(self, default_room_id: str = "") -> None:
        self.default_room_id = default_room_id

    # ------------------------------------------------------------------ chat
    def from_comment(self, payload: Mapping[str, Any], *, room_id: str = "") -> Event:
        if not isinstance(payload, Mapping):
            raise MessageFormatError("comment payload must be an object")
        user = _nested(payload, ("user", "user_info", "userInfo"))
        text = _first(payload, ("content", "text", "comment", "message"))
        if text is None:
            raise MessageFormatError("comment payload has no content")
        text = str(text).strip()
        if not text:
            raise MessageFormatError("comment content is empty")
        return Event(
            source="douyin_comment",
            user=_user_name(user) or str(_first(payload, ("nickname", "user_name")) or "观众"),
            user_id=_user_id(user) or str(_first(payload, ("user_id", "userId")) or ""),
            text=text,
            timestamp=_timestamp(payload),
            room_id=_room_id(payload, room_id or self.default_room_id),
            message_id=_message_id(payload),
            message_type="comment",
        )

    # ------------------------------------------------------------------ gift
    def from_gift(self, payload: Mapping[str, Any], *, room_id: str = "") -> Event:
        if not isinstance(payload, Mapping):
            raise MessageFormatError("gift payload must be an object")
        user = _nested(payload, ("user", "user_info", "userInfo"))
        gift = _nested(payload, ("gift", "gift_info", "giftInfo"))
        gift_name = _first(gift, ("name", "gift_name", "giftName", "describe", "desc"))
        if gift_name is None:
            gift_name = _first(payload, ("gift_name", "giftName"))
        gift_id = _first(gift, ("id", "gift_id", "giftId", "diamond_id"))
        if gift_id is None:
            gift_id = _first(payload, ("gift_id", "giftId"))
        if gift_name is None and gift_id is None:
            raise MessageFormatError("gift payload has neither gift id nor name")
        count_raw = _first(
            gift,
            ("repeat_count", "repeatCount", "combo_count", "comboCount", "count", "num"),
        )
        if count_raw is None:
            count_raw = _first(payload, ("repeat_count", "repeatCount", "gift_count", "giftCount", "count"))
        try:
            count = max(1, int(count_raw or 1))
        except (TypeError, ValueError):
            count = 1
        name = str(gift_name).strip() if gift_name is not None else "礼物"
        return Event(
            source="douyin_gift",
            user=_user_name(user) or str(_first(payload, ("nickname", "user_name")) or "观众"),
            user_id=_user_id(user) or str(_first(payload, ("user_id", "userId")) or ""),
            text=f"送出 {name}" + (f" × {count}" if count > 1 else ""),
            gift_name=name,
            gift_id=str(gift_id or "").strip(),
            gift_count=count,
            timestamp=_timestamp(payload),
            room_id=_room_id(payload, room_id or self.default_room_id),
            message_id=_message_id(payload),
            message_type="gift",
        )

    # --------------------------------------------------------------- generic
    def normalize(
        self,
        payload: Mapping[str, Any],
        *,
        kind: str,
        room_id: str = "",
    ) -> Event:
        normalized = (kind or "").strip().lower()
        if normalized in {"comment", "chat", "danmaku"}:
            return self.from_comment(payload, room_id=room_id)
        if normalized in {"gift", "like_gift"}:
            return self.from_gift(payload, room_id=room_id)
        raise MessageFormatError(f"unsupported message kind: {kind!r}")


def _user_name(user: Mapping[str, Any]) -> str:
    value = _first(user, ("nickname", "display_name", "displayName", "name", "user_name"))
    return str(value).strip() if value is not None else ""


def _user_id(user: Mapping[str, Any]) -> str:
    value = _first(user, ("id", "user_id", "userId", "open_id", "openid", "sec_openid", "sec_uid"))
    return str(value).strip() if value is not None else ""
