"""Deterministic, high-priority gift performances.

Gift events deliberately bypass free-form LLM generation: latency is lower and
the result stays on-brand. The mapping is data, so adding a gift is one tuple.

Gift matching prefers the platform's stable ``gift_id``.  Because the final
Douyin gift-id table is only available after the platform permission is
confirmed, the id → name table is injected from configuration
(``GIFT_ID_MAP``); ``gift_name`` remains the display text and the fallback key
for old scripts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from danmaku_pet.shared.events import Event
from danmaku_pet.shared.plans import ActionPlan, DecisionResult, Emotion, Motion


@dataclass(frozen=True, slots=True)
class GiftRule:
    canonical: str
    aliases: tuple[str, ...]
    reply_template: str
    emotion: Emotion
    motion: Motion
    min_hold_ms: int


RULES: tuple[GiftRule, ...] = (
    GiftRule(
        canonical="嘉年华",
        aliases=("嘉年华", "梦幻城堡"),
        reply_template="哇！谢谢{user}的{gift}！这波必须原地转个圈！",
        emotion="surprised",
        motion="spin",
        min_hold_ms=3200,
    ),
    GiftRule(
        canonical="火箭",
        aliases=("火箭", "超级火箭"),
        reply_template="火箭升空！谢谢{user}，我直接跳起来啦！",
        emotion="surprised",
        motion="jump",
        min_hold_ms=2800,
    ),
    GiftRule(
        canonical="跑车",
        aliases=("跑车", "浪漫跑车", "越野车"),
        reply_template="谢谢{user}的{gift}！小人开心到转圈！",
        emotion="happy",
        motion="spin",
        min_hold_ms=3000,
    ),
    GiftRule(
        canonical="玫瑰",
        aliases=("玫瑰", "小心心", "爱心", "鲜花"),
        reply_template="收到{user}的{gift}啦，送你一个害羞的飞吻！",
        emotion="shy",
        motion="shy",
        min_hold_ms=2600,
    ),
    GiftRule(
        canonical="棒棒糖",
        aliases=("棒棒糖", "糖果", "糖"),
        reply_template="谢谢{user}的{gift}，甜到心里啦！",
        emotion="happy",
        motion="wave",
        min_hold_ms=2400,
    ),
    GiftRule(
        canonical="啤酒",
        aliases=("啤酒", "干杯"),
        reply_template="干杯！谢谢{user}的{gift}，小人敬你一个！",
        emotion="happy",
        motion="nod",
        min_hold_ms=2200,
    ),
    GiftRule(
        canonical="墨镜",
        aliases=("墨镜", "眼镜"),
        reply_template="哇，{user}送的{gift}太酷了！",
        emotion="surprised",
        motion="surprised",
        min_hold_ms=2200,
    ),
)

FALLBACK_RULE = GiftRule(
    canonical="礼物",
    aliases=(),
    reply_template="谢谢{user}的{gift}！小鱼收到啦！",
    emotion="happy",
    motion="wave",
    min_hold_ms=2200,
)


def resolve_gift_name(
    gift_name: str | None,
    gift_id: str | None = None,
    *,
    gift_ids: Mapping[str, str] | None = None,
) -> str:
    """Prefer the configured gift-id mapping, then the display name."""

    if gift_id and gift_ids:
        mapped = gift_ids.get(str(gift_id).strip())
        if mapped:
            return str(mapped).strip()
    return (gift_name or "").strip()


def choose_rule(
    gift_name: str | None = None,
    gift_id: str | None = None,
    *,
    gift_ids: Mapping[str, str] | None = None,
) -> GiftRule:
    name = resolve_gift_name(gift_name, gift_id, gift_ids=gift_ids)
    if not name:
        return FALLBACK_RULE
    for rule in RULES:
        if any(alias in name or name in alias for alias in rule.aliases):
            return rule
    return FALLBACK_RULE


def decide_gift(
    event: Event,
    *,
    gift_ids: Mapping[str, str] | None = None,
) -> DecisionResult:
    rule = choose_rule(event.gift_name, event.gift_id, gift_ids=gift_ids)
    gift = event.gift_name or rule.canonical
    count = max(1, event.gift_count)
    gift_label = f"{gift} × {count}" if count > 1 else gift
    reply = rule.reply_template.format(user=event.user, gift=gift_label, count=count)
    plan = ActionPlan(
        reply=reply[:240],
        emotion=rule.emotion,
        motion=rule.motion,
        priority="high",
        interrupt=True,
        min_hold_ms=rule.min_hold_ms,
    )
    return DecisionResult(
        plan=plan,
        source="gift_script",
        raw={
            "canonical": rule.canonical,
            "matched_gift": gift,
            "gift_id": event.gift_id,
            "count": count,
            "min_hold_ms": rule.min_hold_ms,
        },
        latency_ms=0,
        model="gift-script",
        warnings=(),
    )


def gift_table() -> list[dict[str, object]]:
    return [
        {
            "canonical": rule.canonical,
            "aliases": list(rule.aliases),
            "emotion": rule.emotion,
            "motion": rule.motion,
            "min_hold_ms": rule.min_hold_ms,
        }
        for rule in (*RULES, FALLBACK_RULE)
    ]
