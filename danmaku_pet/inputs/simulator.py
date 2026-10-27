"""Local simulated danmaku loop (no Douyin connection in the MVP)."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

from danmaku_pet.models import Event

logger = logging.getLogger(__name__)

Submit = Callable[[Event], Awaitable[None]]

CHAT_LINES: tuple[str, ...] = (
    "小鱼今天怎么这么可爱",
    "主播会跳个舞吗",
    "你好呀，第一次来",
    "给我讲个冷笑话",
    "刚才那个动作好好笑",
    "今天心情有点难过，陪我聊聊",
    "你转一圈我看看",
    "这也太厉害了吧",
    "小鱼喜欢吃什么",
    "来个害羞的表情",
)

USERS: tuple[str, ...] = ("小满", "阿星", "橘子汽水", "夜航船", "momo", "不吃香菜")

GIFTS: tuple[tuple[str, int], ...] = (
    ("小心心", 1),
    ("玫瑰", 3),
    ("棒棒糖", 2),
    ("啤酒", 1),
    ("墨镜", 1),
    ("跑车", 1),
    ("火箭", 1),
    ("嘉年华", 1),
)

SCENARIOS = ("mixed", "chat", "gift")


class Simulator:
    def __init__(self, submit: Submit) -> None:
        self._submit = submit
        self._task: asyncio.Task[None] | None = None
        self.interval = 4.0
        self.scenario = "mixed"
        self.started_at: float | None = None
        self.sent = 0
        self._counter = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, interval_seconds: float, scenario: str = "mixed") -> None:
        await self.stop()
        self.interval = max(0.8, min(60.0, float(interval_seconds)))
        self.scenario = scenario if scenario in SCENARIOS else "mixed"
        self.started_at = time.time()
        self.sent = 0
        self._counter = 0
        self._task = asyncio.create_task(self._run(), name="danmaku-simulator")
        logger.info("模拟弹幕循环启动: interval=%.1fs scenario=%s", self.interval, self.scenario)

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.started_at = None

    def _next_event(self) -> Event:
        self._counter += 1
        make_gift = self.scenario == "gift" or (
            self.scenario == "mixed" and self._counter % 4 == 0
        )
        user = random.choice(USERS)
        if make_gift:
            gift, count = random.choice(GIFTS)
            return Event(
                source="gift_sim",
                user=user,
                text=f"送出 {gift}" + (f" × {count}" if count > 1 else ""),
                gift_name=gift,
                gift_count=count,
                timestamp=time.time(),
            )
        return Event(
            source="danmaku_sim",
            user=user,
            text=random.choice(CHAT_LINES),
            timestamp=time.time(),
        )

    async def _run(self) -> None:
        try:
            while True:
                event = self._next_event()
                try:
                    await self._submit(event)
                    self.sent += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("模拟消息提交失败: %s", exc)
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            raise
        finally:
            self.started_at = None

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "interval_seconds": self.interval,
            "scenario": self.scenario,
            "sent": self.sent,
            "started_at": self.started_at,
            "available_scenarios": list(SCENARIOS),
        }
