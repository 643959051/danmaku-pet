from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from danmaku_pet.__main__ import _browser_url
from danmaku_pet.client.startup import build_config, validate


def test_browser_url_uses_loopback_for_wildcard_host() -> None:
    config = SimpleNamespace(host="0.0.0.0", port=8787)
    assert _browser_url(config) == "http://127.0.0.1:8787/"  # type: ignore[arg-type]


def test_browser_url_keeps_explicit_host() -> None:
    config = SimpleNamespace(host="127.0.0.1", port=8788)
    assert _browser_url(config) == "http://127.0.0.1:8788/"  # type: ignore[arg-type]


def test_cli_overrides_room_and_transport(monkeypatch) -> None:
    monkeypatch.setenv("ROOM_ID", "")
    monkeypatch.setenv("CLIENT_TOKEN", "")
    config, _ = build_config(
        [
            "--room-id",
            "room-42",
            "--client-id",
            "pc-1",
            "--client-token",
            "secret-token",
            "--transport",
            "static",
            "--static-commands",
            __file__,
            "--no-desktop",
            "--no-browser",
        ]
    )
    assert config.room_id == "room-42"
    assert config.client_id == "pc-1"
    assert config.client_token == "secret-token"
    assert config.transport == "static"
    assert config.static_commands == Path(__file__)
    assert config.desktop_pet_enabled is False
    assert config.auto_open_browser is False
    assert validate(config) == []


def test_platform_launch_params_map_known_keys_and_keep_unknown() -> None:
    config, _ = build_config(
        [
            "--launch",
            "room_id=room-9",
            "--launch",
            "token=abc",
            "--launch",
            "plugin_version=1.2.3",
            "--cloud_url=ws://127.0.0.1:8000/ws/client",
        ]
    )
    assert config.room_id == "room-9"
    assert config.client_token == "abc"
    assert config.cloud_url == "ws://127.0.0.1:8000/ws/client"
    assert config.platform_params["plugin_version"] == "1.2.3"
    assert "plugin_version" in config.public_status()["platform_param_keys"]


def test_websocket_transport_requires_token_and_ws_url() -> None:
    config, _ = build_config(["--room-id", "r", "--transport", "websocket"])
    config.client_token = ""
    config.cloud_url = "http://example.invalid"
    problems = validate(config)
    assert any("cloud_url" in problem for problem in problems)
    assert any("CLIENT_TOKEN" in problem for problem in problems)


def test_static_transport_requires_an_existing_file() -> None:
    config, _ = build_config(["--transport", "static", "--room-id", "r"])
    missing = Path(__file__).resolve().parent / "no-such-commands.jsonl"
    config.static_commands = missing
    problems = validate(config)
    assert any("模拟指令文件不存在" in problem for problem in problems)


def test_platform_transport_is_selectable_but_explicit() -> None:
    config, _ = build_config(["--transport", "platform", "--room-id", "r"])
    assert config.transport == "platform"
    # No URL/token required for the platform channel; it fails loudly on connect.
    assert not any("CLIENT_TOKEN" in problem for problem in validate(config))


def test_config_does_not_expose_the_client_token() -> None:
    config, _ = build_config(["--client-token", "super-secret-token", "--room-id", "r"])
    status = config.public_status()
    assert "super-secret-token" not in str(status)
    assert status["cloud_token_configured"] is True
