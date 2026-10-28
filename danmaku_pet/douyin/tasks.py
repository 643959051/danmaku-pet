"""Comment / gift interaction task lifecycle (start, status, stop).

Platform task starts are request-scoped: the admin endpoint calls the platform
and records the result.  There is no background retry task — an instance can be
replaced at any time, so a retry that is not driven by a request would be lost.
Operators can simply click "start" again on failure.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from danmaku_pet.douyin.openapi import PlatformNotConfigured

logger = logging.getLogger(__name__)

TaskKind = Literal["comment", "gift"]
TASK_KINDS: tuple[str, ...] = ("comment", "gift")


@dataclass(slots=True)
class InteractionTask:
    room_id: str
    kind: str
    status: str = "starting"
    task_id: str = ""
    error: str = ""
    retries: int = 0
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "kind": self.kind,
            "status": self.status,
            "task_id": self.task_id,
            "error": self.error,
            "retries": self.retries,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }


def _key(room_id: str, kind: str) -> str:
    return f"{room_id}|{kind}"


class InteractionTaskManager:
    def __init__(self, openapi: Any) -> None:
        self.openapi = openapi
        self._tasks: dict[str, InteractionTask] = {}

    async def start(self, room_id: str, kind: str) -> InteractionTask:
        if kind not in TASK_KINDS:
            raise ValueError(f"unsupported task kind: {kind!r}")
        task = self._tasks.get(_key(room_id, kind))
        if task is not None and task.status == "running":
            return task
        task = InteractionTask(room_id=room_id, kind=kind)
        self._tasks[_key(room_id, kind)] = task
        await self._attempt(task)
        return task

    async def _attempt(self, task: InteractionTask) -> None:
        task.status = "starting"
        task.updated_at = time.time()
        try:
            response = await self.openapi.start_task(task.room_id, task.kind)
        except PlatformNotConfigured as exc:
            task.status = "error"
            task.error = str(exc)
            task.updated_at = time.time()
            logger.warning("互动任务启动失败（平台未配置）: %s", exc)
            return
        except Exception as exc:  # noqa: BLE001 - converted to task state
            task.status = "error"
            task.error = str(exc)
            task.updated_at = time.time()
            logger.warning("互动任务启动失败（可手动重试）: %s", exc)
            return
        task.task_id = str(_dig_task_id(response))
        task.status = "running"
        task.error = ""
        task.updated_at = time.time()
        logger.info(
            "互动任务已启动: room=%s kind=%s task_id=%s",
            task.room_id,
            task.kind,
            task.task_id,
        )

    async def stop(self, room_id: str, kind: str) -> InteractionTask | None:
        task = self._tasks.get(_key(room_id, kind))
        if task is None:
            return None
        if task.task_id:
            try:
                await self.openapi.stop_task(task.task_id, kind)
            except Exception as exc:  # noqa: BLE001 - state is still updated
                task.error = str(exc)
                logger.warning("互动任务停止失败: %s", exc)
        task.status = "stopped"
        task.updated_at = time.time()
        return task

    async def stop_room(self, room_id: str) -> None:
        for kind in TASK_KINDS:
            await self.stop(room_id, kind)

    async def stop_all(self) -> None:
        for task in list(self._tasks.values()):
            await self.stop(task.room_id, task.kind)

    def snapshot(self) -> list[dict[str, Any]]:
        return [task.snapshot() for task in self._tasks.values()]

    def tasks_for(self, room_id: str) -> list[dict[str, Any]]:
        return [
            task.snapshot() for task in self._tasks.values() if task.room_id == room_id
        ]

    async def shutdown(self) -> None:
        """No background tasks to cancel; kept for the app lifecycle contract."""

        return None


def _dig_task_id(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    for key in ("task_id", "taskId", "id"):
        value = response.get(key)
        if value:
            return str(value)
    data = response.get("data")
    if isinstance(data, dict):
        for key in ("task_id", "taskId", "id"):
            value = data.get(key)
            if value:
                return str(value)
    return ""
