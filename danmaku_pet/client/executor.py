"""Local execution of one :class:`PerformCommand`.

The client owns speech timing: EdgeTTS renders the reply, pygame measures the
real duration, and the action holds for ``max(motion minimum, speech + 300ms)``.
The cloud deliberately does not know those numbers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from danmaku_pet.broadcaster import Broadcaster
from danmaku_pet.config import Config
from danmaku_pet.execution.action_adapter import ActionAdapter
from danmaku_pet.execution.backend import LocalPerformanceBackend
from danmaku_pet.execution.tts import EdgeTTSService, estimate_duration_ms
from danmaku_pet.models import AudioClip
from danmaku_pet.shared.commands import PerformCommand, PerformResult, speech_text_for

logger = logging.getLogger(__name__)

StartedCallback = Callable[[int], Awaitable[None]]


class LocalExecutor:
    def __init__(
        self,
        config: Config,
        backend: LocalPerformanceBackend,
        tts: EdgeTTSService,
        adapter: ActionAdapter,
        broadcaster: Broadcaster,
    ) -> None:
        self.config = config
        self.backend = backend
        self.tts = tts
        self.adapter = adapter
        self.broadcaster = broadcaster
        self.current_command: PerformCommand | None = None
        self.last_speech_text = ""
        self.last_audio: AudioClip | None = None
        self.last_error = ""
        self.performed = 0

    async def start(self) -> None:
        await self.backend.start()
        prepare = getattr(self.backend.player, "prepare", None)
        if callable(prepare):
            try:
                prepare()
            except Exception:
                logger.warning("预热音频设备失败", exc_info=True)

    async def shutdown(self) -> None:
        await self.backend.shutdown()

    async def interrupt(self) -> None:
        self.backend.player.stop()
        await self.backend.native.interrupt()

    # ------------------------------------------------------------------ perform
    async def perform(
        self,
        command: PerformCommand,
        *,
        on_started: StartedCallback | None = None,
    ) -> PerformResult:
        self.current_command = command
        plan = command.plan
        event = command.event
        speech_text = speech_text_for(event, plan, command.read_aloud)
        self.last_speech_text = speech_text
        self.last_error = ""
        error_code = ""
        error_message = ""

        audio = await self.tts.synthesize(speech_text)
        measured = self.backend.player.measure(audio.path)
        if measured:
            audio.duration_ms = measured
        elif not audio.duration_ms:
            audio.duration_ms = estimate_duration_ms(speech_text)
        self.last_audio = audio
        if audio.error:
            error_code = "tts_failed"
            error_message = audio.error
            logger.warning("TTS 失败，执行静音动作: %s", audio.error)
            await self.broadcaster.send(
                "notice",
                {
                    "level": "warning",
                    "message": f"TTS 不可用，已用静音动作继续：{audio.error}",
                },
            )

        cue = self.adapter.build_cue(
            plan,
            event,
            audio.duration_ms,
            min_hold_override=plan.min_hold_ms,
        )
        if cue.speech_ms and not audio.path:
            await self.broadcaster.send(
                "notice",
                {
                    "level": "warning",
                    "message": "音频文件缺失，动作按时长估算执行",
                },
            )
        if on_started is not None:
            try:
                await on_started(cue.speech_ms)
            except Exception:
                logger.warning("发送 started 回执失败", exc_info=True)

        started = time.perf_counter()
        try:
            outcome = await self.backend.perform(plan, event, audio, cue)
        except asyncio.CancelledError:
            self.current_command = None
            raise
        except Exception as exc:  # noqa: BLE001 - one command must not kill the client
            self.last_error = str(exc)
            logger.exception("本地执行异常")
            self.current_command = None
            return PerformResult(
                command_id=command.command_id,
                room_id=command.room_id,
                status="error",
                speech_ms=cue.speech_ms,
                error_code="executor_error",
                error_message=self.last_error,
                client_id=self.config.client_id,
            )
        finally:
            logger.info(
                "本地执行耗时 %.0fms command=%s",
                (time.perf_counter() - started) * 1000.0,
                command.command_id,
            )

        status = outcome.status
        if status == "error":
            error_code = error_code or "visual_error"
            error_message = outcome.error or error_message
            self.last_error = error_message
        if status == "completed":
            self.performed += 1
        self.current_command = None
        return PerformResult(
            command_id=command.command_id,
            room_id=command.room_id,
            status=status,
            speech_ms=cue.speech_ms,
            error_code=error_code,
            error_message=error_message,
            client_id=self.config.client_id,
        )

    # ------------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        command = self.current_command
        return {
            "current_command": command.to_dict() if command else None,
            "last_speech_text": self.last_speech_text,
            "last_audio": {
                "path": str(self.last_audio.path) if self.last_audio and self.last_audio.path else "",
                "duration_ms": self.last_audio.duration_ms if self.last_audio else 0,
                "cached": self.last_audio.cached if self.last_audio else False,
                "error": self.last_audio.error if self.last_audio else None,
            },
            "last_error": self.last_error,
            "performed": self.performed,
            "tts": self.tts.status(),
            "backend": self.backend.status(),
        }
