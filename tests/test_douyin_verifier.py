from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace

import pytest

from danmaku_pet.douyin.verifier import MessageVerifier, VerificationError


def settings(**overrides):
    base = dict(
        verify_mode="hmac",
        verify_token="token-1",
        verify_secret="secret-1",
        token_header="x-douyin-token",
        signature_header="x-douyin-signature",
        timestamp_header="x-douyin-timestamp",
        timestamp_tolerance_s=300,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()


def test_disabled_mode_accepts_everything() -> None:
    MessageVerifier(settings(verify_mode="disabled")).verify({}, b"{}")


def test_header_mode_accepts_the_shared_token() -> None:
    MessageVerifier(settings(verify_mode="header")).verify(
        {"X-Douyin-Token": "token-1"}, b"{}"
    )


def test_header_mode_rejects_wrong_or_missing_token() -> None:
    verifier = MessageVerifier(settings(verify_mode="header"))
    with pytest.raises(VerificationError) as exc:
        verifier.verify({"x-douyin-token": "nope"}, b"{}")
    assert exc.value.code == "bad_token"
    with pytest.raises(VerificationError):
        verifier.verify({}, b"{}")


def test_header_mode_requires_a_configured_secret() -> None:
    with pytest.raises(VerificationError) as exc:
        MessageVerifier(settings(verify_mode="header", verify_token="")).verify(
            {"x-douyin-token": "x"}, b"{}"
        )
    assert exc.value.code == "missing_secret"


def test_hmac_mode_accepts_a_valid_signature() -> None:
    body = b'{"room_id":"r"}'
    timestamp = "1710000000"
    MessageVerifier(settings()).verify(
        {
            "x-douyin-signature": sign("secret-1", timestamp, body),
            "x-douyin-timestamp": timestamp,
        },
        body,
        now=1710000000,
    )


def test_hmac_mode_rejects_bad_signature() -> None:
    with pytest.raises(VerificationError) as exc:
        MessageVerifier(settings()).verify(
            {"x-douyin-signature": "deadbeef", "x-douyin-timestamp": "1710000000"},
            b"{}",
            now=1710000000,
        )
    assert exc.value.code == "bad_signature"


def test_hmac_mode_rejects_stale_timestamp() -> None:
    body = b"{}"
    timestamp = "1710000000"
    with pytest.raises(VerificationError) as exc:
        MessageVerifier(settings()).verify(
            {
                "x-douyin-signature": sign("secret-1", timestamp, body),
                "x-douyin-timestamp": timestamp,
            },
            body,
            now=1710000000 + 1000,
        )
    assert exc.value.code == "stale_signature"


def test_hmac_mode_without_timestamp_header_signs_the_body() -> None:
    body = b'{"a":1}'
    signature = hmac.new(b"secret-1", body, hashlib.sha256).hexdigest()
    MessageVerifier(settings()).verify({"x-douyin-signature": signature}, body)


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(VerificationError):
        MessageVerifier(settings(verify_mode="magic")).verify({}, b"{}")
