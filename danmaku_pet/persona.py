"""Editable persona/tone settings for the virtual character."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

REPLY_LENGTHS = ("short", "medium", "long")
DEFAULT_IDENTITY = "直播间里的虚拟陪伴小人，会接梗、会撒娇，也会认真听观众说话"
DEFAULT_TONE = "温柔、活泼、自然，偶尔撒娇"


@dataclass(frozen=True, slots=True)
class Persona:
    name: str = "小鱼"
    identity: str = DEFAULT_IDENTITY
    tone: str = DEFAULT_TONE
    catchphrase: str = ""
    reply_length: str = "short"
    emoji: bool = False
    rules: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "Persona":
        value = value or {}

        def text(key: str, default: str, limit: int) -> str:
            raw = value.get(key, default)
            return str(raw if raw is not None else default).strip()[:limit]

        length = str(value.get("reply_length", "short")).strip().lower()
        if length not in REPLY_LENGTHS:
            length = "short"
        return cls(
            name=text("name", "小鱼", 20) or "小鱼",
            identity=text("identity", DEFAULT_IDENTITY, 200) or DEFAULT_IDENTITY,
            tone=text("tone", DEFAULT_TONE, 120) or DEFAULT_TONE,
            catchphrase=text("catchphrase", "", 80),
            reply_length=length,
            emoji=bool(value.get("emoji", False)),
            rules=text("rules", "", 600),
        )


DEFAULT_PERSONA = Persona()


def load_persona(path: Path) -> Persona:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_PERSONA
    if not isinstance(raw, dict):
        return DEFAULT_PERSONA
    payload = raw.get("persona", raw)
    if not isinstance(payload, dict):
        return DEFAULT_PERSONA
    return Persona.from_mapping(payload)


def save_persona(path: Path, persona: Persona) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "persona": persona.to_dict()}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
