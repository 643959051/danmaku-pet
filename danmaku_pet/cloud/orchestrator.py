"""Stateless, request-scoped cloud orchestrator.

Every request follows the same path: load the room document from the state
store (Redis in production), decide, dispatch, save.  There is **no** in-process
worker, queue, waiter or prune task: if the instance is replaced between two
requests, the next request picks the room state up from Redis.

In-memory ``MemoryStateStore`` is only for local development/tests; production
requires Redis (see :class:`danmaku_pet.cloud.config.CloudConfig`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from typing import Any

from danmaku_pet.broadcaster import Broadcaster
from danmaku_pet.cloud.config import CloudConfig
from danmaku_pet.cloud.content_safety import ContentGuard
from danmaku_pet.cloud.gateway import GatewayManager
from danmaku_pet.cloud.metrics import StageMetrics
from danmaku_pet.cloud.sessions import SessionRegistry
from danmaku_pet.cloud.state import RoomState, StateStore
from danmaku_pet.decision.context import ConversationMemory
from danmaku_pet.decision.gifts import decide_gift
from danmaku_pet.decision.llm import DecisionEngine
from danmaku_pet.decision.validator import fallback_plan
from danmaku_pet.douyin.dedup import event_dedup_key
from danmaku_pet.models import DecisionResult, Event, LogEntry
from danmaku_pet.shared.commands import (
    TERMINAL_STATUSES,
    PerformCommand,
    PerformResult,
    speech_text_for,
)
from danmaku_pet.shared.plans import ActionPlan
from danmaku_pet.shared.protocol import make_command_id

logger = logging.getLogger(__name__)


class CloudOrchestrator:
    def __init__(
        self,
        config: CloudConfig,
        broadcaster: Broadcaster,
        engine: DecisionEngine,
        gateway: GatewayManager,
        sessions: SessionRegistry,
        guard: ContentGuard,
        metrics: StageMetrics,
        store: StateStore,
        content_security: Any | None = None,
    ) -> None:
        self.config = config
        self.broadcaster = broadcaster
        self.engine = engine
        self.gateway = gateway
        self.sessions = sessions
        self.guard = guard
        self.metrics = metrics
        self.store = store
        self.content_security = content_security
        self.started_at = time.time()
        self._started = False
        self._closing = False
        self._engine_lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._closing = False
        await self.store.start()
        await self.gateway.start()
        self._started = True
        logger.info(
            "云端编排器已启动: 网关=%s, LLM=%s, 状态=%s",
            self.config.gateway_mode,
            self.config.effective_llm_mode,
            self.store.backend,
        )

    async def shutdown(self) -> None:
        self._closing = True
        self._started = False
        await self.store.close()
        await self.gateway.close()
        await self.engine.close()
        logger.info("云端编排器已关闭")

    @property
    def alive(self) -> bool:
        return self._started and not self._closing

    async def healthy(self) -> bool:
        return bool(self.alive and await self.store.ping())

    async def swap_engine(self, new_engine: DecisionEngine) -> None:
        async with self._engine_lock:
            old_engine = self.engine
            self.engine = new_engine
            if old_engine is not new_engine:
                try:
                    await old_engine.close()
                except Exception:
                    logger.warning("关闭旧决策引擎失败", exc_info=True)

    async def _screen_content(
        self, text: str, user: str
    ) -> tuple[bool, str, bool]:
        """Platform text/name safety.

        Returns ``(allowed, reason, suppress_speech)``.  An unsafe result drops
        the whole event (so it can never reach TTS); an unavailable API is
        fail-closed by default, while fail-open still suppresses TTS.
        """

        if self.content_security is None:
            return True, "", False
        try:
            verdict = await self.content_security.check(text, user=user)
        except Exception as exc:  # noqa: BLE001 - safety path must not 500
            logger.warning("平台内容安全检测异常: %s", exc)
            verdict = None
        if verdict is None or getattr(verdict, "status", "") == "unavailable":
            if bool(
                getattr(self.config.douyin, "content_security_fail_closed", True)
            ):
                return False, "内容安全服务暂不可用，已保守丢弃该事件", True
            return True, "", True
        if getattr(verdict, "status", "") == "unsafe":
            fields = set(getattr(verdict, "unsafe_fields", ()) or ())
            labels = []
            if "user" in fields:
                labels.append("昵称")
            if "comment" in fields:
                labels.append("弹幕")
            suffix = "、".join(labels) or "内容"
            return (
                False,
                f"{suffix}未通过平台内容安全检测（谐音/变体也会命中），已丢弃",
                True,
            )
        return True, "", False

    # ------------------------------------------------------------------ ingress
    async def handle_event(self, event: Event) -> RoomState:
        if self._closing:
            raise RuntimeError("云端正在关闭")
        room_id = event.room_id or self.config.default_room_id
        if not room_id:
            raise ValueError("事件缺少 room_id，且没有配置 DEFAULT_ROOM_ID")
        if not event.room_id:
            event = replace(event, room_id=room_id)

        async with self.store.lock(room_id):
            state = await self.store.get_room(
                room_id, default_auto_reply=self.config.auto_reply
            )
            state.last_event_at = time.time()
            self.metrics.inc("events_received")

            if state.paused:
                await self._drop(state, event, "插件已暂停，新事件未处理")
                await self.store.save_room(state)
                return state

            state.stats["received"] += 1
            name_guard = self.guard.check_name(event.user)
            if not name_guard.allowed:
                await self._drop(
                    state, event, f"昵称未通过本地安全检查（{name_guard.reason}）"
                )
                await self.store.save_room(state)
                return state
            if name_guard.text != event.user:
                event = replace(event, user=name_guard.text)

            comment_guard = None
            if not event.is_gift:
                comment_guard = self.guard.check_comment(event.text)
                if not comment_guard.allowed:
                    await self._drop(
                        state,
                        event,
                        f"评论内容未通过安全检查（{comment_guard.reason}）",
                    )
                    await self.store.save_room(state)
                    return state
                if comment_guard.text != event.text:
                    event = replace(event, text=comment_guard.text)

            allowed, reason, suppress_speech = await self._screen_content(
                event.text, event.user
            )
            if not allowed:
                self.metrics.inc("content_security_blocked")
                await self._drop(state, event, reason)
                await self.store.save_room(state)
                return state

            if not event.is_gift:
                state.last_danmaku = event.to_dict()
                if comment_guard is not None and comment_guard.flagged:
                    self.metrics.inc("comments_flagged")
                    await self._notify(
                        room_id,
                        "warning",
                        f"评论疑似提示词注入，已按普通弹幕处理：{event.text[:30]}",
                    )

            if event.is_gift:
                await self._process(state, event, read_aloud=False)
            elif state.auto_reply:
                await self._process(
                    state, event, read_aloud=not suppress_speech
                )
            else:
                state.add_pending(event.to_dict())
                await self.broadcaster.send(
                    "event", {"room_id": room_id, "event": event.to_dict()}
                )
                await self._broadcast_pending(state)
                logger.info(
                    "房间 %s 弹幕进入待回复列表（自动回复关闭）: %s",
                    room_id,
                    event.event_id,
                )

            await self.store.save_room(state)
            await self._broadcast_status(state)
            return state

    async def handle_official_event(self, event: Event) -> bool:
        """Official push entry: message_id dedup in the external store."""

        key = f"msg:{event_dedup_key(event)}"
        if not await self.store.dedup(key, self.config.dedup_ttl_s):
            self.metrics.inc("messages_duplicate")
            logger.info("重复的平台消息已忽略: %s", event.effective_message_id)
            return False
        await self.handle_event(event)
        return True

    async def reply_to(
        self,
        room_id: str,
        *,
        event_id: str | None = None,
        text: str | None = None,
        user: str | None = None,
    ) -> Event:
        async with self.store.lock(room_id):
            state = await self.store.get_room(
                room_id, default_auto_reply=self.config.auto_reply
            )
            source = state.find_danmaku(event_id) if event_id else state.find_danmaku()
            final_text = (text or (source.get("text", "") if source else "")).strip()
            final_user = (user or (source.get("user", "") if source else "观众")).strip() or "观众"
            if not final_text:
                raise LookupError("没有可回复的弹幕")
            name_guard = self.guard.check_name(final_user)
            if not name_guard.allowed:
                raise ValueError("昵称未通过安全检查，已拒绝转为语音")
            final_user = name_guard.text
            allowed, reason, suppress_speech = await self._screen_content(
                final_text, final_user
            )
            if not allowed:
                raise ValueError(reason or "内容未通过平台内容安全检测")
            if source is not None:
                state.remove_pending(str(source.get("event_id") or ""))
            event = Event(
                source="manual",
                user=final_user,
                text=final_text,
                room_id=room_id,
                timestamp=time.time(),
                message_type="comment",
            )
            state.last_event_at = time.time()
            state.stats["received"] += 1
            await self._process(
                state, event, read_aloud=state.auto_reply and not suppress_speech
            )
            await self.store.save_room(state)
            await self._broadcast_status(state)
            return event

    # ------------------------------------------------------------------ decision
    async def decide(
        self, event: Event, memory: ConversationMemory
    ) -> DecisionResult:
        if event.is_gift:
            return decide_gift(event, gift_ids=self.config.gift_id_map)
        try:
            async with self._engine_lock:
                return await self.engine.decide(event, memory)
        except Exception as exc:  # noqa: BLE001 - degrade instead of failing the request
            logger.warning("LLM 决策失败，使用兜底动作: %s", exc)
            self.metrics.inc("decisions_fallback")
            await self.broadcaster.send(
                "notice",
                {
                    "room_id": event.room_id,
                    "level": "warning",
                    "message": f"DeepSeek 决策失败，已兜底：{exc}",
                },
            )
            return DecisionResult(
                plan=fallback_plan(event),
                source="fallback",
                latency_ms=0,
                model="fallback",
                warnings=(str(exc),),
            )

    # ------------------------------------------------------------------ process
    async def _process(
        self, state: RoomState, event: Event, *, read_aloud: bool
    ) -> None:
        entry = LogEntry(
            event=event,
            room_id=state.room_id,
            status="deciding",
            read_aloud=read_aloud,
        )
        state.add_entry(entry.to_dict())
        await self.broadcaster.send(
            "event", {"room_id": state.room_id, "event": event.to_dict()}
        )
        logger.info(
            "房间 %s 处理事件 %s source=%s read_aloud=%s%s",
            state.room_id,
            event.event_id,
            event.source,
            read_aloud,
            (
                f" user={event.user} text={event.display_text}"
                if self.config.log_viewer_data
                else "（观众昵称/文本已隐藏，LOG_VIEWER_DATA=false）"
            ),
        )
        try:
            memory = state.conversation(self.config.max_context_rounds)
            decision = await self.decide(event, memory)
            plan = self._guard_plan(decision.plan, entry, decision)
            entry.plan = plan
            entry.decision_source = decision.source
            entry.latency_ms = decision.latency_ms
            state.stats["decision_ms_total"] += max(0, decision.latency_ms)
            self.metrics.observe("decision", decision.latency_ms)

            now = time.time()
            ttl = (
                self.config.gift_expires_s
                if event.is_gift
                else self.config.comment_expires_s
            )
            command = PerformCommand(
                command_id=make_command_id(state.room_id, event),
                room_id=state.room_id,
                event=event,
                plan=plan,
                created_at=now,
                expires_at=now + ttl,
                read_aloud=read_aloud,
            )
            entry.command_id = command.command_id
            await self.broadcaster.send(
                "plan",
                {
                    "room_id": state.room_id,
                    "event_id": event.event_id,
                    "command_id": command.command_id,
                    "plan": plan.to_dict(),
                    "decision_source": decision.source,
                    "model": decision.model,
                    "latency_ms": decision.latency_ms,
                    "warnings": list(decision.warnings),
                    "read_aloud": read_aloud,
                    "speech_text": speech_text_for(event, plan, read_aloud),
                },
            )
            if decision.warnings:
                await self._notify(
                    state.room_id, "warning", "；".join(decision.warnings)
                )

            if not await self.store.dedup(
                f"cmd:{command.command_id}", self.config.dedup_ttl_s
            ):
                entry.status = "duplicate"
                entry.result_status = "duplicate"
                entry.error = "同一消息的指令已下发过，已忽略重复触发"
                state.stats["duplicates"] += 1
                self.metrics.inc("commands_deduplicated")
                await self._notify(
                    state.room_id, "warning", "检测到重复消息，已跳过（避免重复表演）"
                )
                return

            dispatch_started = time.perf_counter()
            sent = await self.gateway.send(command)
            entry.dispatched_at = time.time()
            self.metrics.observe_now("dispatch", dispatch_started)
            if not sent:
                entry.status = "error"
                entry.error = "指令下发失败：客户端未连接或网关拒绝"
                state.stats["errors"] += 1
                await self._notify(state.room_id, "error", entry.error)
                return

            entry.status = "dispatched"
            state.stats["dispatched"] += 1
            self.sessions.mark_command_sent(state.room_id)
            if not event.is_gift:
                state.add_memory(
                    event.display_text, plan.reply, self.config.max_context_rounds
                )
        except Exception as exc:  # noqa: BLE001 - one event must not 500 the request
            entry.status = "error"
            entry.error = str(exc)
            state.stats["errors"] += 1
            logger.exception("房间 %s 事件 %s 执行失败", state.room_id, event.event_id)
            await self._notify(state.room_id, "error", f"执行失败：{exc}")
        finally:
            entry.ended_at = time.time()
            self._replace_entry(state, entry)
            await self.broadcaster.send(
                "entry", {"room_id": state.room_id, "entry": entry.to_dict()}
            )

    def _replace_entry(self, state: RoomState, entry: LogEntry) -> None:
        data = entry.to_dict()
        for index, item in enumerate(state.recent):
            event = item.get("event") if isinstance(item, dict) else None
            if isinstance(event, dict) and event.get("event_id") == entry.event.event_id:
                state.recent[index] = data
                return
        state.add_entry(data)

    def _guard_plan(
        self, plan: ActionPlan, entry: LogEntry, decision: DecisionResult
    ) -> ActionPlan:
        verdict = self.guard.check_reply(plan.reply)
        if verdict.allowed:
            if verdict.text == plan.reply:
                return plan
            self.metrics.inc("replies_truncated")
            decision.warnings = (*decision.warnings, "回复超长，已截断")
            return plan.model_copy(update={"reply": verdict.text})

        replacement = verdict.text or self.guard.fallback_reply
        entry.error = f"回复内容被安全规则替换：{verdict.reason}"
        self.metrics.inc("replies_sanitized")
        decision.warnings = (*decision.warnings, f"回复内容已调整（{verdict.reason}）")
        return plan.model_copy(update={"reply": replacement})

    # ------------------------------------------------------------------ results
    async def handle_result(self, result: PerformResult) -> bool:
        self.metrics.inc("results_received")
        self.metrics.inc(f"result_{result.status}")
        if result.client_id:
            self.sessions.touch(result.client_id, result_status=result.status)

        handled = False
        async with self.store.lock(result.room_id):
            state = await self.store.get_room(
                result.room_id, default_auto_reply=self.config.auto_reply
            )
            entry = state.find_entry(result.command_id)
            if entry is not None:
                handled = True
                previous = str(entry.get("status") or "")
                entry["result_status"] = result.status
                entry["speech_ms"] = result.speech_ms or entry.get("speech_ms", 0)
                if (
                    result.status == "started"
                    and previous not in TERMINAL_STATUSES
                ):
                    entry["status"] = "started"
                    dispatched_at = float(entry.get("dispatched_at") or 0)
                    if dispatched_at:
                        self.metrics.observe(
                            "client_start", (time.time() - dispatched_at) * 1000.0
                        )
                if result.terminal and previous not in TERMINAL_STATUSES:
                    entry["status"] = result.status
                    if result.status == "completed":
                        state.stats["completed"] += 1
                        state.stats["speech_ms_total"] += max(0, result.speech_ms)
                        self.metrics.observe("performance", result.speech_ms)
                    elif result.status == "interrupted":
                        state.stats["interrupted"] += 1
                    elif result.status == "expired":
                        state.stats["timeouts"] += 1
                    elif result.status == "error":
                        state.stats["errors"] += 1
                        entry["error"] = (
                            result.error_message or result.error_code or "客户端执行失败"
                        )
                state.updated_at = time.time()
            await self.store.save_room(state)
            await self._broadcast_status(state)
        await self.broadcaster.send(
            "result",
            {
                "room_id": result.room_id,
                "result": result.to_dict(),
                "handled": handled,
            },
        )
        if not handled:
            logger.info("收到未知指令的回执: %s", result.command_id)
        return handled

    # ------------------------------------------------------------------ control
    async def interrupt(self, room_id: str, reason: str = "主播手动打断") -> bool:
        sent = await self.gateway.interrupt(room_id, reason)
        async with self.store.lock(room_id):
            state = await self.store.get_room(
                room_id, default_auto_reply=self.config.auto_reply
            )
            current = state.current_entry()
            if current is not None and current.get("status") not in TERMINAL_STATUSES:
                current["status"] = "interrupted"
                current["result_status"] = "interrupted"
                current["error"] = reason
                state.stats["interrupted"] += 1
                state.updated_at = time.time()
                await self.store.save_room(state)
                await self._broadcast_status(state)
        await self.broadcaster.send(
            "notice", {"room_id": room_id, "level": "info", "message": reason}
        )
        return sent

    async def set_paused(self, room_id: str, paused: bool) -> None:
        async with self.store.lock(room_id):
            state = await self.store.get_room(
                room_id, default_auto_reply=self.config.auto_reply
            )
            state.paused = bool(paused)
            state.updated_at = time.time()
            await self.store.save_room(state)
            await self._broadcast_status(state)
        if paused:
            await self.gateway.interrupt(room_id, "主播暂停插件")
            await self._notify(room_id, "info", "插件已暂停：新弹幕不再自动处理")
        else:
            await self._notify(room_id, "info", "插件已恢复")

    async def set_auto_reply(self, room_id: str | None, enabled: bool) -> None:
        if room_id:
            async with self.store.lock(room_id):
                state = await self.store.get_room(
                    room_id, default_auto_reply=self.config.auto_reply
                )
                state.auto_reply = bool(enabled)
                state.updated_at = time.time()
                await self.store.save_room(state)
                await self._broadcast_pending(state)
                await self._broadcast_status(state)
        else:
            self.config.auto_reply = bool(enabled)
            for state in await self.store.list_rooms():
                async with self.store.lock(state.room_id):
                    state.auto_reply = bool(enabled)
                    await self.store.save_room(state)
                    await self._broadcast_pending(state)
        await self.broadcaster.send(
            "auto_reply", {"room_id": room_id or "*", "enabled": bool(enabled)}
        )

    async def clear_context(self, room_id: str) -> bool:
        async with self.store.lock(room_id):
            states = await self.store.list_rooms()
            state = next((item for item in states if item.room_id == room_id), None)
            if state is None:
                return False
            state.memory = []
            state.updated_at = time.time()
            await self.store.save_room(state)
            await self._broadcast_status(state)
        await self._notify(room_id, "info", "上下文已清空")
        return True

    # ------------------------------------------------------------------ output
    async def _drop(self, state: RoomState, event: Event, reason: str) -> None:
        state.stats["dropped"] += 1
        self.metrics.inc("events_dropped")
        await self.broadcaster.send(
            "dropped",
            {
                "room_id": state.room_id,
                "event": event.to_dict(),
                "reason": reason,
            },
        )
        logger.info("房间 %s 丢弃事件 %s: %s", state.room_id, event.event_id, reason)

    async def _notify(self, room_id: str, level: str, message: str) -> None:
        await self.broadcaster.send(
            "notice", {"room_id": room_id, "level": level, "message": message}
        )

    async def _broadcast_pending(self, state: RoomState) -> None:
        await self.broadcaster.send(
            "pending",
            {
                "room_id": state.room_id,
                "auto_reply": state.auto_reply,
                "items": state.pending_snapshot(),
            },
        )

    async def _broadcast_status(self, state: RoomState) -> None:
        await self.broadcaster.send(
            "status",
            {
                "room_id": state.room_id,
                "room": state.snapshot(
                    [session.snapshot() for session in self.sessions.by_room(state.room_id)],
                    queue_max=self.config.queue_max,
                ),
            },
        )

    # ------------------------------------------------------------------ status
    async def room_state(self, room_id: str) -> RoomState | None:
        for state in await self.store.list_rooms():
            if state.room_id == room_id:
                return state
        return None

    async def room_snapshot(self, room_id: str) -> dict[str, Any] | None:
        state = await self.room_state(room_id)
        if state is None:
            return None
        return state.snapshot(
            [session.snapshot() for session in self.sessions.by_room(room_id)],
            queue_max=self.config.queue_max,
        )

    async def snapshot(self) -> dict[str, Any]:
        rooms = []
        for state in await self.store.list_rooms():
            rooms.append(
                state.snapshot(
                    [
                        session.snapshot()
                        for session in self.sessions.by_room(state.room_id)
                    ],
                    queue_max=self.config.queue_max,
                )
            )
        return {
            "started_at": self.started_at,
            "now": time.time(),
            "state_backend": self.store.backend,
            "rooms": rooms,
            "sessions": self.sessions.snapshot(),
            "gateway": self.gateway.status(),
            "metrics": self.metrics.snapshot(),
            "auto_reply_default": self.config.auto_reply,
            "gateway_mode": self.config.gateway_mode,
        }

    async def room_count(self) -> int:
        return len(await self.store.list_rooms())
