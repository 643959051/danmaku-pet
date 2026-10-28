"""Command dispatcher: dedup, expiry, ordering, preemption, result callbacks.

Status lifecycle per command:

    accepted -> started -> completed | interrupted | expired | error

Rules (改造计划 阶段 2/6/7):

* the same ``command_id`` is never performed twice;
* an already-expired command reports ``expired`` and is not performed;
* a high-priority gift command cancels the current performance and jumps the
  queue; the interrupted command reports ``interrupted`` once;
* only one performance runs at a time per client (first version).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Awaitable, Callable

from danmaku_pet.client.executor import LocalExecutor
from danmaku_pet.shared.commands import PerformCommand, PerformResult
from danmaku_pet.shared.protocol import DedupCache

logger = logging.getLogger(__name__)

ResultSink = Callable[[PerformResult], Awaitable[Any]]


class CommandDispatcher:
    def __init__(
        self,
        executor: LocalExecutor,
        *,
        send_result: ResultSink,
        client_id: str,
        dedup_ttl_s: float = 600.0,
        max_queue: int = 8,
        grace_s: float = 0.5,
    ) -> None:
        self.executor = executor
        self._send_result = send_result
        self.client_id = client_id
        self.max_queue = max(1, int(max_queue))
        self.grace_s = max(0.0, float(grace_s))
        self._dedup = DedupCache(ttl_s=dedup_ttl_s, maxsize=4096)
        self._items: deque[PerformCommand] = deque()
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._current_task: asyncio.Task[PerformResult] | None = None
        self._current_command: PerformCommand | None = None
        self._closing = False
        self._stats: dict[str, int] = {
            "accepted": 0,
            "started": 0,
            "completed": 0,
            "interrupted": 0,
            "expired": 0,
            "errors": 0,
            "duplicates": 0,
            "queue_dropped": 0,
        }
        self._recent_results: deque[PerformResult] = deque(maxlen=20)
        self._last_error = ""

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._closing = False
            self._worker = asyncio.create_task(self._worker_loop(), name="command-dispatcher")

    async def stop(self) -> None:
        self._closing = True
        worker = self._worker
        self._worker = None
        task = self._current_task
        if task is not None and not task.done():
            task.cancel()
        if worker is not None:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("停止指令分发器时出现异常")
        self._items.clear()
        self._wake.clear()

    # ------------------------------------------------------------------ submit
    async def submit(self, command: PerformCommand) -> str:
        """Queue a command; returns ``queued|duplicate|expired|closed``."""

        if self._closing:
            self._last_error = "客户端正在关闭"
            return "closed"
        if not self._dedup.check_and_add(command.command_id):
            self._stats["duplicates"] += 1
            logger.info("重复指令已忽略: %s", command.command_id)
            return "duplicate"
        if command.is_expired(grace_s=self.grace_s):
            self._stats["expired"] += 1
            logger.info("指令已过期，未执行: %s", command.command_id)
            await self._emit(
                self._result(
                    command,
                    "expired",
                    error_code="expired",
                    error_message="指令已超过有效期，不再表演旧弹幕",
                )
            )
            return "expired"

        await self._emit(self._result(command, "accepted"))
        high_priority = command.plan.priority == "high" or command.plan.interrupt
        if high_priority:
            await self._preempt()
            self._items.appendleft(command)
        else:
            self._drop_overflow()
            self._items.append(command)
        self._stats["accepted"] += 1
        self._wake.set()
        return "queued"

    def _drop_overflow(self) -> None:
        if len(self._items) < self.max_queue:
            return
        victim = self._items.popleft()
        self._stats["queue_dropped"] += 1
        logger.info("本地队列已满，丢弃最旧指令: %s", victim.command_id)
        asyncio.create_task(
            self._emit(
                self._result(
                    victim,
                    "expired",
                    error_code="queue_overflow",
                    error_message="本地队列已满，指令未执行",
                )
            ),
            name=f"drop-{victim.command_id}",
        )

    async def _preempt(self) -> None:
        task = self._current_task
        if task is None or task.done():
            await self.executor.interrupt()
            return
        logger.info("高优先级指令抢占当前表演")
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await self.executor.interrupt()

    # ------------------------------------------------------------------ worker
    async def _worker_loop(self) -> None:
        while not self._closing:
            await self._wake.wait()
            while self._items and not self._closing:
                command = self._items.popleft()
                if not self._items:
                    self._wake.clear()
                await self._run(command)

    async def _run(self, command: PerformCommand) -> None:
        if command.is_expired(grace_s=self.grace_s):
            self._stats["expired"] += 1
            await self._emit(
                self._result(
                    command,
                    "expired",
                    error_code="expired",
                    error_message="排队期间指令已过期",
                )
            )
            return
        self._current_command = command
        task = asyncio.create_task(
            self._execute(command), name=f"perform-{command.command_id}"
        )
        self._current_task = task
        try:
            result = await task
        except asyncio.CancelledError:
            self._current_task = None
            self._current_command = None
            if self._closing:
                raise
            self._stats["interrupted"] += 1
            await self._emit(
                self._result(
                    command,
                    "interrupted",
                    error_code="interrupted",
                    error_message="被更高优先级指令或主播打断",
                )
            )
            return
        except Exception as exc:  # noqa: BLE001 - never kill the dispatcher
            self._last_error = str(exc)
            logger.exception("执行指令失败")
            self._stats["errors"] += 1
            await self._emit(
                self._result(
                    command,
                    "error",
                    error_code="dispatcher_error",
                    error_message=self._last_error,
                )
            )
            return
        finally:
            self._current_task = None
            self._current_command = None
        if result.status == "completed":
            self._stats["completed"] += 1
        elif result.status == "interrupted":
            self._stats["interrupted"] += 1
        elif result.status == "expired":
            self._stats["expired"] += 1
        elif result.status == "error":
            self._stats["errors"] += 1
        await self._emit(result)

    async def _execute(self, command: PerformCommand) -> PerformResult:
        async def on_started(speech_ms: int) -> None:
            self._stats["started"] += 1
            await self._emit(self._result(command, "started", speech_ms=speech_ms))

        return await self.executor.perform(command, on_started=on_started)

    # --------------------------------------------------------------- interrupt
    async def handle_interrupt(self, reason: str = "云端打断当前表演") -> None:
        logger.info("收到打断指令: %s", reason)
        task = self._current_task
        victims = list(self._items)
        self._items.clear()
        self._wake.clear()
        for victim in victims:
            await self._emit(
                self._result(
                    victim,
                    "interrupted",
                    error_code="operator_stop",
                    error_message=reason,
                )
            )
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self.executor.interrupt()

    # ------------------------------------------------------------------ helpers
    def _result(
        self,
        command: PerformCommand,
        status: str,
        *,
        speech_ms: int = 0,
        error_code: str = "",
        error_message: str = "",
    ) -> PerformResult:
        return PerformResult(
            command_id=command.command_id,
            room_id=command.room_id,
            status=status,
            speech_ms=speech_ms,
            error_code=error_code,
            error_message=error_message,
            client_id=self.client_id,
        )

    async def _emit(self, result: PerformResult) -> None:
        self._recent_results.appendleft(result)
        try:
            await self._send_result(result)
        except Exception as exc:  # noqa: BLE001 - offline results are queued by caller
            self._last_error = f"回执发送失败：{exc}"
            logger.warning(self._last_error)

    # ------------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        current = self._current_command
        last = self._recent_results[0] if self._recent_results else None
        return {
            "current": {
                "command_id": current.command_id,
                "room_id": current.room_id,
                "reply": current.plan.reply,
                "motion": current.plan.motion,
                "expires_at": current.expires_at,
            }
            if current
            else None,
            "queue": [
                {
                    "command_id": item.command_id,
                    "reply": item.plan.reply,
                    "priority": item.plan.priority,
                }
                for item in list(self._items)[:8]
            ],
            "stats": dict(self._stats),
            "last_result": last.to_dict() if last else None,
            "recent_results": [item.to_dict() for item in list(self._recent_results)[:8]],
            "last_error": self._last_error,
            "dedup_size": len(self._dedup),
            "running": self._worker is not None and not self._worker.done(),
            "now": time.time(),
        }
