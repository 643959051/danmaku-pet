from __future__ import annotations

import pytest

from danmaku_pet.douyin.openapi import PlatformNotConfigured
from danmaku_pet.douyin.tasks import InteractionTaskManager


class FakeOpenAPI:
    def __init__(self, *, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.starts: list[tuple[str, str]] = []
        self.stops: list[tuple[str, str]] = []

    async def start_task(self, room_id: str, kind: str) -> dict:
        self.starts.append((room_id, kind))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("platform busy")
        return {"task_id": f"task-{room_id}-{kind}"}

    async def stop_task(self, task_id: str, kind: str) -> dict:
        self.stops.append((task_id, kind))
        return {}


async def test_start_and_stop_task() -> None:
    api = FakeOpenAPI()
    manager = InteractionTaskManager(api)
    task = await manager.start("room-1", "comment")
    assert task.status == "running"
    assert task.task_id == "task-room-1-comment"
    assert manager.snapshot()[0]["kind"] == "comment"

    stopped = await manager.stop("room-1", "comment")
    assert stopped is not None and stopped.status == "stopped"
    assert api.stops == [("task-room-1-comment", "comment")]
    await manager.shutdown()


async def test_unsupported_kind_is_rejected() -> None:
    manager = InteractionTaskManager(FakeOpenAPI())
    with pytest.raises(ValueError):
        await manager.start("room-1", "like")
    await manager.shutdown()


async def test_unconfigured_platform_reports_error_without_retry() -> None:
    class Unconfigured:
        async def start_task(self, room_id: str, kind: str) -> dict:
            raise PlatformNotConfigured("no path configured")

        async def stop_task(self, task_id: str, kind: str) -> dict:
            return {}

    manager = InteractionTaskManager(Unconfigured())
    task = await manager.start("room-1", "gift")
    assert task.status == "error"
    assert "no path configured" in task.error
    assert task.retries == 0
    await manager.shutdown()


async def test_failed_start_is_reported_without_background_retry() -> None:
    """平台标准：请求返回后不应该还有后台任务；失败由主播手动重试。"""

    api = FakeOpenAPI(fail_times=1)
    manager = InteractionTaskManager(api)
    task = await manager.start("room-1", "comment")
    assert task.status == "error"
    assert "platform busy" in task.error
    assert len(api.starts) == 1, "不应在后台自动重试"
    await manager.shutdown()


async def test_stop_all_and_shutdown() -> None:
    api = FakeOpenAPI()
    manager = InteractionTaskManager(api)
    await manager.start("room-1", "comment")
    await manager.start("room-1", "gift")
    await manager.stop_all()
    assert {task["status"] for task in manager.snapshot()} == {"stopped"}
    await manager.shutdown()
