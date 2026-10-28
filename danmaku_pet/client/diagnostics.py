"""Local diagnostics snapshot for the 开播电脑 status page.

Everything here is meant to answer one of five questions at a glance:

1. 云端连上了吗？
2. 绑定的是哪个直播间？
3. 现在在做什么动作？
4. 声音从哪台设备出？
5. 最近一次错误是什么？
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from danmaku_pet.client.connection import ClientConnection
from danmaku_pet.client.dispatcher import CommandDispatcher
from danmaku_pet.client.executor import LocalExecutor
from danmaku_pet.config import Config


class Diagnostics:
    def __init__(
        self,
        config: Config,
        connection: ClientConnection,
        dispatcher: CommandDispatcher,
        executor: LocalExecutor,
    ) -> None:
        self.config = config
        self.connection = connection
        self.dispatcher = dispatcher
        self.executor = executor
        self.started_at = time.time()
        self.recent_errors: deque[dict[str, Any]] = deque(maxlen=20)

    def error(self, source: str, message: str) -> None:
        self.recent_errors.appendleft(
            {"at": time.time(), "source": source, "message": str(message)[:400]}
        )

    # ------------------------------------------------------------------ health
    def problems(self) -> list[str]:
        problems: list[str] = []
        if not self.config.room_id:
            problems.append("未绑定直播间（room_id 为空）")
        if self.connection.state != "online":
            detail = f"：{self.connection.last_error}" if self.connection.last_error else ""
            problems.append(f"云端未连接（{self.connection.state}）{detail}")
        backend = self.executor.backend.status()
        pet = backend.get("desktop_pet", {})
        state = pet.get("state")
        if self.config.desktop_pet_enabled and state not in {"ready", "starting"}:
            problems.append(f"桌面小人未就绪（{state}）：{pet.get('last_error') or '等待 helper'}")
        if not self.config.desktop_pet_enabled:
            problems.append("桌面小人已关闭（--no-desktop），直播伴侣将没有画面")
        audio = backend.get("audio", {})
        if not audio.get("mixer_ready"):
            problems.append(f"音频设备未就绪：{audio.get('last_error') or 'pygame mixer 未初始化'}")
        tts = self.executor.tts.status()
        if tts.get("last_error"):
            problems.append(f"EdgeTTS 最近一次失败：{tts['last_error']}")
        if self.executor.last_error:
            problems.append(f"最近执行错误：{self.executor.last_error}")
        return problems

    def snapshot(self) -> dict[str, Any]:
        return {
            "app": {
                "name": "弹幕宠物本地客户端",
                "started_at": self.started_at,
                "now": time.time(),
                "uptime_s": round(time.time() - self.started_at, 1),
            },
            "config": self.config.public_status(),
            "binding": {
                "room_id": self.config.room_id,
                "cloud_url": self.config.cloud_url,
                "client_id": self.config.client_id,
                "transport": self.config.transport,
                "bound": self.config.bound,
            },
            "connection": self.connection.status(),
            "dispatcher": self.dispatcher.status(),
            "executor": self.executor.status(),
            "diagnostics": {
                "problems": self.problems(),
                "recent_errors": list(self.recent_errors),
            },
        }
