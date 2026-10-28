from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from danmaku_pet.broadcaster import Broadcaster
from danmaku_pet.client.executor import LocalExecutor
from danmaku_pet.execution.action_adapter import ActionAdapter
from danmaku_pet.execution.backend import PerformanceOutcome
from danmaku_pet.models import AudioClip
from tests.support import make_command, make_event, make_plan


class FakePlayer:
    def __init__(self, measured: int | None = 900) -> None:
        self.measured = measured
        self.stopped = 0

    def measure(self, path) -> int | None:
        return self.measured

    def stop(self) -> None:
        self.stopped += 1


class FakeBackend:
    def __init__(self, *, outcome: PerformanceOutcome | None = None, cancel: bool = False, boom: bool = False) -> None:
        self.player = FakePlayer()
        self.outcome = outcome or PerformanceOutcome("completed")
        self.cancel = cancel
        self.boom = boom
        self.calls: list[tuple] = []

    async def perform(self, plan, event, audio, cue):
        self.calls.append((plan, event, audio, cue))
        if self.cancel:
            raise asyncio.CancelledError
        if self.boom:
            raise RuntimeError("native helper crashed")
        return self.outcome

    def status(self) -> dict:
        return {"desktop_pet": {"state": "ready"}, "audio": {"mixer_ready": True}}


class FakeTTS:
    def __init__(self, clip: AudioClip) -> None:
        self.clip = clip
        self.texts: list[str] = []

    async def synthesize(self, text: str) -> AudioClip:
        self.texts.append(text)
        return self.clip

    def status(self) -> dict:
        return {"engine": "fake", "last_error": None}


def build(backend=None, tts=None):
    config = SimpleNamespace(client_id="test-client")
    backend = backend or FakeBackend()
    tts = tts or FakeTTS(AudioClip(path=None, duration_ms=900, text="好呀"))
    executor = LocalExecutor(
        config,  # type: ignore[arg-type]
        backend,  # type: ignore[arg-type]
        tts,  # type: ignore[arg-type]
        ActionAdapter(),
        Broadcaster(),
    )
    return executor, backend, tts


async def test_completed_performance_reports_speech_and_hold() -> None:
    executor, backend, tts = build()
    started: list[int] = []

    async def on_started(speech_ms: int) -> None:
        started.append(speech_ms)

    command = make_command(plan=make_plan(motion="nod"))
    result = await executor.perform(command, on_started=on_started)
    assert result.status == "completed"
    assert result.speech_ms == 900
    assert started == [900]
    assert tts.texts == ["好呀"]
    cue = backend.calls[0][3]
    assert cue.hold_ms == 1600, "动作不早于语音结束，且不短于动作最短时长"
    assert result.error_code == ""


async def test_read_aloud_prefixes_the_danmaku() -> None:
    executor, _, tts = build()
    command = make_command(read_aloud=True, event=make_event(user="小满", text="你好呀"))
    await executor.perform(command)
    assert tts.texts == ["小满说：你好呀。好呀"]


async def test_tts_failure_falls_back_to_silent_action() -> None:
    tts = FakeTTS(AudioClip(path=None, duration_ms=0, error="network down", text="好呀"))
    executor, backend, _ = build(tts=tts)
    result = await executor.perform(make_command())
    assert result.status == "completed"
    assert result.error_code == "tts_failed"
    assert "network down" in result.error_message
    assert backend.calls, "静音动作仍然执行"


async def test_backend_error_becomes_error_result() -> None:
    backend = FakeBackend(outcome=PerformanceOutcome("error", "pet helper died"))
    executor, _, _ = build(backend=backend)
    result = await executor.perform(make_command())
    assert result.status == "error"
    assert result.error_code == "visual_error"
    assert "pet helper died" in result.error_message


async def test_backend_exception_becomes_executor_error() -> None:
    backend = FakeBackend(boom=True)
    executor, _, _ = build(backend=backend)
    result = await executor.perform(make_command())
    assert result.status == "error"
    assert result.error_code == "executor_error"


async def test_cancellation_propagates_so_the_dispatcher_can_report_interrupted() -> None:
    executor, _, _ = build(backend=FakeBackend(cancel=True))
    with pytest.raises(asyncio.CancelledError):
        await executor.perform(make_command())


async def test_gift_min_hold_override_is_applied() -> None:
    executor, backend, _ = build()
    command = make_command(
        plan=make_plan(motion="spin", priority="high", interrupt=True, min_hold_ms=3200)
    )
    await executor.perform(command)
    cue = backend.calls[0][3]
    assert cue.hold_ms == 3200


async def test_status_reports_tts_audio_and_backend() -> None:
    executor, _, _ = build()
    await executor.perform(make_command())
    status = executor.status()
    assert status["performed"] == 1
    assert status["tts"]["engine"] == "fake"
    assert status["backend"]["desktop_pet"]["state"] == "ready"
    assert status["current_command"] is None
