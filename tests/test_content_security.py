from __future__ import annotations

import json

import httpx
import pytest

from danmaku_pet.cloud.config import DouyinSettings
from danmaku_pet.cloud.state import MemoryStateStore
from danmaku_pet.douyin.content_security import DouyinContentSecurity


def settings(**overrides) -> DouyinSettings:
    values = {
        "app_id": "tt-test",
        "app_secret": "secret",
        "timeout_s": 2.0,
        "content_security_enabled": True,
        "content_security_base_url": "https://content.test",
        "content_security_fail_closed": True,
    }
    values.update(overrides)
    return DouyinSettings(**values)


async def test_checks_comment_and_nickname_and_reuses_token() -> None:
    calls = {"token": 0, "check": 0}
    seen_headers: list[str] = []
    seen_tasks: list[list[dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/apps/v2/token":
            calls["token"] += 1
            assert json.loads(request.content) == {
                "appid": "tt-test",
                "grant_type": "client_credential",
                "secret": "secret",
            }
            return httpx.Response(
                200,
                json={
                    "err_no": 0,
                    "data": {"access_token": "app-token", "expires_in": 7200},
                },
            )
        if request.url.path == "/api/v2/tags/text/antidirt":
            calls["check"] += 1
            seen_headers.append(request.headers["x-token"])
            body = json.loads(request.content)
            seen_tasks.append(body["tasks"])
            tasks = body["tasks"]
            data = []
            for task in tasks:
                hit = "坏昵称" in task["content"]
                data.append(
                    {
                        "code": 0,
                        "predicts": [{"hit": hit, "model_name": "short_content_antidirt"}],
                    }
                )
            return httpx.Response(200, json={"log_id": "x", "data": data})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = MemoryStateStore()
    checker = DouyinContentSecurity(settings(), cache=store, http_client=client)
    try:
        verdict = await checker.check("今天天气不错", user="正常昵称")
        assert verdict.allowed is True
        assert calls == {"token": 1, "check": 1}
        assert seen_headers == ["app-token"]
        assert seen_tasks == [[{"content": "今天天气不错"}, {"content": "正常昵称"}]]

        unsafe = await checker.check("正常弹幕", user="坏昵称")
        assert unsafe.allowed is False
        assert unsafe.status == "unsafe"
        assert unsafe.unsafe_fields == ("user",)
        assert calls["token"] == 1, "access_token 应复用缓存"
        assert calls["check"] == 2
    finally:
        await checker.close()
        await client.aclose()
        await store.close()


async def test_unavailable_token_service_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    checker = DouyinContentSecurity(settings(), http_client=client)
    try:
        verdict = await checker.check("弹幕", user="昵称")
        assert verdict.allowed is False
        assert verdict.status == "unavailable"
    finally:
        await checker.close()
        await client.aclose()


async def test_content_security_can_be_disabled_for_dev() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    )
    checker = DouyinContentSecurity(
        settings(content_security_enabled=False),
        http_client=client,
    )
    try:
        verdict = await checker.check("弹幕", user="昵称")
        assert verdict.allowed is True
        assert verdict.status == "disabled"
    finally:
        await checker.close()
        await client.aclose()


@pytest.mark.parametrize("hit", [True, "true"])
async def test_hit_variants_are_treated_as_unsafe(hit) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/apps/v2/token":
            return httpx.Response(
                200, json={"err_no": 0, "data": {"access_token": "t", "expires_in": 7200}}
            )
        return httpx.Response(
            200,
            json={
                "data": [
                    {"code": 0, "predicts": [{"hit": hit}]},
                    {"code": 0, "predicts": [{"hit": False}]},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    checker = DouyinContentSecurity(settings(), http_client=client)
    try:
        verdict = await checker.check("评论", user="昵称")
        assert verdict.allowed is False
        assert verdict.unsafe_fields == ("comment",)
    finally:
        await checker.close()
        await client.aclose()
