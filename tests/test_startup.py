from __future__ import annotations

from types import SimpleNamespace

from danmaku_pet.__main__ import _browser_url


def test_browser_url_uses_loopback_for_wildcard_host() -> None:
    config = SimpleNamespace(host="0.0.0.0", port=8787)
    assert _browser_url(config) == "http://127.0.0.1:8787/"  # type: ignore[arg-type]


def test_browser_url_keeps_explicit_host() -> None:
    config = SimpleNamespace(host="127.0.0.1", port=8788)
    assert _browser_url(config) == "http://127.0.0.1:8788/"  # type: ignore[arg-type]
