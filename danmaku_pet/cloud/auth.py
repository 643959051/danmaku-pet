"""Token authentication for the cloud admin surface and the client socket.

Two independent secrets:

* ``ADMIN_TOKEN``  — 运营/管理后台（REST + 管理端 WebSocket）
* ``CLIENT_TOKEN`` — 开播电脑客户端注册

Both are compared with :func:`hmac.compare_digest` so a wrong token cannot be
probed byte by byte.  Nothing here is logged.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Header, HTTPException, WebSocket, status

BEARER_PREFIX = "bearer "


def _normalize(value: str | None) -> str:
    text = (value or "").strip()
    if text.lower().startswith(BEARER_PREFIX):
        return text[len(BEARER_PREFIX) :].strip()
    return text


def _matches(expected: str, supplied: str | None) -> bool:
    if not expected:
        return False
    candidate = _normalize(supplied)
    if not candidate:
        return False
    return hmac.compare_digest(candidate, expected)


@dataclass(slots=True)
class TokenAuth:
    admin_token: str
    client_token: str

    # ------------------------------------------------------------------ admin
    async def require_admin(
        self,
        authorization: str | None = Header(default=None),
        x_admin_token: str | None = Header(default=None),
    ) -> None:
        if _matches(self.admin_token, x_admin_token) or _matches(
            self.admin_token, authorization
        ):
            return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="管理接口需要有效令牌（Authorization: Bearer <ADMIN_TOKEN>）",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ----------------------------------------------------------------- client
    def client_allowed(self, token: str | None) -> bool:
        return _matches(self.client_token, token)

    def require_admin_token(self, token: str | None) -> None:
        """For WebSocket handshakes, where dependencies cannot read headers."""

        if not (_matches(self.admin_token, token)):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="管理端 WebSocket 需要有效令牌",
            )

    async def websocket_admin_ok(self, websocket: WebSocket, token: str | None) -> bool:
        """Check the query-string token before accepting the socket."""

        if not _matches(self.admin_token, token):
            await websocket.close(code=4401, reason="invalid admin token")
            return False
        return True
