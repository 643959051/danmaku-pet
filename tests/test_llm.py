from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import pytest

from danmaku_pet.decision.context import ConversationMemory
from danmaku_pet.decision.llm import (
    DeepSeekEngine,
    DecisionError,
    MockDecisionEngine,
    local_now,
    parse_json_object,
)
from danmaku_pet.models import Event


def make_config() -> SimpleNamespace:
    return SimpleNamespace(
        deepseek_api_key="test-key",
        deepseek_base_url="https://example.invalid",
        deepseek_model="deepseek-chat",
        llm_timeout_s=5.0,
    )


def make_event(text: str = "你好") -> Event:
    return Event(source="manual", user="A", text=text, timestamp=time.time())


@pytest.mark.asyncio
async def test_deepseek_parses_fenced_json_and_validates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '```json\n{"reply":"你好呀","emotion":"happy","motion":"wave","priority":"normal","interrupt":false}\n```'}}]},
        )

    engine = DeepSeekEngine(make_config(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await engine.decide(make_event(), ConversationMemory(10))
    assert result.plan.motion == "wave"
    assert result.source == "deepseek"
    await engine.close()


@pytest.mark.asyncio
async def test_deepseek_invalid_motion_uses_adapter_fallback() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"reply":"嗨","emotion":"happy","motion":"dance","priority":"normal"}'}}]},
        )

    engine = DeepSeekEngine(make_config(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await engine.decide(make_event(), ConversationMemory(10))
    assert result.plan.motion == "nod"
    assert result.warnings
    await engine.close()


@pytest.mark.asyncio
async def test_deepseek_retries_without_response_format() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            return httpx.Response(400, text="response_format unsupported")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"reply":"好","emotion":"neutral","motion":"nod","priority":"normal","interrupt":false}'}}]},
        )

    engine = DeepSeekEngine(make_config(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await engine.decide(make_event(), ConversationMemory(10))
    assert result.plan.reply == "好"
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]
    await engine.close()


@pytest.mark.asyncio
async def test_deepseek_http_error_raises_decision_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    engine = DeepSeekEngine(make_config(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(DecisionError):
        await engine.decide(make_event(), ConversationMemory(10))
    await engine.close()


@pytest.mark.asyncio
async def test_deepseek_timeout_raises_decision_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow model", request=request)

    engine = DeepSeekEngine(make_config(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(DecisionError):
        await engine.decide(make_event(), ConversationMemory(10))
    await engine.close()


@pytest.mark.asyncio
async def test_deepseek_invalid_json_raises_decision_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    engine = DeepSeekEngine(make_config(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(DecisionError):
        await engine.decide(make_event(), ConversationMemory(10))
    await engine.close()


def test_parse_json_rejects_markdown_without_object() -> None:
    with pytest.raises(DecisionError):
        parse_json_object("not json")


@pytest.mark.asyncio
async def test_mock_engine_is_deterministic() -> None:
    result = await MockDecisionEngine().decide(make_event("转个圈"), ConversationMemory(10))
    assert result.plan.motion == "spin"
    assert result.source == "mock"


@pytest.mark.asyncio
async def test_mock_engine_answers_time_questions() -> None:
    """本地模拟也要报时间，而不是套用“收到弹幕”的兜底模板。"""

    result = await MockDecisionEngine().decide(
        make_event("小鱼，现在几点了"), ConversationMemory(10)
    )
    assert result.plan.reply.startswith("现在是 ")
    assert "周" in result.plan.reply
    assert result.plan.motion == "nod"
    assert result.source == "mock"


def test_local_now_uses_the_configured_timezone() -> None:
    assert local_now("UTC").utcoffset().total_seconds() == 0
    assert local_now("Asia/Shanghai").utcoffset().total_seconds() == 8 * 3600
    # Unknown names fall back to the process timezone instead of crashing.
    assert local_now("Mars/Olympus").tzinfo is not None
