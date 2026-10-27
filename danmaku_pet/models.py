"""Shared data structures crossing the three layers.

Input adapters only produce `Event`; the decision layer only produces
`ActionPlan`; the execution layer only consumes `ActionPlan` + `VisualCue`.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

Source = Literal["manual", "danmaku_sim", "gift_sim"]
SOURCES: tuple[str, ...] = get_args(Source)

Emotion = Literal["happy", "sad", "surprised", "shy", "angry", "neutral"]
MOTIONS = (
    "idle",
    "nod",
    "wave",
    "jump",
    "spin",
    "shake_head",
    "shy",
    "sad",
    "surprised",
)
Motion = Literal[
    "idle",
    "nod",
    "wave",
    "jump",
    "spin",
    "shake_head",
    "shy",
    "sad",
    "surprised",
]
Priority = Literal["normal", "high"]
EMOTIONS: tuple[str, ...] = get_args(Emotion)


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

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"unsupported source: {self.source!r}")
        if not isinstance(self.timestamp, (int, float)) or self.timestamp <= 0:
            raise ValueError("timestamp must be a positive epoch number")
        if self.gift_count < 0:
            raise ValueError("gift_count must be >= 0")
        object.__setattr__(self, "user", (self.user or "观众").strip()[:40])
        object.__setattr__(self, "text", (self.text or "").strip()[:500])
        if self.gift_name is not None:
            object.__setattr__(self, "gift_name", str(self.gift_name).strip()[:60] or None)
        if self.is_gift and not self.gift_name:
            object.__setattr__(self, "gift_name", "礼物")
        if self.is_gift and self.gift_count == 0:
            object.__setattr__(self, "gift_count", 1)

    @property
    def is_gift(self) -> bool:
        return self.source == "gift_sim" or self.gift_name is not None

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
        return {
            "source": self.source,
            "user": self.user,
            "text": self.text,
            "gift_name": self.gift_name,
            "gift_count": self.gift_count,
            "timestamp": self.timestamp,
        }


class ActionPlan(BaseModel):
    """The strict JSON contract returned by the decision layer."""

    model_config = ConfigDict(extra="ignore")

    reply: str = Field(min_length=1, max_length=240)
    emotion: Emotion
    motion: Motion
    priority: Priority
    interrupt: bool

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


@dataclass(slots=True)
class DecisionResult:
    plan: ActionPlan
    source: str
    raw: dict[str, Any] | None = None
    latency_ms: int = 0
    model: str = ""
    warnings: tuple[str, ...] = ()


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
        }
