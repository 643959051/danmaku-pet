"""Per-stage counters and latency observations (改造计划 阶段 7)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

STAGES = ("decision", "dispatch", "gateway_send", "client_start", "performance")


@dataclass(slots=True)
class StageStats:
    count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    last_ms: float = 0.0

    def observe(self, ms: float) -> None:
        value = max(0.0, float(ms))
        self.count += 1
        self.total_ms += value
        self.max_ms = max(self.max_ms, value)
        self.last_ms = value

    def snapshot(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "avg_ms": round(self.total_ms / self.count, 1) if self.count else 0.0,
            "max_ms": round(self.max_ms, 1),
            "last_ms": round(self.last_ms, 1),
        }


@dataclass(slots=True)
class StageMetrics:
    counters: dict[str, int] = field(default_factory=dict)
    stages: dict[str, StageStats] = field(
        default_factory=lambda: {name: StageStats() for name in STAGES}
    )
    started_at: float = field(default_factory=time.time)

    def inc(self, name: str, amount: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + int(amount)

    def observe(self, stage: str, ms: float) -> None:
        self.stages.setdefault(stage, StageStats()).observe(ms)

    def observe_now(self, stage: str, started_at: float) -> None:
        """``observe`` in seconds, measured from a ``time.perf_counter()`` mark."""

        self.observe(stage, (time.perf_counter() - started_at) * 1000.0)

    def snapshot(self) -> dict[str, Any]:
        return {
            "uptime_s": round(time.time() - self.started_at, 1),
            "counters": dict(sorted(self.counters.items())),
            "stages": {name: stats.snapshot() for name, stats in self.stages.items()},
        }
