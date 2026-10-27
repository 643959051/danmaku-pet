"""Map the stable motion vocabulary onto dsh-dafeiyu's state events.

dsh-dafeiyu's public helper protocol exposes states and pulses, not arbitrary
clip names. The table below is intentionally explicit: replacing this backend
with Live2D, a custom renderer, or dsh-bigfish only requires a new adapter that
consumes the same ActionPlan/VisualCue.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from danmaku_pet.models import ActionPlan, Event, Motion, VisualCue


@dataclass(frozen=True, slots=True)
class MotionMapping:
    motion: Motion
    native_state: str
    native_activity: str | None
    min_hold_ms: int
    browser_clip: str
    browser_start: int
    browser_end: int
    transform: str
    label: str
    note: str


MOTION_MAP: dict[str, MotionMapping] = {
    "idle": MotionMapping(
        "idle", "IDLE", None, 600, "idle", 0, 241, "none", "待机", "原生 idle 循环"
    ),
    "nod": MotionMapping(
        "nod", "SUCCESS", None, 1600, "success", 0, 56, "bob", "点头", "用 success 片段代替点头"
    ),
    "wave": MotionMapping(
        "wave", "SUCCESS", None, 2200, "head_pat", 0, 64, "sway", "挥手", "用 head_pat 的抬手片段代替挥手"
    ),
    "jump": MotionMapping(
        "jump", "SUCCESS", None, 2400, "success", 52, 120, "bounce", "跳跃", "用 success 的腾跃片段"
    ),
    "spin": MotionMapping(
        "spin", "SUCCESS", None, 3000, "success", 96, 196, "spin", "转圈", "helper effect 做左右转身；网页预览同步使用横向 yaw"
    ),
    "shake_head": MotionMapping(
        "shake_head", "ERROR", None, 2200, "error", 0, 64, "shake", "摇头", "用 error 片段 + 浏览器水平抖动"
    ),
    "shy": MotionMapping(
        "shy", "WAITING", None, 2400, "head_pat", 56, 140, "tilt", "害羞", "用 head_pat 的低头片段 + 倾斜"
    ),
    "sad": MotionMapping(
        "sad", "ERROR", None, 2800, "error", 28, 118, "sink", "难过", "用 error 的低头片段"
    ),
    "surprised": MotionMapping(
        "surprised", "WAITING", None, 2000, "poke", 0, 56, "pop", "惊讶", "用 poke 的受惊片段 + 弹跳"
    ),
}

EMOTION_LABELS: dict[str, str] = {
    "happy": "开心",
    "sad": "难过",
    "surprised": "惊讶",
    "shy": "害羞",
    "angry": "生气",
    "neutral": "平静",
}

TALK_STATE = "WORKING"


class ActionAdapter:
    def build_cue(
        self,
        plan: ActionPlan,
        event: Event,
        audio_duration_ms: int,
        *,
        min_hold_override: int = 0,
    ) -> VisualCue:
        mapping = MOTION_MAP[plan.motion]
        speech_ms = max(0, int(audio_duration_ms))
        mapping_min = max(mapping.min_hold_ms, int(min_hold_override))
        hold_ms = max(mapping_min, speech_ms + 300) if speech_ms else mapping_min
        if plan.motion == "idle":
            mode = "talk" if speech_ms > 0 else "idle"
            native_state = TALK_STATE if mode == "talk" else "IDLE"
            note = "说话期间用 WORKING 循环作为说话动画" if mode == "talk" else mapping.note
        else:
            mode = "pulse"
            native_state = mapping.native_state
            note = mapping.note
        return VisualCue(
            motion=plan.motion,
            emotion=plan.emotion,
            mode=mode,  # type: ignore[arg-type]
            native_state=native_state,
            native_activity=mapping.native_activity,
            native_hold_ms=hold_ms,
            browser_clip=mapping.browser_clip,
            browser_start=mapping.browser_start,
            browser_end=mapping.browser_end,
            browser_fps=24,
            browser_loop=True,
            transform=mapping.transform,
            hold_ms=hold_ms,
            speech_ms=speech_ms,
            label=mapping.label,
            note=note,
        )

    @staticmethod
    def mapping_table() -> list[dict[str, object]]:
        rows = []
        for mapping in MOTION_MAP.values():
            row = asdict(mapping)
            row["native_mode"] = "state WORKING" if mapping.motion == "idle" else "pulse"
            rows.append(row)
        return rows
