from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from danmaku_pet.douyin.openapi import (
    DouyinAPIError,
    DouyinOpenAPI,
    PlatformNotConfigured,
)
from danmaku_pet.shared.commands import PerformCommand
from tests.support import make_command


def settings(**overrides):
    base = dict(
        base_url="https://api.example.invalid",
        app_id="app",
        app_secret="secret",
        timeout_s=2.0,
        retries=1,
        token_path="/oauth/token",
        task_start_path="/task/start",
        task_stop_path="/task/stop",
        task_status_path="/task/status",
        command_push_path="/gateway/command",
        interrupt_path="/gateway/interrupt",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def client_with(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_missing_base_url_raises_platform_not_configured() -> None:
    api = DouyinOpenAPI(settings(base_url=""), http_client=client_with(lambda r: httpx.Response(200, json={})))
    with pytest.raises(PlatformNotConfigured):
        await api.start_task("room", "comment")
    await api.close()


async def test_missing_path_raises_platform_not_configured() -> None:
    api = DouyinOpenAPI(
        settings(command_push_path=""), http_client=client_with(lambda r: httpx.Response(200, json={}))
    )
    with pytest.raises(PlatformNotConfigured):
        await api.send_perform_command(make_command())
    await api.close()


async def test_start_task_uses_token_and_configured_payload() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/oauth/token":
            assert json.loads(request.content) == {"app_id": "app", "app_secret": "secret"}
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, json={"task_id": "task-1"})

    api = DouyinOpenAPI(settings(), http_client=client_with(handler))
    result = await api.start_task("room-1", "comment")
    assert result["task_id"] == "task-1"
    payload = json.loads(calls[-1].content)
    assert payload == {"room_id": "room-1", "task_type": "comment"}
    await api.close()


async def test_send_perform_command_payload() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "tok"})
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"code": 0})

    api = DouyinOpenAPI(settings(), http_client=client_with(handler))
    command = make_command()
    await api.send_perform_command(command)
    assert captured["room_id"] == command.room_id
    assert captured["command_id"] == command.command_id
    assert captured["command"]["plan"]["reply"] == command.plan.reply
    await api.close()


async def test_retries_a_transient_500() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "tok"})
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"task_id": "t"})

    async def no_sleep(_: float) -> None:
        return None

    api = DouyinOpenAPI(settings(retries=2), http_client=client_with(handler), sleep=no_sleep)
    result = await api.start_task("room", "gift")
    assert result["task_id"] == "t"
    assert attempts["count"] == 2
    await api.close()


async def test_non_retryable_client_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    api = DouyinOpenAPI(settings(retries=0), http_client=client_with(handler))
    with pytest.raises(DouyinAPIError):
        await api.start_task("room", "comment")
    await api.close()


async def test_business_error_inside_http_200_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 10086, "message": "no permission"})

    api = DouyinOpenAPI(settings(retries=0), http_client=client_with(handler))
    with pytest.raises(DouyinAPIError) as exc:
        await api.start_task("room", "comment")
    assert "10086" in str(exc.value)
    await api.close()


async def test_send_interrupt_uses_configured_path() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "tok"})
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={})

    api = DouyinOpenAPI(settings(), http_client=client_with(handler))
    await api.send_interrupt("room-1", "主播停止")
    assert captured == {"room_id": "room-1", "reason": "主播停止"}
    await api.close()
