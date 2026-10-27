"""Future Douyin Webcast adapter seam.

Replace the *inside* of these methods with the official WebcastChatMessage /
WebcastGiftMessage protobuf objects. The rest of the project keeps consuming
the same Event fields and does not change.

Observed field shapes (official webcast protobuf-like names):
  WebcastChatMessage: user.nickname, content, common.createTime (ms)
  WebcastGiftMessage: user.nickname, gift.name, repeatCount/comboCount,
                      common.createTime (ms)
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from danmaku_pet.models import Event


def _timestamp_from_common(payload: Mapping[str, Any]) -> float:
    common = payload.get("common")
    raw = common.get("createTime") if isinstance(common, Mapping) else None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return time.time()
    if value > 10_000_000_000:  # milliseconds
        value /= 1000.0
    return value if value > 0 else time.time()


def _user_name(payload: Mapping[str, Any]) -> str:
    user = payload.get("user")
    if isinstance(user, Mapping):
        for key in ("nickname", "displayName", "display_name"):
            value = user.get(key)
            if value:
                return str(value)
    return "观众"


class DouyinWebcastAdapter:
    """The only module that should change when real Douyin messages arrive."""

    def from_webcast_chat(self, message: Mapping[str, Any]) -> Event:
        return Event(
            source="danmaku_sim",
            user=_user_name(message),
            text=str(message.get("content", "")),
            timestamp=_timestamp_from_common(message),
        )

    def from_webcast_gift(self, message: Mapping[str, Any]) -> Event:
        gift = message.get("gift")
        gift_name = "礼物"
        if isinstance(gift, Mapping):
            gift_name = str(gift.get("name") or gift.get("describe") or "礼物")
        raw_count = message.get("repeatCount", message.get("comboCount", 1))
        try:
            count = max(1, int(raw_count))
        except (TypeError, ValueError):
            count = 1
        return Event(
            source="gift_sim",
            user=_user_name(message),
            text=f"送出 {gift_name}" + (f" × {count}" if count > 1 else ""),
            gift_name=gift_name,
            gift_count=count,
            timestamp=_timestamp_from_common(message),
        )

    def normalize(self, message: Mapping[str, Any], *, gift: bool = False) -> Event:
        return self.from_webcast_gift(message) if gift else self.from_webcast_chat(message)
