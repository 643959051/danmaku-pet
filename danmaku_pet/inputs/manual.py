"""Input adapter boundary. Only this package knows where simulated events come from."""

from __future__ import annotations

import time

from danmaku_pet.models import Event


def manual_event(text: str, user: str = "观众", room_id: str = "") -> Event:
    return Event(
        source="manual",
        user=user or "观众",
        text=text,
        room_id=room_id,
        timestamp=time.time(),
        message_type="comment",
    )
