"""抖音小程序内容安全检测（getAccessToken + text/antidirt）。

文档：
* ``POST /api/apps/v2/token`` 获取小程序 access_token；
* ``POST /api/v2/tags/text/antidirt`` 用 ``X-Token`` 检测文本/昵称。

正式环境把 access_token 缓存在外部状态存储（Redis）里，避免每个实例重复
getAccessToken —— 平台文档明确说明重复获取会让旧 token 的有效期缩短到 5 分钟。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

TOKEN_PATH = "/api/apps/v2/token"
CONTENT_SECURITY_PATH = "/api/v2/tags/text/antidirt"
_CACHE_KEY = "douyin:app-access-token"


class ContentSecurityUnavailable(RuntimeError):
    """The platform check could not be completed."""


class ContentSecurityCache(Protocol):
    async def cache_get(self, key: str) -> Any | None: ...
    async def cache_set(self, key: str, value: Any, ttl_s: float) -> None: ...
    def lock(self, key: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class ContentSecurityVerdict:
    allowed: bool
    status: str = "ok"  # ok / unsafe / unavailable / disabled
    unsafe_fields: tuple[str, ...] = ()
    reason: str = ""


class DouyinContentSecurity:
    def __init__(
        self,
        settings: Any,
        *,
        cache: ContentSecurityCache | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(float(settings.timeout_s), connect=5.0)
        )
        self._token = ""
        self._token_expires_at = 0.0
        self._local_lock = asyncio.Lock()
        base = (settings.content_security_base_url or "https://developer.toutiao.com").rstrip("/")
        self._base_url = base
        self._cache_key = f"{_CACHE_KEY}:{settings.app_id}"

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.settings.content_security_enabled),
            "base_url": self._base_url,
            "fail_closed": bool(self.settings.content_security_fail_closed),
        }

    async def check(self, text: str, *, user: str = "") -> ContentSecurityVerdict:
        if not self.settings.content_security_enabled:
            return ContentSecurityVerdict(True, status="disabled")

        fields: list[tuple[str, str]] = []
        if (text or "").strip():
            fields.append(("comment", str(text).strip()))
        if (user or "").strip():
            fields.append(("user", str(user).strip()))
        if not fields:
            return ContentSecurityVerdict(True)

        try:
            token = await self._access_token()
            payload = await self._post(
                CONTENT_SECURITY_PATH,
                headers={
                    "Content-Type": "application/json",
                    "X-Token": token,
                },
                json={"tasks": [{"content": value} for _, value in fields]},
            )
            return self._parse(payload, fields)
        except ContentSecurityUnavailable as exc:
            logger.warning("内容安全检测不可用: %s", exc)
            return ContentSecurityVerdict(
                False, status="unavailable", reason=str(exc)
            )
        except Exception:
            logger.exception("内容安全检测异常")
            return ContentSecurityVerdict(
                False, status="unavailable", reason="内容安全接口请求失败"
            )

    # ------------------------------------------------------------------ token
    async def _access_token(self) -> str:
        # In a multi-instance deployment another instance may refresh the token
        # early (which shortens the old token's life).  Always prefer the shared
        # Redis copy when a cache is configured.
        if self.cache is not None:
            cached = await self._read_cache()
            if cached:
                return cached

        now = time.time()
        if self._token and now < self._token_expires_at - 60:
            return self._token

        if self.cache is not None:
            try:
                async with self.cache.lock("douyin:access-token"):
                    cached = await self._read_cache()
                    if cached:
                        return cached
                    token, expires_in = await self._fetch_token()
                    await self._write_cache(token, expires_in)
                    return token
            except ContentSecurityUnavailable:
                raise
            except Exception as exc:
                # Cache/lock failure must not turn into a hard outage; fetch
                # once without the shared lock and keep going.
                logger.warning("读取共享 access_token 缓存失败: %s", exc)

        async with self._local_lock:
            now = time.time()
            if self._token and now < self._token_expires_at - 60:
                return self._token
            token, expires_in = await self._fetch_token()
            self._token = token
            self._token_expires_at = time.time() + min(3600, expires_in)
            return token

    async def _read_cache(self) -> str:
        if self.cache is None:
            return ""
        try:
            cached = await self.cache.cache_get(self._cache_key)
        except Exception as exc:
            logger.warning("读取 access_token 缓存失败: %s", exc)
            return ""
        if not isinstance(cached, dict):
            return ""
        token = str(cached.get("access_token") or "")
        try:
            expires_at = float(cached.get("expires_at") or 0)
        except (TypeError, ValueError):
            return ""
        if token and time.time() < expires_at - 60:
            self._token = token
            # 遵循平台“每小时更新一次”的建议，避免本地实例一直用到 2 小时。
            self._token_expires_at = min(expires_at, time.time() + 3600)
            return token
        return ""

    async def _write_cache(self, token: str, expires_in: int) -> None:
        if self.cache is None:
            return
        # 文档建议每小时刷新一次；即使 expires_in=7200，也最多缓存 1 小时。
        ttl = max(60, min(3600, int(expires_in) - 120))
        try:
            await self.cache.cache_set(
                self._cache_key,
                {"access_token": token, "expires_at": time.time() + expires_in},
                ttl,
            )
        except Exception as exc:
            logger.warning("写入 access_token 缓存失败: %s", exc)

    async def _fetch_token(self) -> tuple[str, int]:
        appid = str(self.settings.app_id or "").strip()
        secret = str(self.settings.app_secret or "").strip()
        if not appid or not secret:
            raise ContentSecurityUnavailable(
                "未配置 DOUYIN_APP_ID / DOUYIN_APP_SECRET"
            )
        payload = await self._post(
            TOKEN_PATH,
            json={
                "appid": appid,
                "grant_type": "client_credential",
                "secret": secret,
            },
        )
        err_no = payload.get("err_no")
        if err_no not in (None, 0, "0"):
            raise ContentSecurityUnavailable(
                f"getAccessToken 失败: err_no={err_no} {payload.get('err_tips', '')}".strip()
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ContentSecurityUnavailable("getAccessToken 响应缺少 data")
        token = str(data.get("access_token") or "")
        if not token:
            raise ContentSecurityUnavailable("getAccessToken 未返回 access_token")
        try:
            expires_in = int(data.get("expires_in") or 7200)
        except (TypeError, ValueError):
            expires_in = 7200
        expires_in = max(120, expires_in)
        self._token = token
        self._token_expires_at = time.time() + min(3600, expires_in)
        return token, expires_in

    # --------------------------------------------------------------- requests
    async def _post(
        self,
        path: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            response = await self._client.post(
                url,
                json=json,
                headers={"Content-Type": "application/json", **(headers or {})},
            )
        except httpx.HTTPError as exc:
            raise ContentSecurityUnavailable(f"网络错误: {exc}") from exc

        if response.status_code != 200:
            raise ContentSecurityUnavailable(
                f"内容安全接口 HTTP {response.status_code}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ContentSecurityUnavailable("内容安全接口返回的不是 JSON") from exc
        if not isinstance(data, dict):
            raise ContentSecurityUnavailable("内容安全接口返回的 JSON 顶层不是对象")
        return data

    def _parse(
        self,
        payload: dict[str, Any],
        fields: list[tuple[str, str]],
    ) -> ContentSecurityVerdict:
        code = payload.get("code", payload.get("errCode"))
        if code not in (None, 0, "0"):
            raise ContentSecurityUnavailable(
                f"内容安全接口错误码 {code}: {payload.get('message', '')}".strip()
            )
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != len(fields):
            raise ContentSecurityUnavailable("内容安全接口返回结果数量不匹配")

        unsafe: list[str] = []
        for (label, _), item in zip(fields, data):
            if not isinstance(item, dict):
                raise ContentSecurityUnavailable("内容安全接口返回了无效结果")
            item_code = item.get("code")
            if item_code not in (None, 0, "0"):
                raise ContentSecurityUnavailable(
                    f"内容安全任务失败: {item_code} {item.get('msg', '')}".strip()
                )
            predicts = item.get("predicts") or []
            if not isinstance(predicts, list):
                raise ContentSecurityUnavailable("内容安全接口缺少 predicts")
            if any(_is_hit(prediction) for prediction in predicts):
                unsafe.append(label)

        if unsafe:
            return ContentSecurityVerdict(
                False,
                status="unsafe",
                unsafe_fields=tuple(unsafe),
                reason="平台内容安全检测未通过",
            )
        return ContentSecurityVerdict(True)


def _is_hit(prediction: Any) -> bool:
    if not isinstance(prediction, dict):
        return False
    hit = prediction.get("hit")
    return hit is True or str(hit).lower() == "true"
