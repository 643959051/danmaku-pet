"""Local performance backend: native desktop pet + TTS playback + UI preview.

This module runs **only** on the 开播电脑.  It receives a validated
:class:`ActionPlan` (already turned into a command by the cloud) and performs
it; it never decides anything and never talks to DeepSeek.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from danmaku_pet.broadcaster import Broadcaster
from danmaku_pet.config import Config
from danmaku_pet.execution.action_adapter import ActionAdapter
from danmaku_pet.execution.dafeiyu import DafeiyuNativeBackend
from danmaku_pet.execution.tts import AudioPlayer
from danmaku_pet.models import ActionPlan, AudioClip, Event, VisualCue

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PerformanceOutcome:
    status: Literal["completed", "interrupted", "error"]
    error: str = ""


class LocalPerformanceBackend:
    def __init__(
        self,
        config: Config,
        native: DafeiyuNativeBackend,
        player: AudioPlayer,
        broadcaster: Broadcaster,
        adapter: ActionAdapter,
    ) -> None:
        self.config = config
        self.native = native
        self.player = player
        self.broadcaster = broadcaster
        self.adapter = adapter
        self._active_event_id: str | None = None
        self._interrupted = False
        self._started_at: float | None = None
        self._last_error: str | None = None

    async def start(self) -> None:
        await self.native.start()

    async def _media_url(self, path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            relative = path.resolve().relative_to(self.config.assets_dir.resolve())
        except ValueError:
            return None
        return "/media/" + relative.as_posix()

    async def perform(
        self,
        plan: ActionPlan,
        event: Event,
        audio: AudioClip,
        cue: VisualCue,
    ) -> PerformanceOutcome:
        self._active_event_id = event.event_id
        self._interrupted = False
        self._started_at = time.time()
        media_url = await self._media_url(audio.path)
        await self.broadcaster.send(
            "visual",
            {
                "event_id": event.event_id,
                "user": event.user,
                "text": event.display_text,
                "reply": plan.reply,
                "plan": plan.to_dict(),
                "cue": cue.to_dict(),
                "audio_url": media_url,
            },
        )
        await self.broadcaster.send(
            "performance",
            {
                "phase": "start",
                "event_id": event.event_id,
                "duration_ms": cue.hold_ms,
                "speech_ms": cue.speech_ms,
                "audio_url": media_url,
                "audio_text": audio.text,
                "motion": plan.motion,
                "emotion": plan.emotion,
                "mode": cue.mode,
                "started_at": self._started_at,
            },
        )
        await self.native.show(plan, event, cue)
        played = self.player.play(audio.path)
        logger.info(
            "perform motion=%s emotion=%s speech=%sms hold=%sms native=%s audio=%s",
            plan.motion,
            plan.emotion,
            cue.speech_ms,
            cue.hold_ms,
            self.native.status()["state"],
            played,
        )
        try:
            await asyncio.sleep(max(0.25, cue.hold_ms / 1000))
            await self.native.idle()
            await self.broadcaster.send(
                "performance",
                {
                    "phase": "end",
                    "event_id": event.event_id,
                    "reason": "complete",
                    "ended_at": time.time(),
                },
            )
            return PerformanceOutcome("completed")
        except asyncio.CancelledError:
            self.player.stop()
            await self.native.interrupt()
            await self.broadcaster.send(
                "performance",
                {
                    "phase": "end",
                    "event_id": event.event_id,
                    "reason": "interrupted",
                    "ended_at": time.time(),
                },
            )
            raise
        except Exception as exc:
            self.player.stop()
            await self.native.interrupt()
            self._last_error = str(exc)
            logger.exception("执行动作失败: %s", exc)
            await self.broadcaster.send(
                "performance",
                {
                    "phase": "end",
                    "event_id": event.event_id,
                    "reason": "error",
                    "error": str(exc),
                    "ended_at": time.time(),
                },
            )
            return PerformanceOutcome("error", str(exc))
        finally:
            self._active_event_id = None

    async def interrupt(self) -> None:
        self._interrupted = True
        self.player.stop()
        await self.native.interrupt()

    async def shutdown(self) -> None:
        self.player.stop()
        self.player.close()
        await self.native.shutdown()

    def status(self) -> dict[str, Any]:
        return {
            "desktop_pet": self.native.status(),
            "audio": self.player.status(),
            "active_event_id": self._active_event_id,
            "started_at": self._started_at,
            "last_error": self._last_error,
        }


# Backwards-compatible name for older imports/scripts.
PerformanceBackend = LocalPerformanceBackend
