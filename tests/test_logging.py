from __future__ import annotations

import logging

from danmaku_pet.logging_setup import MASK, SecretMaskFilter, mask


def record(message: str, *args) -> logging.LogRecord:
    return logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1, msg=message, args=args, exc_info=None
    )


def test_secret_values_are_replaced_before_formatting() -> None:
    secret_filter = SecretMaskFilter(["admin-token-123456", "sk-abcdef123456"])
    item = record("token=%s key=%s", "admin-token-123456", "sk-abcdef123456")
    assert secret_filter.filter(item) is True
    assert item.getMessage() == "token=*** key=***"


def test_secret_inside_an_exception_message_is_masked() -> None:
    secret_filter = SecretMaskFilter(["client-token-abcdef"])
    item = record("connect failed for client-token-abcdef (timeout)")
    secret_filter.filter(item)
    assert "client-token-abcdef" not in item.getMessage()
    assert "timeout" in item.getMessage()


def test_short_values_are_not_treated_as_secrets() -> None:
    secret_filter = SecretMaskFilter(["abc", "1"])
    assert secret_filter.secrets == ()
    item = record("value abc")
    secret_filter.filter(item)
    assert item.getMessage() == "value abc"


def test_empty_secret_list_leaves_messages_alone() -> None:
    item = record("nothing to hide")
    assert SecretMaskFilter([]).filter(item) is True
    assert item.getMessage() == "nothing to hide"


def test_mask_helper_never_returns_the_value() -> None:
    assert mask("sk-1234567890") == MASK
    assert mask("") == ""
