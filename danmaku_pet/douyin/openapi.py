"""Thin, configurable client for the official Douyin Open Platform APIs.

Every path comes from configuration.  When a path is missing the call raises
:class:`PlatformNotConfigured` with an actionable message instead of silently
talking to a guessed URL.  This is the single place where official HTTP calls
(including the 抖音云网关 downlink) are made.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Mapping, Protocol

import httpx

logger = logging.getLogger(__name__)


class PlatformNotConfigured(RuntimeError):
    """The platform capability or endpoint has not been configured yet."""


class DouyinAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        code: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retryable = retryable


class DouyinSettingsLike(Protocol):
    base_url: str
    app_id: str
    app_secret: str
    timeout_s: float
    retries: int
    token_path: str
    task_start_path: str
    task_stop_path: str
    task_status_path: str
    command_push_path: str
    interrupt_path: str


class DouyinOpenAPI:
    """HTTP calls with timeout, bounded retries and a cached access token."""

    def __init__(
        self,
        settings: DouyinSettingsLike,
        *,
        http_client: httpx.AsyncClient | None = None,
        sleep=asyncio.sleep,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(float(settings.timeout_s), connect=5.0)
        )
        self._access_token: str = ""
        self._token_expires_at: float = 0.0

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------ token
    async def access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at - 30:
            return self._access_token
        path = self.settings.token_path
        if not path:
            raise PlatformNotConfigured(
                "未配置 DOUYIN_TOKEN_PATH：平台凭证获取方式确认后再启用官方接口"
            )
        payload = {"app_id": self.settings.app_id, "app_secret": self.settings.app_secret}
        data = await self._request("POST", path, json=payload, authenticated=False)
        token = _dig(data, ("access_token", "accessToken", "token", "data.access_token"))
        expires_in = _dig_number(data, ("expires_in", "expiresIn", "data.expires_in"))
        if not token:
            raise DouyinAPIError("平台未返回 access_token", code="missing_token")
        self._access_token = str(token)
        self._token_expires_at = time.time() + max(60.0, float(expires_in or 7200))
        return self._access_token

    # --------------------------------------------------------------- requests
    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        if not path:
            raise PlatformNotConfigured("平台接口路径未配置（请查看 cloud.env.example）")
        base = (self.settings.base_url or "").rstrip("/")
        if not base:
            raise PlatformNotConfigured(
                "未配置 DOUYIN_OPENAPI_BASE_URL：请按抖音开放平台最新文档填写"
            )
        url = f"{base}{path if path.startswith('/') else '/' + path}"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if authenticated:
            token = await self.access_token()
            headers["Authorization"] = f"Bearer {token}"

        attempts = max(0, int(self.settings.retries)) + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method.upper(), url, json=dict(json or {}), params=dict(params or {}), headers=headers
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    raise DouyinAPIError(
                        f"平台网络错误：{exc}", retryable=True
                    ) from exc
                await self._sleep(min(4.0, 0.5 * (2**attempt)))
                continue

            if response.status_code == 429 or response.status_code >= 500:
                last_error = DouyinAPIError(
                    f"平台 HTTP {response.status_code}: {response.text[:200]}",
                    status_code=response.status_code,
                    retryable=True,
                )
                if attempt + 1 >= attempts:
                    raise last_error
                await self._sleep(min(4.0, 0.5 * (2**attempt)))
                continue
            if response.status_code >= 400:
                raise DouyinAPIError(
                    f"平台 HTTP {response.status_code}: {response.text[:300]}",
                    status_code=response.status_code,
                    retryable=False,
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise DouyinAPIError("平台返回的不是 JSON") from exc
            if not isinstance(data, dict):
                raise DouyinAPIError("平台返回的 JSON 顶层不是对象")
            _raise_for_business_error(data)
            return data
        raise last_error or DouyinAPIError("平台请求失败")

    # ------------------------------------------------------- interaction tasks
    async def start_task(self, room_id: str, kind: str) -> dict[str, Any]:
        path = self.settings.task_start_path
        if not path:
            raise PlatformNotConfigured(
                "未配置 DOUYIN_TASK_START_PATH：评论/礼物互动数据任务接口待平台确认"
            )
        return await self._request(
            "POST", path, json={"room_id": room_id, "task_type": kind}
        )

    async def stop_task(self, task_id: str, kind: str) -> dict[str, Any]:
        path = self.settings.task_stop_path
        if not path:
            raise PlatformNotConfigured("未配置 DOUYIN_TASK_STOP_PATH")
        return await self._request(
            "POST", path, json={"task_id": task_id, "task_type": kind}
        )

    async def query_task(self, task_id: str) -> dict[str, Any]:
        path = self.settings.task_status_path
        if not path:
            raise PlatformNotConfigured("未配置 DOUYIN_TASK_STATUS_PATH")
        return await self._request("GET", path, params={"task_id": task_id})

    # ------------------------------------------------------- 抖音云网关下发
    async def send_perform_command(self, command: Any) -> dict[str, Any]:
        path = self.settings.command_push_path
        if not path:
            raise PlatformNotConfigured(
                "未配置 DOUYIN_COMMAND_PUSH_PATH：抖音云网关下发接口待平台确认"
            )
        return await self._request(
            "POST",
            path,
            json={
                "room_id": command.room_id,
                "command_id": command.command_id,
                "command": command.to_dict(),
            },
        )

    async def send_interrupt(self, room_id: str, reason: str) -> dict[str, Any]:
        path = self.settings.interrupt_path
        if not path:
            raise PlatformNotConfigured("未配置 DOUYIN_COMMAND_INTERRUPT_PATH")
        return await self._request(
            "POST", path, json={"room_id": room_id, "reason": reason}
        )


def _dig(payload: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    """Look up a dotted key path, tolerating a nested ``data`` envelope."""

    for key in keys:
        current: Any = payload
        for part in key.split("."):
            if isinstance(current, Mapping) and part in current:
                current = current[part]
            else:
                current = None
                break
        if current not in (None, ""):
            return current
    return None


def _dig_number(payload: Mapping[str, Any], keys: tuple[str, ...]) -> float | None:
    value = _dig(payload, keys)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _raise_for_business_error(payload: Mapping[str, Any]) -> None:
    """Platform responses often carry a business code inside HTTP 200."""

    code = payload.get("code", payload.get("err_no", payload.get("errno")))
    if code in (None, "", 0, "0"):
        return
    message = payload.get("message") or payload.get("msg") or "平台返回错误码"
    raise DouyinAPIError(f"平台错误 {code}: {message}", code=str(code), retryable=False)
