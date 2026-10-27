"""Input adapter boundary. Only this package knows where events come from."""

from __future__ import annotations

import time

from danmaku_pet.models import Event


def manual_event(text: str, user: str = "观众") -> Event:
    return Event(
        source="manual",
        user=user or "观众",
        text=text,
        timestamp=time.time(),
    )
