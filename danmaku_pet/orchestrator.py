"""Three-stage orchestrator: queue -> decision -> performance."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

from danmaku_pet.broadcaster import Broadcaster
from danmaku_pet.config import Config
from danmaku_pet.decision.context import ConversationMemory
from danmaku_pet.decision.gifts import decide_gift
from danmaku_pet.decision.llm import DecisionEngine
from danmaku_pet.decision.validator import fallback_plan
from danmaku_pet.execution.action_adapter import ActionAdapter
from danmaku_pet.execution.backend import PerformanceBackend
from danmaku_pet.execution.tts import EdgeTTSService, estimate_duration_ms
from danmaku_pet.models import DecisionResult, Event, LogEntry
from danmaku_pet.queueing import DropInfo, EventQueue

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        config: Config,
        broadcaster: Broadcaster,
        engine: DecisionEngine,
        tts: EdgeTTSService,
        backend: PerformanceBackend,
        adapter: ActionAdapter,
    ) -> None:
        self.config = config
        self.broadcaster = broadcaster
        self.engine = engine
        self.tts = tts
        self.backend = backend
        self.adapter = adapter
        self.memory = ConversationMemory(config.max_context_rounds)
        self.queue = EventQueue(maxsize=config.queue_max, stale_after_s=config.stale_after_s)
        self._worker: asyncio.Task[None] | None = None
        self._current: asyncio.Task[None] | None = None
        self._engine_lock = asyncio.Lock()
        self._closing = False
        self.started_at = time.time()
        self.current_entry: LogEntry | None = None
        self.last_danmaku: Event | None = None
        self.pending: deque[Event] = deque(maxlen=30)
        self.recent: deque[LogEntry] = deque(maxlen=40)
        self.stats: dict[str, float | int] = {
            "received": 0,
            "completed": 0,
            "interrupted": 0,
            "errors": 0,
            "dropped": 0,
            "deepseek_latency_ms_total": 0,
            "speech_ms_total": 0,
        }

    async def start(self) -> None:
        await self.backend.start()
        self._worker = asyncio.create_task(self._worker_loop(), name="message-worker")
        logger.info("消息 worker 已启动")

    # ------------------------------------------------------------------ submit
    async def submit(self, event: Event, *, read_aloud: bool = False) -> None:
        """Explicit submission (manual box / gift): always process it."""

        if self._closing:
            raise RuntimeError("服务正在关闭")
        self.stats["received"] = int(self.stats["received"]) + 1
        if not event.is_gift:
            self.last_danmaku = event
        await self._enqueue(event, read_aloud=read_aloud)

    async def ingest(self, event: Event) -> None:
        """Incoming danmaku path.

        Auto-reply OFF: keep it in the pending inbox and do not call the LLM.
        Auto-reply ON: process immediately, reading the danmaku before the reply.
        Gifts always use the high-priority script path, independent of this switch.
        """

        if event.is_gift:
            await self.submit(event)
            return
        if self._closing:
            raise RuntimeError("服务正在关闭")
        self.stats["received"] = int(self.stats["received"]) + 1
        self.last_danmaku = event
        if getattr(self.config, "auto_reply", False):
            await self._enqueue(event, read_aloud=True, count_received=False)
            return
        self._add_pending(event)
        await self.broadcaster.send("event", event.to_dict())
        await self._broadcast_pending()
        await self._broadcast_status()
        logger.info("弹幕进入待回复列表（自动回复关闭）: %s", event.event_id)

    async def _enqueue(
        self,
        event: Event,
        *,
        read_aloud: bool,
        count_received: bool = True,
    ) -> None:
        if count_received:
            self.stats["received"] = int(self.stats["received"]) + 1
            if not event.is_gift:
                self.last_danmaku = event
        self._remove_pending(event.event_id)
        dropped: list[DropInfo] = []
        if event.is_gift:
            await self._cancel_current("高优先级礼物打断当前表演")
            dropped.extend(await self.queue.put(event, preempt=True))
        else:
            dropped.extend(await self.queue.put(event, read_aloud=read_aloud))
        await self._broadcast_drops(dropped)
        await self._broadcast_pending()
        await self._broadcast_status()

    async def interrupt_current(self, reason: str = "手动打断") -> None:
        await self._cancel_current(reason)
        await self.backend.interrupt()
        await self.broadcaster.send("notice", {"level": "info", "message": reason})
        await self._broadcast_status()

    def find_danmaku(self, event_id: str | None = None) -> Event | None:
        """Find a normal danmaku by id, newest first, for manual re-replies."""

        if event_id:
            if self.last_danmaku and self.last_danmaku.event_id == event_id:
                return self.last_danmaku
            for item in self.pending:
                if item.event_id == event_id:
                    return item
            if self.current_entry and self.current_entry.event.event_id == event_id:
                if not self.current_entry.event.is_gift:
                    return self.current_entry.event
            for entry in self.recent:
                if entry.event.event_id == event_id and not entry.event.is_gift:
                    return entry.event
            return None
        if self.pending:
            return self.pending[-1]
        return self.last_danmaku

    async def reply_to(
        self,
        *,
        event_id: str | None = None,
        text: str | None = None,
        user: str | None = None,
    ) -> Event:
        """Create a fresh manual event that replies to a previous danmaku.

        This deliberately re-enters the normal three-stage pipeline, so it gets
        fresh context, TTS and a new action instead of replaying an old plan.
        """

        source = self.find_danmaku(event_id) if event_id else self.last_danmaku
        final_text = (text or (source.text if source else "")).strip()
        final_user = (user or (source.user if source else "观众")).strip() or "观众"
        if not final_text:
            raise LookupError("没有可回复的弹幕")
        if source is not None:
            self._remove_pending(source.event_id)
        event = Event(
            source="manual",
            user=final_user,
            text=final_text,
            timestamp=time.time(),
        )
        await self.submit(event, read_aloud=bool(getattr(self.config, "auto_reply", False)))
        return event

    async def swap_engine(self, new_engine: DecisionEngine) -> None:
        """Hot-swap the decision engine after settings were validated.

        The lock covers a single in-flight DeepSeek call; a settings save may
        wait for it instead of closing its HTTP client mid-request.
        """

        async with self._engine_lock:
            old_engine = self.engine
            self.engine = new_engine
            if old_engine is not new_engine:
                try:
                    await old_engine.close()
                except Exception:
                    logger.warning("关闭旧决策引擎失败", exc_info=True)

    async def _cancel_current(self, reason: str) -> None:
        task = self._current
        if task is None or task.done():
            return
        logger.info("取消当前表演: %s", reason)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("取消当前任务时出现异常")
        await self.backend.interrupt()

    async def _broadcast_drops(self, drops: list[DropInfo]) -> None:
        for drop in drops:
            self.stats["dropped"] = int(self.stats["dropped"]) + 1
            await self.broadcaster.send(
                "dropped",
                {
                    "event": drop.event.to_dict(),
                    "reason": drop.reason,
                },
            )
            logger.info("事件丢弃: %s (%s)", drop.event.event_id, drop.reason)

    def _add_pending(self, event: Event) -> None:
        self.pending = deque(
            (item for item in self.pending if item.event_id != event.event_id),
            maxlen=30,
        )
        self.pending.append(event)

    def _remove_pending(self, event_id: str) -> bool:
        if any(item.event_id == event_id for item in self.pending):
            self.pending = deque(
                (item for item in self.pending if item.event_id != event_id),
                maxlen=30,
            )
            return True
        return False

    def pending_snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "event": event.to_dict(),
                "waiting_seconds": round(event.age_seconds(time.time()), 1),
            }
            for event in reversed(self.pending)
        ]

    async def _broadcast_pending(self) -> None:
        await self.broadcaster.send(
            "pending",
            {
                "auto_reply": bool(getattr(self.config, "auto_reply", False)),
                "items": self.pending_snapshot(),
            },
        )

    async def set_auto_reply(self, enabled: bool) -> None:
        self.config.auto_reply = bool(enabled)
        await self._broadcast_pending()
        await self.broadcaster.send(
            "auto_reply",
            {"enabled": bool(enabled)},
        )
        await self._broadcast_status()

    # ------------------------------------------------------------------ worker
    async def _worker_loop(self) -> None:
        while True:
            queued, drops = await self.queue.get()
            event = queued.event
            await self._broadcast_drops(drops)
            task = asyncio.create_task(
                self._process(event, read_aloud=queued.read_aloud),
                name=f"process-{event.event_id}",
            )
            self._current = task
            try:
                await task
            except asyncio.CancelledError:
                if self._closing:
                    raise
                # A gift or manual interrupt cancelled this performance; the
                # worker itself stays alive and picks up the next event.
            except Exception:
                logger.exception("处理事件失败: %s", event.event_id)
            finally:
                if self._current is task:
                    self._current = None
                self.current_entry = None
                await self._broadcast_status()

    # --------------------------------------------------------------- decision
    async def _decide(self, event: Event) -> DecisionResult:
        if event.is_gift:
            return decide_gift(event)
        try:
            async with self._engine_lock:
                return await self.engine.decide(event, self.memory)
        except Exception as exc:
            logger.warning("LLM 决策失败，使用兜底动作: %s", exc)
            await self.broadcaster.send(
                "notice",
                {"level": "warning", "message": f"DeepSeek 决策失败，已兜底：{exc}"},
            )
            return DecisionResult(
                plan=fallback_plan(event),
                source="fallback",
                latency_ms=0,
                model="fallback",
                warnings=(str(exc),),
            )

    # --------------------------------------------------------------- process
    async def _process(self, event: Event, *, read_aloud: bool = False) -> None:
        entry = LogEntry(event=event, status="processing", read_aloud=read_aloud)
        self.current_entry = entry
        self.recent.appendleft(entry)
        await self.broadcaster.send("event", entry.event.to_dict())
        logger.info(
            "处理事件 %s source=%s user=%s text=%s read_aloud=%s",
            event.event_id,
            event.source,
            event.user,
            event.display_text,
            read_aloud,
        )
        try:
            decision = await self._decide(event)
            plan = decision.plan
            entry.plan = plan
            entry.decision_source = decision.source
            entry.latency_ms = decision.latency_ms
            self.stats["deepseek_latency_ms_total"] = int(
                self.stats["deepseek_latency_ms_total"]
            ) + max(0, decision.latency_ms)
            speech_text = plan.reply
            if read_aloud:
                heard = " ".join(event.display_text.split())
                if len(heard) > 180:
                    heard = heard[:179].rstrip() + "…"
                speech_text = f"{event.user}说：{heard}。{plan.reply}"
            await self.broadcaster.send(
                "plan",
                {
                    "event_id": event.event_id,
                    "plan": plan.to_dict(),
                    "decision_source": decision.source,
                    "model": decision.model,
                    "latency_ms": decision.latency_ms,
                    "warnings": list(decision.warnings),
                    "read_aloud": read_aloud,
                    "speech_text": speech_text,
                },
            )
            if decision.warnings:
                await self.broadcaster.send(
                    "notice",
                    {"level": "warning", "message": "；".join(decision.warnings)},
                )
            logger.info(
                "决策 %s: %s / %s / %s / interrupt=%s",
                decision.source,
                plan.motion,
                plan.emotion,
                plan.priority,
                plan.interrupt,
            )

            audio = await self.tts.synthesize(speech_text)
            measured = self.backend.player.measure(audio.path)
            if measured:
                audio.duration_ms = measured
            elif not audio.duration_ms:
                audio.duration_ms = estimate_duration_ms(speech_text)
            entry.audio_ms = audio.duration_ms
            if audio.error:
                await self.broadcaster.send(
                    "notice",
                    {"level": "warning", "message": f"TTS 不可用，已用静音动作：{audio.error}"},
                )

            override = 0
            if event.is_gift and decision.raw:
                override = int(decision.raw.get("min_hold_ms", 0) or 0)
            cue = self.adapter.build_cue(
                plan, event, audio.duration_ms, min_hold_override=override
            )
            await self.broadcaster.send(
                "entry",
                {"entry": entry.to_dict(), "event_id": event.event_id},
            )
            await self.backend.perform(plan, event, audio, cue)

            if not event.is_gift:
                self.memory.add(event.display_text, plan.reply)
            entry.status = "completed"
            self.stats["completed"] = int(self.stats["completed"]) + 1
            self.stats["speech_ms_total"] = int(self.stats["speech_ms_total"]) + max(
                0, audio.duration_ms
            )
            logger.info("事件 %s 表演完成", event.event_id)
        except asyncio.CancelledError:
            entry.status = "interrupted"
            entry.error = "被更高优先级事件打断"
            self.stats["interrupted"] = int(self.stats["interrupted"]) + 1
            await self.broadcaster.send(
                "notice",
                {"level": "info", "message": f"已打断：{event.display_text[:30]}"},
            )
            raise
        except Exception as exc:
            entry.status = "error"
            entry.error = str(exc)
            self.stats["errors"] = int(self.stats["errors"]) + 1
            logger.exception("事件 %s 执行失败", event.event_id)
            await self.broadcaster.send(
                "notice",
                {"level": "error", "message": f"执行失败：{exc}"},
            )
        finally:
            entry.ended_at = time.time()
            if self.current_entry is entry:
                self.current_entry = None
            await self.broadcaster.send(
                "entry",
                {"entry": entry.to_dict(), "event_id": event.event_id},
            )

    # --------------------------------------------------------------- utilities
    async def _broadcast_status(self) -> None:
        await self.broadcaster.send(
            "status",
            {
                "queue": self.queue.snapshot(),
                "stats": self.snapshot_stats(),
                "current": self.current_entry.to_dict() if self.current_entry else None,
                "memory_rounds": len(self.memory),
                "last_danmaku": self.last_danmaku.to_dict() if self.last_danmaku else None,
                "pending": self.pending_snapshot(),
                "auto_reply": bool(getattr(self.config, "auto_reply", False)),
                "backend": self.backend.status(),
            },
        )

    def snapshot_stats(self) -> dict[str, Any]:
        completed = max(1, int(self.stats["completed"]))
        return {
            **self.stats,
            "avg_latency_ms": round(
                float(self.stats["deepseek_latency_ms_total"]) / completed, 1
            ),
            "avg_speech_ms": round(float(self.stats["speech_ms_total"]) / completed, 1),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "queue": self.queue.snapshot(),
            "stats": self.snapshot_stats(),
            "current": self.current_entry.to_dict() if self.current_entry else None,
            "recent": [entry.to_dict() for entry in list(self.recent)[:20]],
            "last_danmaku": self.last_danmaku.to_dict() if self.last_danmaku else None,
            "pending": self.pending_snapshot(),
            "auto_reply": bool(getattr(self.config, "auto_reply", False)),
            "memory": self.memory.snapshot(),
            "backend": self.backend.status(),
        }

    def clear_memory(self) -> None:
        self.memory.clear()

    async def shutdown(self) -> None:
        self._closing = True
        if self._worker is not None:
            self._worker.cancel()
        await self._cancel_current("服务关闭")
        drops = await self.queue.drain("服务关闭")
        await self._broadcast_drops(drops)
        if self._worker is not None:
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        await self.backend.shutdown()
        await self.engine.close()
        logger.info("Orchestrator 已关闭")
