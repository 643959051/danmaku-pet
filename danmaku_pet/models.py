"""Local runtime data structures plus re-exports of the shared contracts.

``Event`` / ``ActionPlan`` / ``DecisionResult`` now live in
:mod:`danmaku_pet.shared` because they cross the cloud/client boundary.  They
are re-exported here so existing imports keep working.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from danmaku_pet.shared.events import (
    MESSAGE_TYPES,
    SOURCES,
    Event,
    MessageType,
    Source,
)
from danmaku_pet.shared.plans import (
    EMOTIONS,
    MOTIONS,
    ActionPlan,
    DecisionResult,
    Emotion,
    Motion,
    Priority,
    estimate_speech_ms,
)

__all__ = [
    "ActionPlan",
    "AudioClip",
    "DecisionResult",
    "EMOTIONS",
    "Emotion",
    "Event",
    "LogEntry",
    "MESSAGE_TYPES",
    "MOTIONS",
    "MessageType",
    "Motion",
    "Priority",
    "SOURCES",
    "Source",
    "VisualCue",
    "estimate_speech_ms",
]


@dataclass(slots=True)
class AudioClip:
    path: Path | None
    duration_ms: int
    cached: bool = False
    error: str | None = None
    text: str = ""


@dataclass(frozen=True, slots=True)
class VisualCue:
    """Backend-neutral performance description built by the action adapter."""

    motion: Motion
    emotion: Emotion
    mode: Literal["idle", "talk", "pulse"]
    native_state: str
    native_activity: str | None
    native_hold_ms: int
    browser_clip: str
    browser_start: int
    browser_end: int
    browser_fps: int
    browser_loop: bool
    transform: str
    hold_ms: int
    speech_ms: int
    label: str
    note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LogEntry:
    """One event's journey through the cloud decision pipeline."""

    event: Event
    plan: ActionPlan | None = None
    decision_source: str = ""
    latency_ms: int = 0
    audio_ms: int = 0
    read_aloud: bool = False
    status: str = "queued"
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    # --- cloud protocol bookkeeping --------------------------------------
    room_id: str = ""
    command_id: str = ""
    dispatched_at: float | None = None
    result_status: str = ""
    speech_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.to_dict(),
            "plan": self.plan.to_dict() if self.plan else None,
            "decision_source": self.decision_source,
            "latency_ms": self.latency_ms,
            "audio_ms": self.audio_ms,
            "read_aloud": self.read_aloud,
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "room_id": self.room_id,
            "command_id": self.command_id,
            "dispatched_at": self.dispatched_at,
            "result_status": self.result_status,
            "speech_ms": self.speech_ms,
        }
