"""HTTP receiver for official comment / gift pushes.

Responsibilities, in order: verify -> parse -> de-duplicate -> normalize ->
dispatch to the cloud orchestrator.  Duplicates return 200 (not an error) so a
platform retry does not turn into an infinite retry loop; malformed payloads
return 4xx so the platform's own retry policy can be applied.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from danmaku_pet.douyin.adapter import DouyinEventAdapter, MessageFormatError
from danmaku_pet.douyin.verifier import MessageVerifier, VerificationError
from danmaku_pet.shared.events import Event

logger = logging.getLogger(__name__)

EventHandler = Callable[[Event], Awaitable[Any]]


class DouyinReceiver:
    def __init__(
        self,
        *,
        verifier: MessageVerifier,
        adapter: DouyinEventAdapter,
        on_event: EventHandler,
    ) -> None:
        self.verifier = verifier
        self.adapter = adapter
        self.on_event = on_event
        self.counts: dict[str, int] = {
            "received": 0,
            "accepted": 0,
            "duplicate": 0,
            "rejected": 0,
            "failed": 0,
        }
        self.last_error: str = ""

    def router(self, *, prefix: str = "/api/douyin") -> APIRouter:
        router = APIRouter(prefix=prefix, tags=["douyin"])

        @router.post("/events/comment")
        async def comment(request: Request, room_id: str = Query(default="")) -> JSONResponse:
            return await self.handle(request, kind="comment", room_id=room_id)

        @router.post("/events/gift")
        async def gift(request: Request, room_id: str = Query(default="")) -> JSONResponse:
            return await self.handle(request, kind="gift", room_id=room_id)

        @router.post("/events/{kind}")
        async def generic(kind: str, request: Request, room_id: str = Query(default="")) -> JSONResponse:
            return await self.handle(request, kind=kind, room_id=room_id)

        return router

    async def handle(self, request: Request, *, kind: str, room_id: str) -> JSONResponse:
        self.counts["received"] += 1
        body = await request.body()
        try:
            self.verifier.verify(request.headers, body)
        except VerificationError as exc:
            self.counts["rejected"] += 1
            self.last_error = f"verify:{exc.code}"
            logger.warning("拒绝未通过校验的平台消息: %s", exc)
            return JSONResponse(
                status_code=401,
                content={"ok": False, "error": "verification_failed", "code": exc.code},
            )

        try:
            payload = json.loads(body.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError:
            self.counts["rejected"] += 1
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "invalid_json"},
            )
        if not isinstance(payload, dict):
            self.counts["rejected"] += 1
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "payload_must_be_object"},
            )

        try:
            event = self.adapter.normalize(payload, kind=kind, room_id=room_id)
        except MessageFormatError as exc:
            self.counts["rejected"] += 1
            self.last_error = f"format:{exc}"
            logger.warning("平台消息无法转换: %s", exc)
            return JSONResponse(
                status_code=422,
                content={"ok": False, "error": "unsupported_payload", "detail": str(exc)},
            )

        if not event.room_id:
            self.counts["rejected"] += 1
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "room_id_required"},
            )

        try:
            accepted = await self.on_event(event)
        except Exception as exc:  # noqa: BLE001 - return a retryable failure
            self.counts["failed"] += 1
            self.last_error = f"dispatch:{exc}"
            logger.exception("平台消息派发失败")
            return JSONResponse(
                status_code=500,
                content={"ok": False, "error": "dispatch_failed"},
            )

        if accepted is False:
            # The orchestrator's external dedup store saw this message already.
            self.counts["duplicate"] += 1
            return JSONResponse(
                status_code=200,
                content={
                    "ok": True,
                    "duplicate": True,
                    "room_id": event.room_id,
                    "message_id": event.message_id,
                },
            )

        self.counts["accepted"] += 1
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "room_id": event.room_id,
                "event_id": event.event_id,
                "message_id": event.message_id,
            },
        )

    def status(self) -> dict[str, Any]:
        return {
            "counts": dict(self.counts),
            "last_error": self.last_error,
            "verify_mode": self.verifier.mode,
        }
