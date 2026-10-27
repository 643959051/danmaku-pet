from __future__ import annotations

from pathlib import Path
import time

import pytest

from danmaku_pet.execution.tts import AudioPlayer, EdgeTTSService, estimate_duration_ms


def test_estimate_is_bounded() -> None:
    assert 1400 <= estimate_duration_ms("你好") <= 15000
    assert estimate_duration_ms("") == 1400
    assert estimate_duration_ms("很长" * 2000) == 15000


@pytest.mark.asyncio
async def test_edge_tts_failure_returns_silent_clip(monkeypatch) -> None:
    import edge_tts

    class Broken:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def save(self, path: str) -> None:
            raise RuntimeError("network down")

    monkeypatch.setattr(edge_tts, "Communicate", Broken)
    cache = Path(__file__).resolve().parent.parent / "temp" / "test-artifacts" / "tts"
    cache.mkdir(parents=True, exist_ok=True)
    service = EdgeTTSService(cache, voice="zh-CN-XiaoxiaoNeural")
    clip = await service.synthesize("你好呀")
    assert clip.path is None
    assert clip.duration_ms >= 1400
    assert clip.error


@pytest.mark.asyncio
async def test_edge_tts_cached_file_is_reused(monkeypatch) -> None:
    import edge_tts

    calls = {"count": 0}

    class Worker:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def save(self, path: str) -> None:
            calls["count"] += 1
            Path(path).write_bytes(b"ID3" + b"x" * 500)

    monkeypatch.setattr(edge_tts, "Communicate", Worker)
    cache = Path(__file__).resolve().parent.parent / "temp" / "test-artifacts" / "tts-cache"
    cache.mkdir(parents=True, exist_ok=True)
    service = EdgeTTSService(cache, voice="zh-CN-XiaoxiaoNeural")
    text = f"缓存测试-{time.time_ns()}"
    first = await service.synthesize(text)
    second = await service.synthesize(text)
    assert first.path and first.path.is_file()
    assert first.cached is False
    assert second.cached is True
    assert calls["count"] == 1


def test_audio_player_handles_missing_path() -> None:
    player = AudioPlayer()
    assert player.measure(None) is None
    assert player.play(None) is False
    player.close()
