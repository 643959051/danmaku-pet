from __future__ import annotations

from danmaku_pet.decision.context import ConversationMemory


def test_memory_keeps_only_ten_rounds() -> None:
    memory = ConversationMemory(10)
    for index in range(12):
        memory.add(f"u{index}", f"a{index}")
    messages = memory.messages()
    assert len(messages) == 20
    assert messages[0]["content"] == "u2"
    assert messages[-1]["content"] == "a11"
    assert len(memory) == 10


def test_memory_skips_empty() -> None:
    memory = ConversationMemory(10)
    memory.add("", "a")
    memory.add("u", "")
    assert len(memory) == 0


def test_clear() -> None:
    memory = ConversationMemory(3)
    memory.add("u", "a")
    memory.clear()
    assert memory.messages() == []
