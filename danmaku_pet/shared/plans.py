"""The decision-layer output contract: ``ActionPlan`` and friends.

The plan is deliberately small and fully enumerated so the model output can be
validated and the local client can execute it without knowing anything about
DeepSeek.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

Emotion = Literal["happy", "sad", "surprised", "shy", "angry", "neutral"]
EMOTIONS: tuple[str, ...] = get_args(Emotion)

MOTIONS: tuple[str, ...] = (
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

_SENTENCE_END = re.compile(r"[。！？!?；;，,、\s]+")


def estimate_speech_ms(text: str) -> int:
    """Conservative duration used when TTS or audio-device probing fails.

    It lives in the shared package so the cloud mock gateway can report a
    plausible ``speech_ms`` without importing the client's audio stack.
    """

    visible = len(_SENTENCE_END.sub("", text or ""))
    return max(1400, min(15000, 500 + visible * 190))


class ActionPlan(BaseModel):
    """The strict JSON contract returned by the decision layer."""

    model_config = ConfigDict(extra="ignore")

    reply: str = Field(min_length=1, max_length=240)
    emotion: Emotion
    motion: Motion
    priority: Priority
    interrupt: bool
    # Optional gift pacing hint.  The cloud never computes real speech duration
    # (that is the client's job with EdgeTTS); this is only a minimum hold.
    min_hold_ms: int = Field(default=0, ge=0, le=20_000)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ActionPlan":
        if not isinstance(data, Mapping):
            raise ValueError("plan payload must be an object")
        return cls.model_validate(dict(data))


@dataclass(slots=True)
class DecisionResult:
    plan: ActionPlan
    source: str
    raw: dict[str, Any] | None = None
    latency_ms: int = 0
    model: str = ""
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "source": self.source,
            "latency_ms": self.latency_ms,
            "model": self.model,
            "warnings": list(self.warnings),
        }
