"""Bounded dialogue memory."""

from __future__ import annotations

from collections import deque


class ConversationMemory:
    """Keep the last N completed exchanges (N user + N assistant messages)."""

    def __init__(self, max_rounds: int = 10) -> None:
        self.max_rounds = max(1, max_rounds)
        self._rounds: deque[tuple[str, str]] = deque(maxlen=self.max_rounds)

    def add(self, user_text: str, reply: str) -> None:
        user_text = (user_text or "").strip()[:300]
        reply = (reply or "").strip()[:300]
        if user_text and reply:
            self._rounds.append((user_text, reply))

    def messages(self) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for user_text, reply in self._rounds:
            messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": reply})
        return messages

    def snapshot(self) -> list[dict[str, str]]:
        return [
            {"user": user_text, "assistant": reply}
            for user_text, reply in self._rounds
        ]

    def clear(self) -> None:
        self._rounds.clear()

    def __len__(self) -> int:
        return len(self._rounds)
