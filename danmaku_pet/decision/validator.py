"""Strict validation and safe fallbacks for model output."""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import ValidationError

from danmaku_pet.models import EMOTIONS, MOTIONS, ActionPlan, Event

FALLBACK_REPLY = "我在呢，刚才没听清，再说一遍好吗？"


class PlanValidationError(ValueError):
    """Raised when the decision payload is not an object at all."""


def _clean_reply(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    text = " ".join(text.split())
    if len(text) > 240:
        text = text[:239].rstrip() + "…"
    return text


def _as_emotion(value: Any) -> tuple[str, bool]:
    normalized = str(value or "").strip().lower()
    if normalized in EMOTIONS:
        return normalized, False
    return "neutral", True


def _as_motion(value: Any) -> tuple[str, bool]:
    normalized = str(value or "").strip().lower()
    if normalized in MOTIONS:
        return normalized, False
    return "", True


def _as_priority(value: Any, event: Event) -> tuple[str, bool]:
    normalized = str(value or "").strip().lower()
    if event.is_gift:
        return "high", normalized not in {"", "high"}
    if normalized in {"normal", "high"}:
        return normalized, False
    return "normal", True


def _as_bool(value: Any, default: bool) -> tuple[bool, bool]:
    if isinstance(value, bool):
        return value, False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value), False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True, False
        if normalized in {"false", "0", "no", "n", "off"}:
            return False, False
    return default, value is not None


def normalize_plan(payload: Mapping[str, Any], event: Event) -> tuple[ActionPlan, list[str]]:
    """Validate a raw model object and return a guaranteed-valid ActionPlan.

    Unknown motions fall back to ``nod`` for positive/neutral emotions and to
    ``idle`` otherwise, exactly as the MVP contract requires.
    """

    if not isinstance(payload, Mapping):
        raise PlanValidationError("decision payload must be a JSON object")

    warnings: list[str] = []
    reply = _clean_reply(payload.get("reply"))
    if not reply:
        reply = FALLBACK_REPLY
        warnings.append("reply 缺失或为空，已使用兜底回复")

    emotion, emotion_invalid = _as_emotion(payload.get("emotion"))
    if emotion_invalid:
        warnings.append(f"非法 emotion={payload.get('emotion')!r}，已回退 neutral")

    motion, motion_invalid = _as_motion(payload.get("motion"))
    if motion_invalid:
        fallback = "nod" if emotion in {"happy", "surprised", "neutral"} else "idle"
        motion = fallback
        warnings.append(
            f"非法 motion={payload.get('motion')!r}，已回退 {fallback}"
        )

    priority, priority_invalid = _as_priority(payload.get("priority"), event)
    if priority_invalid:
        warnings.append("非法 priority，已按事件类型回退")

    default_interrupt = event.is_gift or priority == "high"
    interrupt, interrupt_invalid = _as_bool(payload.get("interrupt"), default_interrupt)
    if event.is_gift:
        # A gift is the MVP's hard preemption boundary. Even if the model says
        # false, the queue/backend must be allowed to cut the current act.
        interrupt = True
        if payload.get("interrupt") is False:
            warnings.append("礼物事件强制 interrupt=true")
    if interrupt_invalid:
        warnings.append("interrupt 不是布尔值，已使用默认值")

    plan = ActionPlan(
        reply=reply,
        emotion=emotion,  # type: ignore[arg-type]
        motion=motion,  # type: ignore[arg-type]
        priority=priority,  # type: ignore[arg-type]
        interrupt=interrupt,
    )
    return plan, warnings


def fallback_plan(event: Event) -> ActionPlan:
    if event.is_gift:
        return ActionPlan(
            reply=f"谢谢 {event.user} 的礼物！",
            emotion="happy",
            motion="wave",
            priority="high",
            interrupt=True,
        )
    return ActionPlan(
        reply=FALLBACK_REPLY,
        emotion="shy",
        motion="shy",
        priority="normal",
        interrupt=False,
    )
