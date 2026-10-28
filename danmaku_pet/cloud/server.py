"""Cloud FastAPI application: admin console, gateway downlink, platform pushes.

Run with ``python -m danmaku_pet.cloud``.  The app is created by
:func:`create_app` so tests can inject a config without touching the process
environment.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from danmaku_pet import __version__
from danmaku_pet.broadcaster import Broadcaster
from danmaku_pet.cloud.auth import TokenAuth
from danmaku_pet.cloud.config import CloudConfig
from danmaku_pet.cloud.content_safety import ContentGuard
from danmaku_pet.cloud.gateway import GatewayManager
from danmaku_pet.cloud.metrics import StageMetrics
from danmaku_pet.cloud.orchestrator import CloudOrchestrator
from danmaku_pet.cloud.sessions import ClientSession, SessionRegistry
from danmaku_pet.cloud.state import create_state_store
from danmaku_pet.cloud.ws_gateway import ClientSocket
from danmaku_pet.decision.gifts import gift_table
from danmaku_pet.decision.llm import MockDecisionEngine, build_engine
from danmaku_pet.decision.prompts import build_system_prompt
from danmaku_pet.douyin.adapter import DouyinEventAdapter
from danmaku_pet.douyin.openapi import DouyinOpenAPI
from danmaku_pet.douyin.receiver import DouyinReceiver
from danmaku_pet.douyin.tasks import InteractionTaskManager
from danmaku_pet.douyin.verifier import MessageVerifier
from danmaku_pet.execution.action_adapter import ActionAdapter
from danmaku_pet.inputs.manual import manual_event
from danmaku_pet.inputs.simulator import Simulator
from danmaku_pet.logging_setup import setup_cloud_logging
from danmaku_pet.models import Event
from danmaku_pet.paths import RESOURCE_ROOT
from danmaku_pet.persona import Persona
from danmaku_pet.shared.commands import PerformResult
from danmaku_pet.shared.protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    check_protocol_version,
    decode_message,
)

logger = logging.getLogger(__name__)
WEB_DIR = RESOURCE_ROOT / "danmaku_pet" / "web" / "cloud"


# ---------------------------------------------------------------- request models
class ManualRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    user: str = Field(default="观众", max_length=40)


class GiftRequest(BaseModel):
    gift_name: str = Field(default="小心心", max_length=60)
    gift_id: str = Field(default="", max_length=128)
    gift_count: int = Field(default=1, ge=1, le=9999)
    user: str = Field(default="礼物观众", max_length=40)
    text: str = Field(default="", max_length=200)


class ReplyRequest(BaseModel):
    event_id: str | None = Field(default=None, max_length=64)
    text: str | None = Field(default=None, max_length=500)
    user: str | None = Field(default=None, max_length=40)


class AutoReplyRequest(BaseModel):
    enabled: bool
    room_id: str | None = Field(default=None, max_length=128)


class PauseRequest(BaseModel):
    paused: bool


class PersonaRequest(BaseModel):
    name: str = Field(default="小鱼", max_length=20)
    identity: str = Field(default="", max_length=200)
    tone: str = Field(default="", max_length=120)
    catchphrase: str = Field(default="", max_length=80)
    reply_length: str = Field(default="short", pattern="^(short|medium|long)$")
    emoji: bool = False
    rules: str = Field(default="", max_length=600)


class SimulatorRequest(BaseModel):
    interval_seconds: float = Field(default=4.0, ge=0.8, le=60.0)
    scenario: str = Field(default="mixed", pattern="^(mixed|chat|gift)$")
    room_id: str | None = Field(default=None, max_length=128)


class TaskRequest(BaseModel):
    room_id: str = Field(min_length=1, max_length=128)
    kind: str = Field(default="comment", pattern="^(comment|gift)$")


# ------------------------------------------------------------------- app factory
def create_app(
    config: CloudConfig,
    *,
    engine=None,
    openapi: DouyinOpenAPI | None = None,
) -> FastAPI:
    setup_cloud_logging(
        level=getattr(logging, config.log_level, logging.INFO),
        secrets=(
            config.admin_token,
            config.client_token,
            config.deepseek_api_key,
            config.douyin.app_secret,
            config.douyin.verify_secret,
            config.douyin.verify_token,
        ),
    )

    broadcaster = Broadcaster()
    metrics = StageMetrics()
    sessions = SessionRegistry(idle_timeout_s=30.0)
    guard = ContentGuard(
        blocked_words=config.blocked_words,
        max_comment_chars=config.max_comment_chars,
        max_reply_chars=config.max_reply_chars,
    )
    auth = TokenAuth(admin_token=config.admin_token, client_token=config.client_token)

    startup_warning: str | None = None
    if engine is None:
        try:
            engine = build_engine(config)
        except Exception as exc:  # noqa: BLE001 - start in a clearly-labelled mock
            startup_warning = f"DeepSeek 初始化失败，已切到本地模拟：{exc}"
            logger.error(startup_warning)
            engine = MockDecisionEngine(
                persona=config.persona, timezone_name=config.display_timezone
            )
            config.llm_mode = "mock"

    if openapi is None and config.douyin.base_url:
        openapi = DouyinOpenAPI(config.douyin)

    store = create_state_store(config)
    gateway = GatewayManager(
        mode=config.gateway_mode,
        broadcaster=broadcaster,
        metrics=metrics,
        openapi=openapi,
        mock_auto_ack=config.mock_auto_ack,
        mock_ack_delay_s=config.mock_ack_delay_s,
    )
    orchestrator = CloudOrchestrator(
        config=config,
        broadcaster=broadcaster,
        engine=engine,
        gateway=gateway,
        sessions=sessions,
        guard=guard,
        metrics=metrics,
        store=store,
    )
    gateway.set_result_handler(orchestrator.handle_result)

    verifier = MessageVerifier(config.douyin)
    adapter = DouyinEventAdapter(default_room_id=config.default_room_id)
    receiver = DouyinReceiver(
        verifier=verifier,
        adapter=adapter,
        on_event=orchestrator.handle_official_event,
    )
    tasks = InteractionTaskManager(openapi) if openapi is not None else None
    simulator = Simulator(orchestrator.handle_event, room_id=config.default_room_id)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await orchestrator.start()
        # Persona is state, not config: production reads it from Redis.  The
        # engine was built with the bundled default; swap if Redis has an edit.
        try:
            stored_persona = await store.load_persona()
        except Exception:
            logger.warning("读取远端人设失败，使用内置默认值", exc_info=True)
            stored_persona = None
        if stored_persona:
            persona = Persona.from_mapping(stored_persona)
            if persona != config.persona:
                config.persona = persona
                try:
                    await orchestrator.swap_engine(build_engine(config))
                except Exception:
                    logger.warning("应用远端人设失败，保留当前决策引擎", exc_info=True)
        try:
            yield
        finally:
            await simulator.stop()
            if tasks is not None:
                await tasks.shutdown()
            await orchestrator.shutdown()
            if openapi is not None:
                await openapi.close()
            logger.info("云端服务已关闭")

    app = FastAPI(
        title="弹幕宠物云端后端",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config = config
    app.state.broadcaster = broadcaster
    app.state.metrics = metrics
    app.state.sessions = sessions
    app.state.store = store
    app.state.auth = auth
    app.state.engine = engine
    app.state.gateway = gateway
    app.state.orchestrator = orchestrator
    app.state.receiver = receiver
    app.state.tasks = tasks
    app.state.simulator = simulator
    app.state.openapi = openapi
    app.state.startup_warning = startup_warning
    app.state.page_views = 0

    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="cloud-static")

    # --------------------------------------------------------------- helpers
    def require_test_endpoints() -> None:
        if not config.enable_test_endpoints:
            raise HTTPException(
                status_code=404,
                detail="测试接口在当前环境已关闭（ENABLE_TEST_ENDPOINTS=false）",
            )

    admin = [Depends(auth.require_admin)]
    admin_test = [Depends(auth.require_admin), Depends(require_test_endpoints)]

    async def _snapshot(request: Any) -> dict[str, Any]:
        client = getattr(request, "client", None)
        host = getattr(client, "host", "") or (client[0] if isinstance(client, tuple) else "")
        return {
            "app": {
                "name": "弹幕宠物云端后端",
                "version": __version__,
                "protocol_version": PROTOCOL_VERSION,
                "environment": config.environment,
                "started_at": orchestrator.started_at,
                "now": time.time(),
                "version_ok": True,
                "client_ip": host,
            },
            "config": config.public_status(),
            "startup_warning": app.state.startup_warning,
            "orchestrator": await orchestrator.snapshot(),
            "receiver": receiver.status(),
            "douyin_tasks": tasks.snapshot() if tasks is not None else [],
            "simulator": simulator.status(),
            "actions": {
                "motions": ActionAdapter.mapping_table(),
                "gifts": gift_table(),
            },
        }

    async def _require_room(room_id: str) -> dict[str, Any]:
        snapshot = await orchestrator.room_snapshot(room_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail=f"房间不存在：{room_id}")
        return snapshot

    # ------------------------------------------------------------------ pages
    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        page = WEB_DIR / "index.html"
        app.state.page_views += 1
        if not page.is_file():
            raise HTTPException(status_code=404, detail="管理页面未打包")
        return FileResponse(page)

    # ----------------------------------------------------------------- health
    @app.get("/v1/ping")
    async def ping() -> dict[str, Any]:
        """抖音云存活性探针：必须无鉴权返回 200。"""

        return {"ok": True, "status": "ok", "timestamp": time.time()}

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": await orchestrator.healthy(),
            "worker_alive": bool(orchestrator.alive),
            "environment": config.environment,
            "gateway_mode": config.gateway_mode,
            "state_backend": config.state_backend,
            "version": __version__,
            "protocol_version": PROTOCOL_VERSION,
            "rooms": await orchestrator.room_count(),
            "uptime_s": round(time.time() - orchestrator.started_at, 1),
            "timestamp": time.time(),
        }

    # ------------------------------------------------------------------ status
    @app.get("/api/status", dependencies=admin)
    async def status(request: Request) -> dict[str, Any]:
        return await _snapshot(request)

    @app.get("/api/prompt", dependencies=admin)
    async def prompt() -> dict[str, str]:
        return {"system": build_system_prompt(config.persona)}

    @app.get("/api/actions", dependencies=admin)
    async def actions() -> dict[str, Any]:
        return {"motions": ActionAdapter.mapping_table(), "gifts": gift_table()}

    @app.get("/api/rooms", dependencies=admin)
    async def rooms() -> dict[str, Any]:
        snapshot = await orchestrator.snapshot()
        return {"rooms": snapshot["rooms"], "sessions": sessions.snapshot()}

    @app.get("/api/rooms/{room_id}", dependencies=admin)
    async def room_detail(room_id: str) -> dict[str, Any]:
        return {"room": await _require_room(room_id)}

    # ------------------------------------------------------------- simulation
    @app.post("/api/rooms/{room_id}/events/manual", dependencies=admin_test)
    async def manual(room_id: str, payload: ManualRequest) -> dict[str, Any]:
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="text 不能为空")
        event = manual_event(text, payload.user, room_id=room_id)
        room = await orchestrator.handle_event(event)
        return {"ok": True, "event": event.to_dict(), "queue": room.snapshot()["queue"]}

    @app.post("/api/rooms/{room_id}/events/gift", dependencies=admin_test)
    async def gift(room_id: str, payload: GiftRequest) -> dict[str, Any]:
        count = max(1, payload.gift_count)
        name = payload.gift_name.strip() or "礼物"
        event = Event(
            source="gift_sim",
            user=payload.user,
            text=payload.text.strip() or f"送出 {name}" + (f" × {count}" if count > 1 else ""),
            gift_name=name,
            gift_id=payload.gift_id.strip(),
            gift_count=count,
            room_id=room_id,
            timestamp=time.time(),
            message_type="gift",
        )
        room = await orchestrator.handle_event(event)
        return {"ok": True, "event": event.to_dict(), "queue": room.snapshot()["queue"]}

    # ---------------------------------------------------------------- controls
    @app.post("/api/rooms/{room_id}/reply", dependencies=admin)
    async def reply(room_id: str, payload: ReplyRequest) -> dict[str, Any]:
        try:
            event = await orchestrator.reply_to(
                room_id,
                event_id=payload.event_id,
                text=payload.text,
                user=payload.user,
            )
        except LookupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="房间不存在") from exc
        return {"ok": True, "event": event.to_dict()}

    @app.post("/api/rooms/{room_id}/interrupt", dependencies=admin)
    async def interrupt(room_id: str) -> dict[str, Any]:
        await _require_room(room_id)
        sent = await orchestrator.interrupt(room_id, "主播手动打断当前表演")
        return {"ok": True, "delivered": sent}

    @app.post("/api/rooms/{room_id}/pause", dependencies=admin)
    async def pause(room_id: str, payload: PauseRequest) -> dict[str, Any]:
        await _require_room(room_id)
        await orchestrator.set_paused(room_id, payload.paused)
        return {"ok": True, "paused": bool(payload.paused)}

    @app.post("/api/rooms/{room_id}/context/clear", dependencies=admin_test)
    async def clear_context(room_id: str) -> dict[str, Any]:
        if not await orchestrator.clear_context(room_id):
            raise HTTPException(status_code=404, detail="房间不存在")
        return {"ok": True}

    @app.post("/api/settings/auto-reply", dependencies=admin)
    async def auto_reply(payload: AutoReplyRequest) -> dict[str, Any]:
        try:
            await orchestrator.set_auto_reply(payload.room_id, payload.enabled)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="房间不存在") from exc
        return {"ok": True, "enabled": bool(payload.enabled), "room_id": payload.room_id}

    @app.post("/api/settings/persona", dependencies=admin)
    async def update_persona(payload: PersonaRequest) -> dict[str, Any]:
        persona = Persona.from_mapping(payload.model_dump())
        candidate = replace(config, persona=persona)
        try:
            new_engine = build_engine(candidate)
        except Exception as exc:  # noqa: BLE001 - invalid settings, not a server bug
            raise HTTPException(status_code=400, detail=f"角色引擎初始化失败：{exc}") from exc
        try:
            await store.save_persona(persona.to_dict())
        except Exception as exc:  # noqa: BLE001
            await new_engine.close()
            raise HTTPException(status_code=500, detail=f"保存角色配置失败：{exc}") from exc
        config.persona = persona
        await orchestrator.swap_engine(new_engine)
        await broadcaster.send("persona", persona.to_dict())
        await broadcaster.send(
            "notice", {"level": "info", "message": f"角色语气已更新：{persona.name}"}
        )
        return {"ok": True, "persona": persona.to_dict()}

    @app.post("/api/simulator/start", dependencies=admin_test)
    async def start_simulator(payload: SimulatorRequest) -> dict[str, Any]:
        room_id = payload.room_id or config.default_room_id
        if not room_id:
            raise HTTPException(
                status_code=400,
                detail="需要指定 room_id（或配置 DEFAULT_ROOM_ID）",
            )
        await simulator.start(payload.interval_seconds, payload.scenario, room_id=room_id)
        await broadcaster.send("simulator", simulator.status())
        return {"ok": True, "simulator": simulator.status()}

    @app.post("/api/simulator/stop", dependencies=admin_test)
    async def stop_simulator() -> dict[str, Any]:
        await simulator.stop()
        await broadcaster.send("simulator", simulator.status())
        return {"ok": True, "simulator": simulator.status()}

    # ------------------------------------------------------------- douyin tasks
    @app.get("/api/douyin/tasks", dependencies=admin)
    async def douyin_tasks() -> dict[str, Any]:
        return {
            "configured": tasks is not None,
            "tasks": tasks.snapshot() if tasks is not None else [],
            "receiver": receiver.status(),
        }

    @app.post("/api/douyin/tasks/start", dependencies=admin)
    async def start_task(payload: TaskRequest) -> dict[str, Any]:
        if tasks is None:
            raise HTTPException(
                status_code=409,
                detail="平台接口未配置（DOUYIN_OPENAPI_BASE_URL 等），无法启动互动数据任务",
            )
        await _require_room(payload.room_id)
        task = await tasks.start(payload.room_id, payload.kind)
        await broadcaster.send("douyin_task", task.snapshot())
        return {"ok": task.status == "running", "task": task.snapshot()}

    @app.post("/api/douyin/tasks/stop", dependencies=admin)
    async def stop_task(payload: TaskRequest) -> dict[str, Any]:
        if tasks is None:
            raise HTTPException(status_code=409, detail="平台接口未配置")
        task = await tasks.stop(payload.room_id, payload.kind)
        await broadcaster.send(
            "douyin_task", task.snapshot() if task else {"room_id": payload.room_id}
        )
        return {"ok": True, "task": task.snapshot() if task else None}

    # -------------------------------------------------------------- platform in
    app.include_router(receiver.router())

    # --------------------------------------------------------------- WebSockets
    @app.websocket("/ws")
    async def admin_ws(websocket: WebSocket, token: str = Query(default="")) -> None:
        if not await auth.websocket_admin_ok(websocket, token):
            return
        await broadcaster.connect(websocket)
        try:
            await websocket.send_json({"type": "snapshot", "data": await _snapshot(websocket)})
            while True:
                message = await websocket.receive_text()
                if message.strip() == "ping":
                    await websocket.send_json({"type": "pong", "data": time.time()})
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("管理端 WebSocket 断开", exc_info=True)
        finally:
            await broadcaster.disconnect(websocket)

    @app.websocket("/ws/client")
    async def client_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            raw = await asyncio.wait_for(websocket.receive_json(), timeout=15)
        except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError):
            await websocket.close(code=4408, reason="hello timeout")
            return
        try:
            hello = decode_message(raw)
        except ProtocolError as exc:
            await websocket.close(code=4400, reason=str(exc)[:100])
            return
        if hello.kind != "hello":
            await websocket.close(code=4400, reason="expected hello")
            return
        try:
            version = check_protocol_version(hello.protocol_version)
        except ProtocolError as exc:
            await websocket.close(code=4406, reason=str(exc)[:100])
            return
        if not auth.client_allowed(str(hello.payload.get("token") or "")):
            await websocket.close(code=4401, reason="invalid client token")
            return
        client_id = str(hello.payload.get("client_id") or "").strip()[:128]
        room_id = str(hello.payload.get("room_id") or "").strip()[:128]
        if not client_id or not room_id:
            await websocket.close(code=4400, reason="client_id and room_id are required")
            return
        client = websocket.client
        session = ClientSession(
            client_id=client_id,
            room_id=room_id,
            protocol_version=version,
            remote=f"{client.host}:{client.port}" if client else "",
            user_agent=websocket.headers.get("user-agent", "")[:200],
        )
        sessions.bind(session)
        socket = ClientSocket(websocket, client_id=client_id, room_id=room_id)
        for old in gateway.attach_client(socket):
            await old.close(code=4409, reason="replaced by a newer connection")
        logger.info("客户端已连接: client=%s room=%s", client_id, room_id)
        await broadcaster.send(
            "client",
            {
                "event": "connected",
                "client_id": client_id,
                "room_id": room_id,
                "session": session.snapshot(),
            },
        )
        await socket.send_envelope(
            "welcome",
            {
                "client_id": client_id,
                "room_id": room_id,
                "server_time": time.time(),
                "heartbeat_interval_s": 8,
                "gateway_mode": config.gateway_mode,
            },
            version=version,
        )
        try:
            while True:
                raw = await websocket.receive_json()
                try:
                    message = decode_message(raw)
                except ProtocolError as exc:
                    logger.warning("客户端 %s 发来非法消息: %s", client_id, exc)
                    continue
                sessions.touch(client_id)
                if message.kind == "result":
                    try:
                        result = PerformResult.from_dict(message.payload)
                    except ValueError as exc:
                        logger.warning("客户端 %s 的回执格式错误: %s", client_id, exc)
                        continue
                    result = replace(result, client_id=result.client_id or client_id)
                    await orchestrator.handle_result(result)
                elif message.kind == "heartbeat":
                    await socket.send_envelope(
                        "pong", {"server_time": time.time()}, version=version
                    )
                else:
                    logger.debug("客户端 %s 消息: %s", client_id, message.kind)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("客户端 WebSocket 断开", exc_info=True)
        finally:
            gateway.detach_socket(socket)
            sessions.release(session)
            await broadcaster.send(
                "client", {"event": "disconnected", "client_id": client_id, "room_id": room_id}
            )
            await _broadcast_room_status(client_id, room_id)
            logger.info("客户端已断开: client=%s room=%s", client_id, room_id)

    async def _broadcast_room_status(client_id: str, room_id: str) -> None:
        snapshot = await orchestrator.room_snapshot(room_id)
        if snapshot is not None:
            await broadcaster.send("status", {"room_id": room_id, "room": snapshot})

    return app
