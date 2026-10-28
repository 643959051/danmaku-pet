"""Wire protocol: version negotiation, JSON envelopes and idempotency helpers.

The transport itself is intentionally not defined here.  Formal delivery uses
the 抖音云 gateway, while the self-hosted WebSocket is a development / backup
channel; both carry the same envelope, so the message layer is shared.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from danmaku_pet.shared.events import Event

PROTOCOL_VERSION = 1
MIN_SUPPORTED_PROTOCOL = 1
MAX_SUPPORTED_PROTOCOL = 1

# Message kinds carried by the envelope.
KIND_HELLO = "hello"
KIND_WELCOME = "welcome"
KIND_HEARTBEAT = "heartbeat"
KIND_PONG = "pong"
KIND_COMMAND = "command"
KIND_RESULT = "result"
KIND_INTERRUPT = "interrupt"
KIND_NOTICE = "notice"
KIND_ERROR = "error"
KINDS: tuple[str, ...] = (
    KIND_HELLO,
    KIND_WELCOME,
    KIND_HEARTBEAT,
    KIND_PONG,
    KIND_COMMAND,
    KIND_RESULT,
    KIND_INTERRUPT,
    KIND_NOTICE,
    KIND_ERROR,
)


class ProtocolError(ValueError):
    """A frame is malformed or carries an unknown message kind."""


class VersionMismatch(ProtocolError):
    """The peer speaks a protocol version this build cannot handle."""


def check_protocol_version(
    version: Any,
    *,
    minimum: int = MIN_SUPPORTED_PROTOCOL,
    maximum: int = MAX_SUPPORTED_PROTOCOL,
) -> int:
    """Return a usable version number or raise :class:`VersionMismatch`."""

    try:
        value = int(version)
    except (TypeError, ValueError) as exc:
        raise VersionMismatch(f"协议版本不是整数：{version!r}") from exc
    if value < minimum or value > maximum:
        raise VersionMismatch(
            f"协议版本不兼容：对端 v{value}，本端支持 v{minimum}~v{maximum}；"
            "请同时升级云端与本地客户端"
        )
    return value


@dataclass(frozen=True, slots=True)
class Envelope:
    kind: str
    payload: dict[str, Any]
    protocol_version: int = PROTOCOL_VERSION
    sent_at: float = field(default_factory=time.time)
    message_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "protocol_version": self.protocol_version,
            "sent_at": self.sent_at,
            "message_id": self.message_id,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def encode_message(
    kind: str,
    payload: Mapping[str, Any] | None = None,
    *,
    version: int = PROTOCOL_VERSION,
    message_id: str = "",
    sent_at: float | None = None,
) -> str:
    """Serialize one envelope as a single JSON line."""

    if kind not in KINDS:
        raise ProtocolError(f"unknown message kind: {kind!r}")
    return Envelope(
        kind=kind,
        payload=dict(payload or {}),
        protocol_version=int(version),
        sent_at=time.time() if sent_at is None else float(sent_at),
        message_id=message_id,
    ).to_json()


def decode_message(raw: str | bytes | Mapping[str, Any]) -> Envelope:
    """Parse one envelope and validate its shape and version."""

    if isinstance(raw, (str, bytes, bytearray)):
        text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw
        text = text.strip()
        if not text:
            raise ProtocolError("空消息")
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"消息不是合法 JSON：{exc}") from exc
    else:
        value = raw
    if not isinstance(value, Mapping):
        raise ProtocolError("消息顶层必须是 JSON 对象")

    kind = str(value.get("kind") or "").strip()
    if kind not in KINDS:
        raise ProtocolError(f"未知消息类型：{kind!r}")
    version = check_protocol_version(value.get("protocol_version", 0))
    payload = value.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise ProtocolError("payload 必须是 JSON 对象")
    try:
        sent_at = float(value.get("sent_at") or 0) or time.time()
    except (TypeError, ValueError) as exc:
        raise ProtocolError("sent_at 必须是时间戳") from exc
    return Envelope(
        kind=kind,
        payload=dict(payload),
        protocol_version=version,
        sent_at=sent_at,
        message_id=str(value.get("message_id") or ""),
    )


def make_command_id(room_id: str, event: Event) -> str:
    """Deterministic command id.

    Platform retries produce a fresh ``event_id`` but keep ``message_id``, so
    hashing the stable fields means the client sees the same ``command_id`` and
    its dedup cache refuses to perform the message twice.
    """

    key = event.message_id or event.event_id
    digest = hashlib.sha1(
        f"{room_id}\x00{event.message_type}\x00{key}".encode("utf-8")
    ).hexdigest()[:20]
    return f"cmd_{digest}"


class DedupCache:
    """Bounded, TTL-based duplicate detector.

    Used for ``message_id`` (cloud ingress) and ``command_id`` (client).  The
    clock is injectable so tests do not have to sleep.
    """

    def __init__(
        self,
        *,
        ttl_s: float = 600.0,
        maxsize: int = 4096,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.ttl_s = max(0.0, float(ttl_s))
        self.maxsize = max(1, int(maxsize))
        self._clock = clock
        self._seen: dict[str, float] = {}

    def check_and_add(self, key: str, *, now: float | None = None) -> bool:
        """Return ``True`` when ``key`` is new; remember it in either case."""

        cleaned = str(key or "").strip()
        if not cleaned:
            return False
        current = self._clock() if now is None else float(now)
        self._prune(current)
        if cleaned in self._seen:
            return False
        self._seen[cleaned] = current
        while len(self._seen) > self.maxsize:
            self._seen.pop(next(iter(self._seen)), None)
        return True

    def seen(self, key: str, *, now: float | None = None) -> bool:
        cleaned = str(key or "").strip()
        if not cleaned:
            return False
        current = self._clock() if now is None else float(now)
        self._prune(current)
        return cleaned in self._seen

    def _prune(self, now: float) -> None:
        if not self._seen:
            return
        cutoff = now - self.ttl_s
        stale = [key for key, stamp in self._seen.items() if stamp < cutoff]
        for key in stale:
            self._seen.pop(key, None)

    def clear(self) -> None:
        self._seen.clear()

    def __len__(self) -> int:
        return len(self._seen)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self._seen
