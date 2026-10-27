from __future__ import annotations

import time
from pathlib import Path

from danmaku_pet.decision.prompts import build_system_prompt
from danmaku_pet.persona import DEFAULT_PERSONA, Persona, load_persona, save_persona


def test_persona_defaults_are_safe() -> None:
    persona = Persona.from_mapping({})
    assert persona.name == "小鱼"
    assert persona.reply_length == "short"
    assert persona.emoji is False


def test_persona_sanitizes_invalid_values() -> None:
    persona = Persona.from_mapping(
        {
            "name": "  阿鱼  ",
            "reply_length": "huge",
            "emoji": True,
            "tone": "t" * 500,
        }
    )
    assert persona.name == "阿鱼"
    assert persona.reply_length == "short"
    assert persona.emoji is True
    assert len(persona.tone) <= 120


def test_persona_save_load_roundtrip() -> None:
    directory = Path(__file__).resolve().parent.parent / "temp" / "test-artifacts" / f"persona-{time.time_ns()}"
    path = directory / "persona.json"
    persona = Persona(
        name="小盐",
        identity="盐系虚拟主播",
        tone="冷静、简短、偶尔吐槽",
        catchphrase="就这？",
        reply_length="medium",
        emoji=False,
        rules="不要用网络烂梗",
    )
    save_persona(path, persona)
    loaded = load_persona(path)
    assert loaded == persona


def test_prompt_contains_persona_and_strict_contract() -> None:
    persona = Persona(
        name="小盐",
        identity="盐系虚拟主播",
        tone="冷静、简短",
        catchphrase="就这？",
        reply_length="medium",
        emoji=False,
        rules="不要用网络烂梗",
    )
    prompt = build_system_prompt(persona)
    assert "小盐" in prompt
    assert "冷静、简短" in prompt
    assert "就这？" in prompt
    assert "不要用网络烂梗" in prompt
    assert '"motion"' in prompt
    assert "spin" in prompt
    assert "不要使用 emoji" in prompt


def test_default_prompt_has_no_extra_emoji_instruction() -> None:
    prompt = build_system_prompt(DEFAULT_PERSONA)
    assert "小鱼" in prompt
    assert "不要使用 emoji" in prompt
