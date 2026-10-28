"""Inbound message verification.

The platform can require a shared token header, an HMAC signature over the raw
body, or (during early integration) nothing at all.  ``disabled`` is refused in
the production environment by :class:`CloudConfig`.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping
from typing import Protocol


class VerificationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class VerifierSettingsLike(Protocol):
    verify_mode: str
    verify_token: str
    verify_secret: str
    timestamp_tolerance_s: int
    token_header: str
    signature_header: str
    timestamp_header: str


def _header(headers: Mapping[str, str], name: str) -> str:
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    return lowered.get(name.lower(), "")


class MessageVerifier:
    def __init__(self, settings: VerifierSettingsLike) -> None:
        self.settings = settings

    @property
    def mode(self) -> str:
        return (self.settings.verify_mode or "hmac").lower()

    def verify(
        self,
        headers: Mapping[str, str],
        body: bytes,
        *,
        now: float | None = None,
    ) -> None:
        if self.mode == "disabled":
            return
        if self.mode == "header":
            self._verify_token(headers)
            return
        if self.mode == "hmac":
            self._verify_hmac(headers, body, now=now)
            return
        raise VerificationError("bad_mode", f"未知的校验模式：{self.mode}")

    # ------------------------------------------------------------------ modes
    def _verify_token(self, headers: Mapping[str, str]) -> None:
        expected = self.settings.verify_token
        if not expected:
            raise VerificationError(
                "missing_secret", "DOUYIN_VERIFY_TOKEN 未配置，无法校验平台消息"
            )
        supplied = _header(headers, self.settings.token_header)
        if not supplied or not hmac.compare_digest(supplied.strip(), expected):
            raise VerificationError("bad_token", "平台消息令牌校验失败")

    def _verify_hmac(
        self,
        headers: Mapping[str, str],
        body: bytes,
        *,
        now: float | None = None,
    ) -> None:
        secret = self.settings.verify_secret
        if not secret:
            raise VerificationError(
                "missing_secret", "DOUYIN_VERIFY_SECRET 未配置，无法校验平台签名"
            )
        signature = _header(headers, self.settings.signature_header).strip().lower()
        if not signature:
            raise VerificationError("missing_signature", "缺少平台签名头")
        timestamp = _header(headers, self.settings.timestamp_header).strip()
        if timestamp:
            try:
                sent_at = float(timestamp)
            except ValueError as exc:
                raise VerificationError("bad_timestamp", "平台时间戳不是数字") from exc
            if sent_at > 10_000_000_000:  # milliseconds
                sent_at /= 1000.0
            current = now if now is not None else time.time()
            tolerance = max(30, int(self.settings.timestamp_tolerance_s))
            if abs(current - sent_at) > tolerance:
                raise VerificationError("stale_signature", "平台签名时间戳超出允许范围")
            signed = timestamp.encode("utf-8") + b"." + body
        else:
            signed = body
        expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise VerificationError("bad_signature", "平台签名校验失败")
