"""Downlink command and uplink result contracts.

    cloud  --PerformCommand-->  gateway  -->  local client
    cloud  <--PerformResult---  gateway  <--  local client

Every command carries an absolute ``expires_at`` so a client that reconnects
late can drop stale普通评论 instead of performing an old danmaku, and a stable
``command_id`` so retries never perform twice.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from danmaku_pet.shared.events import Event
from danmaku_pet.shared.plans import ActionPlan
from danmaku_pet.shared.protocol import PROTOCOL_VERSION

PerformStatus = Literal[
    "accepted",
    "started",
    "completed",
    "interrupted",
    "expired",
    "error",
]
PERFORM_STATUSES: tuple[str, ...] = get_args(PerformStatus)
TERMINAL_STATUSES = frozenset({"completed", "interrupted", "expired", "error"})

_TTS_PREFIX_LIMIT = 180


def speech_text_for(event: Event, plan: ActionPlan, read_aloud: bool) -> str:
    """The exact text the client should synthesize.

    Auto-reply mode reads the danmaku first and then speaks the reply; the
    manual/disabled mode only speaks the reply.  Both the cloud console and the
    local executor call this so they never drift apart.
    """

    if not read_aloud or event.is_gift:
        return plan.reply
    heard = " ".join(event.display_text.split())
    if len(heard) > _TTS_PREFIX_LIMIT:
        heard = heard[: _TTS_PREFIX_LIMIT - 1].rstrip() + "…"
    return f"{event.user}说：{heard}。{plan.reply}"


@dataclass(frozen=True, slots=True)
class PerformCommand:
    """One "please perform this" instruction from the cloud."""

    command_id: str
    room_id: str
    event: Event
    plan: ActionPlan
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 10.0)
    protocol_version: int = PROTOCOL_VERSION
    read_aloud: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "command_id", str(self.command_id or "").strip()[:128]
        )
        object.__setattr__(self, "room_id", str(self.room_id or "").strip()[:128])
        if not self.command_id:
            raise ValueError("command_id is required")
        if not self.room_id:
            raise ValueError("room_id is required")
        if not isinstance(self.event, Event):
            raise TypeError("event must be an Event")
        if not isinstance(self.plan, ActionPlan):
            raise TypeError("plan must be an ActionPlan")
        if not isinstance(self.created_at, (int, float)) or self.created_at <= 0:
            raise ValueError("created_at must be a positive epoch number")
        if not isinstance(self.expires_at, (int, float)) or self.expires_at <= 0:
            raise ValueError("expires_at must be a positive epoch number")
        if self.expires_at < self.created_at:
            raise ValueError("expires_at must not be earlier than created_at")
        if self.protocol_version < 1:
            raise ValueError("protocol_version must be >= 1")

    @property
    def lifetime_s(self) -> float:
        return max(0.0, float(self.expires_at) - float(self.created_at))

    def is_expired(self, now: float | None = None, *, grace_s: float = 0.0) -> bool:
        current = now if now is not None else time.time()
        return current > float(self.expires_at) + max(0.0, float(grace_s))

    def remaining_ms(self, now: float | None = None) -> int:
        current = now if now is not None else time.time()
        return max(0, int((float(self.expires_at) - current) * 1000))

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "command_id": self.command_id,
            "room_id": self.room_id,
            "event": self.event.to_dict(),
            "plan": self.plan.to_dict(),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "read_aloud": self.read_aloud,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PerformCommand":
        if not isinstance(data, Mapping):
            raise ValueError("command payload must be an object")
        try:
            raw_event = data["event"]
            raw_plan = data["plan"]
        except KeyError as exc:
            raise ValueError(f"command payload is missing {exc.args[0]!r}") from exc
        return cls(
            command_id=str(data.get("command_id") or ""),
            room_id=str(data.get("room_id") or ""),
            event=Event.from_dict(raw_event),
            plan=ActionPlan.from_dict(raw_plan),
            created_at=float(data.get("created_at") or 0) or time.time(),
            expires_at=float(data.get("expires_at") or 0) or time.time(),
            protocol_version=int(data.get("protocol_version") or PROTOCOL_VERSION),
            read_aloud=bool(data.get("read_aloud", False)),
        )


@dataclass(frozen=True, slots=True)
class PerformResult:
    """The client's acknowledgement of one command (final and intermediate)."""

    command_id: str
    room_id: str
    status: PerformStatus | str
    speech_ms: int = 0
    error_code: str = ""
    error_message: str = ""
    client_id: str = ""
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "command_id", str(self.command_id or "").strip()[:128]
        )
        object.__setattr__(self, "room_id", str(self.room_id or "").strip()[:128])
        status = str(self.status or "").strip().lower()
        if status not in PERFORM_STATUSES:
            raise ValueError(f"unsupported result status: {self.status!r}")
        object.__setattr__(self, "status", status)
        if not self.command_id:
            raise ValueError("command_id is required")
        if not self.room_id:
            raise ValueError("room_id is required")
        try:
            speech_ms = int(self.speech_ms)
        except (TypeError, ValueError) as exc:
            raise ValueError("speech_ms must be an integer") from exc
        object.__setattr__(self, "speech_ms", max(0, min(speech_ms, 120_000)))
        object.__setattr__(self, "error_code", str(self.error_code or "")[:80])
        object.__setattr__(self, "error_message", str(self.error_message or "")[:400])
        object.__setattr__(self, "client_id", str(self.client_id or "")[:128])

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "room_id": self.room_id,
            "status": self.status,
            "speech_ms": self.speech_ms,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "client_id": self.client_id,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PerformResult":
        if not isinstance(data, Mapping):
            raise ValueError("result payload must be an object")
        return cls(
            command_id=str(data.get("command_id") or ""),
            room_id=str(data.get("room_id") or ""),
            status=str(data.get("status") or ""),
            speech_ms=data.get("speech_ms") or 0,
            error_code=data.get("error_code") or "",
            error_message=data.get("error_message") or "",
            client_id=data.get("client_id") or "",
            updated_at=float(data.get("updated_at") or 0) or time.time(),
        )


def new_command_id() -> str:
    return f"cmd_{uuid.uuid4().hex}"
