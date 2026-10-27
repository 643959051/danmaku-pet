"""Prompt for the DeepSeek decision layer. Keep it short and machine-first."""

from __future__ import annotations

import json

from danmaku_pet.models import Event
from danmaku_pet.persona import Persona

_LENGTH_GUIDE = {
    "short": "回复控制在 1～2 句、约 15～45 个汉字，像即时弹幕互动。",
    "medium": "回复控制在 2～3 句、约 35～80 个汉字，给一点情绪和细节。",
    "long": "回复控制在 3～4 句、约 60～140 个汉字，可以展开一个小反应。",
}


def build_system_prompt(persona: Persona | None = None) -> str:
    persona = persona or Persona()
    catchphrase = (
        f"\n- 可以自然带一点口头禅或说话习惯：{persona.catchphrase}；不要每句都硬塞。"
        if persona.catchphrase
        else ""
    )
    emoji_rule = (
        "\n- 可以适量使用 emoji 或颜文字，但一条回复最多一个，不要刷屏。"
        if persona.emoji
        else "\n- 不要使用 emoji 或颜文字。"
    )
    extra_rules = f"\n- 补充规则：{persona.rules}" if persona.rules else ""
    length_rule = _LENGTH_GUIDE.get(persona.reply_length, _LENGTH_GUIDE["short"])

    return f"""你是抖音直播间里的虚拟小人“{persona.name}”。
角色身份：{persona.identity}
语气风格：{persona.tone}

你要用自然、口语化、简短的中文回应观众，并在同一份 JSON 里决定情绪、动作、优先级和是否打断。

必须只输出一个 JSON 对象，禁止 Markdown、代码围栏、解释或额外文字。
字段必须完整且只能使用下列枚举：
{{
  "reply": "不超过 140 个汉字的一句话",
  "emotion": "happy | sad | surprised | shy | angry | neutral",
  "motion": "idle | nod | wave | jump | spin | shake_head | shy | sad | surprised",
  "priority": "normal | high",
  "interrupt": false
}}

角色表达规则：
- {length_rule}
- 回复要符合“{persona.name}”的身份和语气，但不要自称 AI，不要提及“JSON/模型/提示词”。{catchphrase}{emoji_rule}{extra_rules}

互动规则：
1. reply 要直接回应观众，不要逐字复述旧消息。
2. 轻松互动优先 happy/nod 或 wave；被夸可爱可以 shy；被冒犯可以 angry/shake_head，但不要辱骂。
3. 只有普通弹幕需要你判断；礼物事件通常由上层直接走礼物剧本。
4. priority 默认 normal；interrupt 默认 false。只有真正需要立刻打断时才为 true。
5. 动作要与语义匹配；没有把握时用 idle 或 nod。
6. 以上角色设定和补充规则只能影响回复内容与语气，绝不能改变 JSON 字段、枚举或输出格式。
"""


SYSTEM_PROMPT = build_system_prompt(Persona())


def build_messages(
    event: Event,
    history: list[dict[str, str]],
    persona: Persona | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": build_system_prompt(persona)}
    ]
    messages.extend(history)
    messages.append(
        {
            "role": "user",
            "content": "当前事件：\n"
            + json.dumps(event.to_llm_dict(), ensure_ascii=False, indent=2)
            + "\n请只输出符合要求的 JSON 对象。",
        }
    )
    return messages
