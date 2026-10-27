"""DeepSeek JSON decision client plus a deterministic local fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Protocol

import httpx

from danmaku_pet.config import Config
from danmaku_pet.decision.context import ConversationMemory
from danmaku_pet.decision.prompts import build_messages
from danmaku_pet.decision.validator import normalize_plan
from danmaku_pet.models import ActionPlan, DecisionResult, Event
from danmaku_pet.persona import Persona

logger = logging.getLogger(__name__)


class DecisionError(RuntimeError):
    """The model call or JSON parse failed."""


class DecisionEngine(Protocol):
    name: str

    async def decide(self, event: Event, memory: ConversationMemory) -> DecisionResult: ...

    async def close(self) -> None: ...


def _extract_content(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DecisionError("DeepSeek 返回结构缺少 choices[0].message.content") from exc
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in content
        )
    if not isinstance(content, str) or not content.strip():
        raise DecisionError("DeepSeek 返回了空内容")
    return content.strip()


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise DecisionError("模型输出中找不到 JSON 对象")
    try:
        value = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise DecisionError(f"模型输出不是合法 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise DecisionError("模型输出的 JSON 顶层不是对象")
    return value


class DeepSeekEngine:
    name = "deepseek"

    def __init__(
        self,
        config: Config,
        *,
        http_client: httpx.AsyncClient | None = None,
        persona: Persona | None = None,
    ) -> None:
        if not config.deepseek_api_key:
            raise DecisionError("LLM_MODE=deepseek 但未配置 DEEPSEEK_API_KEY")
        self.config = config
        self.persona = persona or getattr(config, "persona", Persona())
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.llm_timeout_s, connect=5.0),
            headers={
                "Authorization": f"Bearer {config.deepseek_api_key}",
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.config.deepseek_base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.config.deepseek_api_key}"}
        started = time.perf_counter()
        response = await self._client.post(url, json=payload, headers=headers)
        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code == 400 and "response_format" in payload:
            # Some compatible gateways reject json_object mode; retry once
            # without it and rely on the strict prompt + parser.
            logger.warning("DeepSeek 拒绝了 response_format，去掉该字段重试一次")
            retry_payload = dict(payload)
            retry_payload.pop("response_format", None)
            response = await self._client.post(url, json=retry_payload, headers=headers)
            latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            body = response.text[:400].replace("\n", " ")
            raise DecisionError(f"DeepSeek HTTP {response.status_code}: {body}")
        try:
            return response.json()
        except ValueError as exc:
            raise DecisionError("DeepSeek 返回的不是 JSON 响应") from exc
        finally:
            logger.debug("DeepSeek 请求耗时 %s ms", latency_ms)

    async def decide(self, event: Event, memory: ConversationMemory) -> DecisionResult:
        payload = {
            "model": self.config.deepseek_model,
            "messages": build_messages(event, memory.messages(), self.persona),
            "temperature": 0.85,
            "max_tokens": 220,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        started = time.perf_counter()
        try:
            response = await self._post(payload)
            raw = parse_json_object(_extract_content(response))
            plan, warnings = normalize_plan(raw, event)
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            raise DecisionError(f"DeepSeek 网络错误: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)
        return DecisionResult(
            plan=plan,
            source="deepseek",
            raw=raw,
            latency_ms=latency_ms,
            model=self.config.deepseek_model,
            warnings=tuple(warnings),
        )


class MockDecisionEngine:
    """Local deterministic fallback so the UI is usable without an API key.

    It is explicitly labelled ``mock`` in the UI; it is not presented as a real
    DeepSeek response. Persona affects only the default reply's self-name.
    """

    name = "mock"

    def __init__(self, persona: Persona | None = None) -> None:
        self.persona = persona or Persona()

    _RULES: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
        (("转", "圈", "旋转"), "惊不惊喜！看我的转圈！", "surprised", "spin"),
        (("跳", "蹦"), "好嘞，给你蹦一个！", "happy", "jump"),
        (("挥手", "你好", "嗨", "hello", "hi"), "嗨，我是小鱼，看到你啦！", "happy", "wave"),
        (("难过", "伤心", "哭", "委屈"), "抱抱你，不开心的事先放一放。", "sad", "sad"),
        (("害羞", "可爱", "喜欢你"), "别、别这么夸我啦……", "shy", "shy"),
        (("生气", "讨厌", "走开"), "哼，我听见了，但我不往心里去。", "angry", "shake_head"),
        (("哇", "惊", "没想到"), "哇！真的假的！", "surprised", "surprised"),
        (("点头", "好的", "对", "嗯"), "收到，小鱼点头同意！", "happy", "nod"),
    )

    async def decide(self, event: Event, memory: ConversationMemory) -> DecisionResult:
        await asyncio.sleep(0.05)
        text = event.text.lower()
        for keywords, reply, emotion, motion in self._RULES:
            if any(keyword in text for keyword in keywords):
                break
        else:
            reply = f"我是{self.persona.name}，收到{event.user}的弹幕：「{event.text[:32]}」，我在听！"
            emotion, motion = "neutral", "nod"
        plan = ActionPlan(
            reply=reply,
            emotion=emotion,  # type: ignore[arg-type]
            motion=motion,  # type: ignore[arg-type]
            priority="normal",
            interrupt=False,
        )
        return DecisionResult(
            plan=plan,
            source="mock",
            raw={"matched": reply},
            latency_ms=50,
            model="mock-local",
            warnings=("未配置 DEEPSEEK_API_KEY，当前为本地模拟决策",),
        )

    async def close(self) -> None:
        return None


def build_engine(config: Config) -> DecisionEngine:
    persona = getattr(config, "persona", Persona())
    if config.effective_llm_mode == "deepseek":
        return DeepSeekEngine(config, persona=persona)
    return MockDecisionEngine(persona=persona)
