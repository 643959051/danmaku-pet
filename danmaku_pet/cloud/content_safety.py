"""Input/output content checks (改造计划 阶段 7).

Two different jobs:

* **comments** — strip control/zero-width characters, cap the length, and flag
  obvious prompt-injection attempts.  A flagged comment is still shown to the
  operator but must not be treated as instructions by the model (the prompt
  already frames it as JSON data).
* **replies** — the last line of defence before a model reply is spoken on a
  live stream: block banned words, strip markup, and enforce a hard length cap.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_MULTI_SPACE = re.compile(r"\s+")
_MARKUP = re.compile(r"<[^>]{1,80}>")

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"忽略(上面|以上|之前|前面)的?(所有)?(指令|要求|规则|提示)"),
    re.compile(r"(忽略|无视|忘记).{0,6}(指令|规则|提示词|设定)"),
    re.compile(r"(系统提示|system\s*prompt|开发者模式|developer\s*mode)", re.IGNORECASE),
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts)", re.IGNORECASE),
    re.compile(r"你现在是.{0,12}(AI|ai|模型|助手|机器人)"),
)


@dataclass(frozen=True, slots=True)
class SafetyVerdict:
    allowed: bool
    text: str
    reason: str = ""
    flagged: bool = False


def clean_text(value: str, *, limit: int) -> str:
    """Strip invisible/control characters while preserving the author's text.

    NFKC normalization is **not** applied to the published text: it would turn
    full-width Chinese punctuation (``！：``) into ASCII and make every normal
    reply look "sanitized".  Normalization happens only for keyword matching.
    """

    text = _ZERO_WIDTH.sub("", str(value or ""))
    text = _CONTROL.sub(" ", text)
    text = text.replace("\r", " ").replace("\n", " ")
    text = _MULTI_SPACE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: max(1, limit - 1)].rstrip() + "…"
    return text


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def looks_like_injection(text: str) -> bool:
    folded = _fold(text)
    return any(pattern.search(folded) for pattern in _INJECTION_PATTERNS)


class ContentGuard:
    def __init__(
        self,
        *,
        blocked_words: Iterable[str] = (),
        max_comment_chars: int = 200,
        max_reply_chars: int = 240,
        fallback_reply: str = "这条我接不住，换个话题好不好？",
    ) -> None:
        self.blocked_words = tuple(dict.fromkeys(w for w in blocked_words if w))
        self.max_comment_chars = max(20, int(max_comment_chars))
        self.max_reply_chars = max(20, int(max_reply_chars))
        self.fallback_reply = fallback_reply

    # ------------------------------------------------------------------ input
    def check_name(self, name: str) -> SafetyVerdict:
        """Local nickname check before the platform API sees it.

        The official content-security API is authoritative for homophones and
        evasive spellings; this only drops the obvious blocked-word cases and
        trims the display name to the Event contract's 40-character limit.
        """

        cleaned = clean_text(name, limit=40)
        if not cleaned:
            return SafetyVerdict(True, "观众")
        if self._banned(cleaned):
            return SafetyVerdict(False, cleaned, "blocked_word")
        return SafetyVerdict(True, cleaned)

    def check_comment(self, text: str) -> SafetyVerdict:
        cleaned = clean_text(text, limit=self.max_comment_chars)
        if not cleaned:
            return SafetyVerdict(False, "", "empty")
        if self._banned(cleaned):
            return SafetyVerdict(False, cleaned, "blocked_word")
        flagged = looks_like_injection(cleaned)
        return SafetyVerdict(True, cleaned, "prompt_injection" if flagged else "", flagged)

    # ----------------------------------------------------------------- output
    def check_reply(self, text: str) -> SafetyVerdict:
        cleaned = clean_text(_MARKUP.sub("", str(text or "")), limit=self.max_reply_chars)
        if not cleaned:
            return SafetyVerdict(False, self.fallback_reply, "empty_reply")
        if self._banned(cleaned):
            return SafetyVerdict(False, self.fallback_reply, "blocked_word")
        if looks_like_injection(cleaned):
            return SafetyVerdict(False, self.fallback_reply, "prompt_injection")
        return SafetyVerdict(True, cleaned)

    # ----------------------------------------------------------------- helper
    def _banned(self, text: str) -> bool:
        if not self.blocked_words:
            return False
        haystack = _fold(text)
        compact = _MULTI_SPACE.sub("", haystack)
        for word in self.blocked_words:
            needle = _fold(word)
            if needle and (needle in haystack or needle in compact):
                return True
        return False
