"""Local status page (no DeepSeek key UI) and client runtime wiring."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from danmaku_pet import __version__
from danmaku_pet.broadcaster import Broadcaster
from danmaku_pet.client.connection import ClientConnection
from danmaku_pet.client.diagnostics import Diagnostics
from danmaku_pet.client.dispatcher import CommandDispatcher
from danmaku_pet.client.executor import LocalExecutor
from danmaku_pet.config import Config
from danmaku_pet.execution.action_adapter import ActionAdapter
from danmaku_pet.execution.backend import LocalPerformanceBackend
from danmaku_pet.execution.dafeiyu import DafeiyuNativeBackend
from danmaku_pet.execution.tts import AudioPlayer, EdgeTTSService
from danmaku_pet.paths import RESOURCE_ROOT
from danmaku_pet.settings_store import update_env_file

logger = logging.getLogger(__name__)
WEB_DIR = RESOURCE_ROOT / "danmaku_pet" / "web" / "client"


class ClientRuntime:
    """Owns every long-lived client object; the FastAPI app only reads state."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.page_views = 0
        self.broadcaster = Broadcaster()
        self.adapter = ActionAdapter()
        self.tts = EdgeTTSService(
            config.tts_cache_dir,
            voice=config.tts_voice,
            rate=config.tts_rate,
            volume=config.tts_volume,
            pitch=config.tts_pitch,
            cache_enabled=config.tts_cache_enabled,
        )
        self.player = AudioPlayer()
        self.native = DafeiyuNativeBackend(config)
        self.backend = LocalPerformanceBackend(
            config, self.native, self.player, self.broadcaster, self.adapter
        )
        self.executor = LocalExecutor(
            config, self.backend, self.tts, self.adapter, self.broadcaster
        )
        self.connection = ClientConnection(config)
        self.dispatcher = CommandDispatcher(
            self.executor,
            send_result=self.connection.send_result,
            client_id=config.client_id,
            dedup_ttl_s=600.0,
            grace_s=config.command_grace_s,
        )
        self.connection.bind_dispatcher(self.dispatcher)
        self.diagnostics = Diagnostics(
            config, self.connection, self.dispatcher, self.executor
        )

    async def start(self) -> None:
        await self.executor.start()
        await self.dispatcher.start()
        await self.connection.start()

    async def stop(self) -> None:
        await self.connection.stop()
        await self.dispatcher.stop()
        await self.executor.shutdown()


class BindRequest(BaseModel):
    room_id: str | None = Field(default=None, max_length=128)
    cloud_url: str | None = Field(default=None, max_length=500)


def create_app(config: Config, runtime: ClientRuntime | None = None) -> FastAPI:
    runtime = runtime or ClientRuntime(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()
            logger.info("本地客户端已关闭")

    app = FastAPI(
        title="弹幕宠物本地客户端",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config = config
    app.state.runtime = runtime
    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="client-static")

    def _status() -> dict[str, Any]:
        payload = runtime.diagnostics.snapshot()
        payload["app"]["version"] = __version__
        payload["app"]["protocol_version"] = 1
        return payload

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        page = WEB_DIR / "index.html"
        runtime.page_views += 1
        if not page.is_file():
            raise HTTPException(status_code=404, detail="本地状态页未打包")
        return FileResponse(page)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "connection": runtime.connection.state,
            "room_id": config.room_id,
            "timestamp": time.time(),
        }

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return _status()

    @app.post("/api/interrupt")
    async def interrupt() -> dict[str, Any]:
        await runtime.dispatcher.handle_interrupt("主播在本地页面停止当前表演")
        return {"ok": True}

    @app.post("/api/reconnect")
    async def reconnect() -> dict[str, Any]:
        await runtime.connection.restart()
        return {"ok": True, "state": runtime.connection.state}

    @app.post("/api/bind")
    async def bind(payload: BindRequest) -> JSONResponse:
        warnings: list[str] = []
        room_id = (payload.room_id or config.room_id).strip()
        cloud_url = (payload.cloud_url or config.cloud_url).strip()
        if not room_id:
            raise HTTPException(status_code=400, detail="room_id 不能为空")
        if not cloud_url.startswith(("ws://", "wss://")):
            raise HTTPException(status_code=400, detail="cloud_url 必须以 ws:// 或 wss:// 开头")
        config.room_id = room_id
        config.cloud_url = cloud_url
        try:
            update_env_file(
                config.root / ".env",
                {"ROOM_ID": room_id, "CLOUD_URL": cloud_url},
            )
        except Exception as exc:  # noqa: BLE001 - binding still applies for this run
            warnings.append(f"写入 .env 失败：{exc}")
            runtime.diagnostics.error("settings", f"写入 .env 失败：{exc}")
        await runtime.connection.restart()
        return JSONResponse(
            {
                "ok": True,
                "room_id": room_id,
                "cloud_url": cloud_url,
                "state": runtime.connection.state,
                "warnings": warnings,
            }
        )

    @app.get("/api/actions")
    async def actions() -> dict[str, Any]:
        return {"motions": ActionAdapter.mapping_table()}

    return app
