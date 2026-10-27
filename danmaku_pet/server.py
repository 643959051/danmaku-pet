"""FastAPI control surface and WebSocket observation channel."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from danmaku_pet.broadcaster import Broadcaster
from danmaku_pet.config import ROOT, Config
from danmaku_pet.decision.gifts import gift_table
from danmaku_pet.decision.llm import MockDecisionEngine, build_engine
from danmaku_pet.decision.prompts import build_system_prompt
from danmaku_pet.execution.action_adapter import ActionAdapter
from danmaku_pet.execution.backend import PerformanceBackend
from danmaku_pet.execution.dafeiyu import DafeiyuNativeBackend
from danmaku_pet.execution.tts import AudioPlayer, EdgeTTSService
from danmaku_pet.inputs.manual import manual_event
from danmaku_pet.inputs.simulator import Simulator
from danmaku_pet.logging_setup import setup_logging
from danmaku_pet.models import Event
from danmaku_pet.orchestrator import Orchestrator
from danmaku_pet.persona import Persona, save_persona
from danmaku_pet.paths import RESOURCE_ROOT
from danmaku_pet.settings_store import update_env_file

logger = logging.getLogger(__name__)
WEB_DIR = RESOURCE_ROOT / "danmaku_pet" / "web"

CONFIG = Config.load()


class ManualRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    user: str = Field(default="观众", max_length=40)


class GiftRequest(BaseModel):
    gift_name: str = Field(default="小心心", max_length=60)
    gift_count: int = Field(default=1, ge=1, le=9999)
    user: str = Field(default="礼物观众", max_length=40)
    text: str = Field(default="", max_length=200)


class SimulatorRequest(BaseModel):
    interval_seconds: float = Field(default=4.0, ge=0.8, le=60.0)
    scenario: str = Field(default="mixed", pattern="^(mixed|chat|gift)$")


class EmptyRequest(BaseModel):
    pass


class LlmSettingsRequest(BaseModel):
    mode: str = Field(default="auto", pattern="^(auto|deepseek|mock)$")
    api_key: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=80)
    base_url: str | None = Field(default=None, max_length=300)
    clear_key: bool = False


class ReplyRequest(BaseModel):
    event_id: str | None = Field(default=None, max_length=40)
    text: str | None = Field(default=None, max_length=500)
    user: str | None = Field(default=None, max_length=40)


class AutoReplyRequest(BaseModel):
    enabled: bool


class PersonaRequest(BaseModel):
    name: str = Field(default="小鱼", max_length=20)
    identity: str = Field(default="", max_length=200)
    tone: str = Field(default="", max_length=120)
    catchphrase: str = Field(default="", max_length=80)
    reply_length: str = Field(default="short", pattern="^(short|medium|long)$")
    emoji: bool = False
    rules: str = Field(default="", max_length=600)


def _snapshot(app: FastAPI) -> dict[str, Any]:
    state = app.state
    orchestrator: Orchestrator = state.orchestrator
    simulator: Simulator = state.simulator
    tts: EdgeTTSService = state.tts
    adapter: ActionAdapter = state.adapter
    config: Config = state.config
    broadcaster: Broadcaster = state.broadcaster
    return {
        "app": {
            "name": "弹幕小人 MVP",
            "version": "0.1.0",
            "started_at": orchestrator.started_at,
            "now": time.time(),
            "url": f"http://{config.host}:{config.port}/",
        },
        "config": config.public_status(),
        "startup_warning": getattr(state, "startup_warning", None),
        "orchestrator": orchestrator.snapshot(),
        "simulator": simulator.status(),
        "tts": tts.status(),
        "broadcaster_clients": broadcaster.count,
        "actions": {
            "motions": adapter.mapping_table(),
            "gifts": gift_table(),
        },
    }


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = CONFIG
    setup_logging(config.logs_dir)
    logger.info("启动弹幕小人 MVP: %s", config.public_status())
    broadcaster = Broadcaster()
    adapter = ActionAdapter()
    tts = EdgeTTSService(
        config.tts_cache_dir,
        voice=config.tts_voice,
        rate=config.tts_rate,
        volume=config.tts_volume,
        pitch=config.tts_pitch,
        cache_enabled=config.tts_cache_enabled,
    )
    startup_warning: str | None = None
    try:
        engine = build_engine(config)
    except Exception as exc:
        startup_warning = f"DeepSeek 初始化失败，已切到本地模拟：{exc}"
        logger.error(startup_warning)
        engine = MockDecisionEngine()
        config.llm_mode = "mock"
    player = AudioPlayer()
    native = DafeiyuNativeBackend(config)
    backend = PerformanceBackend(config, native, player, broadcaster, adapter)
    orchestrator = Orchestrator(config, broadcaster, engine, tts, backend, adapter)
    simulator = Simulator(orchestrator.ingest)

    app.state.config = config
    app.state.broadcaster = broadcaster
    app.state.tts = tts
    app.state.adapter = adapter
    app.state.backend = backend
    app.state.engine = engine
    app.state.orchestrator = orchestrator
    app.state.simulator = simulator
    app.state.startup_warning = startup_warning
    app.state.started_at = time.time()

    await orchestrator.start()
    try:
        yield
    finally:
        await simulator.stop()
        await orchestrator.shutdown()
        logger.info("服务已关闭")


app = FastAPI(title="弹幕小人 MVP", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
if CONFIG.dafeiyu_assets.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=CONFIG.dafeiyu_assets),
        name="dafeiyu-assets",
    )
CONFIG.assets_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=CONFIG.assets_dir), name="media")


PAGE_VIEWS = 0


def page_views() -> int:
    """How many times the management page itself has been requested."""

    return PAGE_VIEWS


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    global PAGE_VIEWS
    PAGE_VIEWS += 1
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    orchestrator: Orchestrator = app.state.orchestrator
    return {
        "ok": True,
        "worker_alive": bool(orchestrator._worker and not orchestrator._worker.done()),
        "timestamp": time.time(),
    }


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return _snapshot(app)


@app.get("/api/prompt")
async def prompt() -> dict[str, str]:
    config: Config = app.state.config
    return {"system": build_system_prompt(config.persona)}


@app.get("/api/actions")
async def actions() -> dict[str, Any]:
    return {
        "motions": ActionAdapter.mapping_table(),
        "gifts": gift_table(),
    }


@app.post("/api/events/manual")
async def manual(payload: ManualRequest) -> dict[str, Any]:
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")
    event = manual_event(text, payload.user)
    orchestrator: Orchestrator = app.state.orchestrator
    await orchestrator.submit(
        event, read_aloud=bool(app.state.config.auto_reply)
    )
    return {"ok": True, "event": event.to_dict(), "queue": orchestrator.queue.snapshot()}


@app.post("/api/events/gift")
async def gift(payload: GiftRequest) -> dict[str, Any]:
    count = max(1, payload.gift_count)
    event = Event(
        source="gift_sim",
        user=payload.user,
        text=payload.text.strip()
        or f"送出 {payload.gift_name}" + (f" × {count}" if count > 1 else ""),
        gift_name=payload.gift_name.strip() or "礼物",
        gift_count=count,
        timestamp=time.time(),
    )
    orchestrator: Orchestrator = app.state.orchestrator
    await orchestrator.submit(event)
    return {"ok": True, "event": event.to_dict(), "queue": orchestrator.queue.snapshot()}


@app.post("/api/chat/reply")
async def chat_reply(payload: ReplyRequest) -> dict[str, Any]:
    """Manually make the character reply to the last (or a chosen) danmaku."""

    orchestrator: Orchestrator = app.state.orchestrator
    try:
        event = await orchestrator.reply_to(
            event_id=payload.event_id,
            text=payload.text,
            user=payload.user,
        )
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "event": event.to_dict(), "queue": orchestrator.queue.snapshot()}


@app.post("/api/settings/auto-reply")
async def update_auto_reply(payload: AutoReplyRequest) -> dict[str, Any]:
    """Default-off auto reply switch, persisted in workspace .env."""

    config: Config = app.state.config
    orchestrator: Orchestrator = app.state.orchestrator
    previous = config.auto_reply
    config.auto_reply = bool(payload.enabled)
    try:
        update_env_file(
            ROOT / ".env",
            {"AUTO_REPLY": "true" if payload.enabled else "false"},
        )
    except Exception as exc:
        config.auto_reply = previous
        raise HTTPException(status_code=500, detail=f"写入 .env 失败：{exc}") from exc
    await orchestrator.set_auto_reply(payload.enabled)
    await app.state.broadcaster.send(
        "notice",
        {
            "level": "info",
            "message": "自动回复已开启：新弹幕会先念出来再回复"
            if payload.enabled
            else "自动回复已关闭：新弹幕进入待回复列表",
        },
    )
    return {"ok": True, "enabled": config.auto_reply, "config": config.public_status()}


@app.post("/api/settings/persona")
async def update_persona(payload: PersonaRequest) -> dict[str, Any]:
    """Persist the character persona and hot-swap the decision engine prompt."""

    config: Config = app.state.config
    orchestrator: Orchestrator = app.state.orchestrator
    persona = Persona.from_mapping(payload.model_dump())
    candidate = replace(config, persona=persona)
    try:
        new_engine = build_engine(candidate)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"角色引擎初始化失败：{exc}") from exc
    try:
        save_persona(config.persona_path, persona)
    except Exception as exc:
        await new_engine.close()
        raise HTTPException(status_code=500, detail=f"保存角色配置失败：{exc}") from exc
    config.persona = persona
    await orchestrator.swap_engine(new_engine)
    await app.state.broadcaster.send("persona", persona.to_dict())
    await app.state.broadcaster.send(
        "notice",
        {"level": "info", "message": f"角色语气已更新：{persona.name}"},
    )
    return {
        "ok": True,
        "persona": persona.to_dict(),
        "config": config.public_status(),
    }


@app.post("/api/settings/llm")
async def update_llm_settings(payload: LlmSettingsRequest) -> dict[str, Any]:
    """Update the API key/model at runtime and persist it to workspace .env.

    The secret is write-only from the browser's perspective: no endpoint ever
    returns the key. The existing key is kept when the key field is left blank.
    """

    config: Config = app.state.config
    orchestrator: Orchestrator = app.state.orchestrator
    key = config.deepseek_api_key
    if payload.clear_key:
        key = ""
    elif payload.api_key and payload.api_key.strip():
        key = payload.api_key.strip()
    if payload.mode == "deepseek" and not key:
        raise HTTPException(
            status_code=400,
            detail="DeepSeek 模式需要 API Key；请填写 Key，或选择 auto/mock",
        )
    model = (payload.model or "").strip() or config.deepseek_model
    base_url = (payload.base_url or "").strip().rstrip("/") or config.deepseek_base_url
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Base URL 必须以 http:// 或 https:// 开头")

    candidate = replace(
        config,
        llm_mode=payload.mode,
        deepseek_api_key=key,
        deepseek_model=model,
        deepseek_base_url=base_url,
    )
    try:
        new_engine = build_engine(candidate)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"LLM 初始化失败：{exc}") from exc

    try:
        update_env_file(
            ROOT / ".env",
            {
                "LLM_MODE": payload.mode,
                "DEEPSEEK_API_KEY": key,
                "DEEPSEEK_MODEL": model,
                "DEEPSEEK_BASE_URL": base_url,
            },
        )
    except Exception as exc:
        await new_engine.close()
        raise HTTPException(status_code=500, detail=f"写入 .env 失败：{exc}") from exc

    config.llm_mode = payload.mode
    config.deepseek_api_key = key
    config.deepseek_model = model
    config.deepseek_base_url = base_url
    await orchestrator.swap_engine(new_engine)
    app.state.startup_warning = None
    public = config.public_status()
    await app.state.broadcaster.send("settings", public)
    await app.state.broadcaster.send(
        "notice",
        {
            "level": "info",
            "message": f"LLM 设置已应用：{public['llm_mode']} / {public['model']}",
        },
    )
    return {"ok": True, "config": public}


@app.post("/api/simulator/start")
async def start_simulator(payload: SimulatorRequest) -> dict[str, Any]:
    simulator: Simulator = app.state.simulator
    await simulator.start(payload.interval_seconds, payload.scenario)
    await app.state.broadcaster.send("simulator", simulator.status())
    return {"ok": True, "simulator": simulator.status()}


@app.post("/api/simulator/stop")
async def stop_simulator(_: EmptyRequest | None = None) -> dict[str, Any]:
    simulator: Simulator = app.state.simulator
    await simulator.stop()
    await app.state.broadcaster.send("simulator", simulator.status())
    return {"ok": True, "simulator": simulator.status()}


@app.post("/api/interrupt")
async def interrupt(_: EmptyRequest | None = None) -> dict[str, Any]:
    orchestrator: Orchestrator = app.state.orchestrator
    await orchestrator.interrupt_current("手动打断当前表演")
    return {"ok": True}


@app.post("/api/context/clear")
async def clear_context(_: EmptyRequest | None = None) -> dict[str, Any]:
    orchestrator: Orchestrator = app.state.orchestrator
    orchestrator.clear_memory()
    await app.state.broadcaster.send("notice", {"level": "info", "message": "上下文已清空"})
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    broadcaster: Broadcaster = app.state.broadcaster
    await broadcaster.connect(websocket)
    try:
        await websocket.send_json({"type": "snapshot", "data": _snapshot(app)})
        while True:
            message = await websocket.receive_text()
            if message.strip() == "ping":
                await websocket.send_json({"type": "pong", "data": time.time()})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("WebSocket 客户端断开", exc_info=True)
    finally:
        await broadcaster.disconnect(websocket)
