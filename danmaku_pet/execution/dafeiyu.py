"""Native dsh-dafeiyu visual backend.

We run the plugin's own unmodified ``runtime/helper.py`` with the project's
PySide6. The helper speaks a line-delimited JSON protocol over stdin/stdout.
This adapter maps the stable Motion enum onto its state/pulse vocabulary and
records the mapping in ``action_adapter.py``.

The packaged .exe is intentionally not used: its PyInstaller bootstrap tries
to extract outside the workspace. Source mode keeps TEMP, layout and caches
inside the project and is the same visual code.

I/O note: on this Windows setup asyncio's subprocess transport needs named
pipes, which the DSH sandbox denies. Popen with anonymous pipes plus daemon
reader threads works and is bridged back to the loop with
``call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Any

from danmaku_pet.config import Config
from danmaku_pet.models import ActionPlan, Event, VisualCue

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = 1


class DafeiyuNativeBackend:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._proc: subprocess.Popen[str] | None = None
        self._ready = asyncio.Event()
        self._exited = asyncio.Event()
        self._write_lock = threading.Lock()
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._threads: list[threading.Thread] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready_task: asyncio.Task[None] | None = None
        self.state = "disabled" if not config.desktop_pet_enabled else "idle"
        self.last_error: str | None = None
        self._closed = False

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.desktop_pet_enabled,
            "state": self.state,
            "pid": self._proc.pid if self._proc and self._proc.poll() is None else None,
            "helper": str(self.config.helper_path),
            "package": str(self.config.dafeiyu_package),
            "protocol": _PROTOCOL_VERSION,
            "last_error": self.last_error,
            "stderr_tail": list(self._stderr_tail)[-5:],
        }

    # --------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if not self.config.desktop_pet_enabled:
            self.state = "disabled"
            return
        if not self.config.helper_path.is_file():
            self.state = "missing"
            self.last_error = f"找不到 helper.py: {self.config.helper_path}"
            logger.warning(self.last_error)
            return
        if self._proc is not None and self._proc.poll() is None:
            return
        self._loop = asyncio.get_running_loop()
        self._ready.clear()
        self._exited.clear()
        self._closed = False
        self.state = "starting"
        try:
            self._spawn()
        except Exception as exc:
            self.state = "error"
            self.last_error = f"启动大肥鱼失败: {exc}"
            logger.exception(self.last_error)
            return
        self._ready_task = asyncio.create_task(self._await_ready(), name="dafeiyu-ready")

    async def _await_ready(self) -> None:
        ready_task = asyncio.create_task(self._ready.wait())
        exited_task = asyncio.create_task(self._exited.wait())
        try:
            done, pending = await asyncio.wait(
                {ready_task, exited_task},
                timeout=75,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if ready_task in done and self._ready.is_set():
                self.state = "ready"
                self.last_error = None
                logger.info(
                    "大肥鱼原生视觉后端已就绪 (pid=%s)",
                    self._proc.pid if self._proc else None,
                )
                try:
                    await self._send_config()
                except Exception as exc:
                    logger.warning("发送大肥鱼配置失败: %s", exc)
                return
            if not done:
                self.state = "error"
                self.last_error = "大肥鱼 75 秒内未完成 ready 握手"
                logger.error(self.last_error)
                await self._terminate()
                return
            self.state = "error"
            self.last_error = "大肥鱼 helper 在 ready 前退出"
            logger.error(self.last_error)
        except asyncio.CancelledError:
            raise

    def _spawn(self) -> None:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        from danmaku_pet.paths import FROZEN

        # A frozen build has no standalone interpreter to run helper.py with, so
        # the exe re-enters itself and executes the vendored helper in-process.
        command = (
            [sys.executable, "--run-dafeiyu-helper"]
            if FROZEN
            else [sys.executable, str(self.config.helper_path)]
        )
        self._proc = subprocess.Popen(
            command,
            cwd=str(self.config.dafeiyu_package),
            env=self.config.env_for_child(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        self._threads = [
            threading.Thread(target=self._read_stdout, name="dafeiyu-stdout", daemon=True),
            threading.Thread(target=self._read_stderr, name="dafeiyu-stderr", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _dispatch(self, callback, *args) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:
            pass

    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                text = line.strip()
                if text:
                    self._dispatch(self._on_line, text)
        except Exception as exc:
            self._dispatch(self._on_reader_error, f"读取大肥鱼输出失败: {exc}")
        finally:
            self._dispatch(self._on_eof)

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                text = line.strip()
                if text:
                    self._stderr_tail.append(text[-500:])
                    logger.debug("大肥鱼 stderr: %s", text[-500:])
        except Exception:
            pass

    def _on_line(self, text: str) -> None:
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("大肥鱼非 JSON 输出: %s", text[:300])
            return
        kind = message.get("kind")
        if kind == "ready":
            self._ready.set()
        elif kind == "error":
            self.last_error = str(message.get("message", "helper error"))
            logger.warning("大肥鱼 helper 错误: %s", self.last_error)
        elif kind == "closed":
            logger.info("大肥鱼 helper 主动关闭: %s", message.get("reason"))
        else:
            logger.debug("大肥鱼消息: %s", message)

    def _on_reader_error(self, message: str) -> None:
        self.last_error = message
        logger.warning(message)

    def _on_eof(self) -> None:
        if not self._closed:
            self.state = "stopped"
            self._ready.clear()
            self._exited.set()
            logger.info("大肥鱼 helper 已退出")

    def _send_sync(self, message: dict[str, Any]) -> bool:
        if self._closed:
            return False
        proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return False
        payload = {
            "protocolVersion": _PROTOCOL_VERSION,
            "timestamp": int(time.time() * 1000),
            **message,
        }
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with self._write_lock:
            try:
                proc.stdin.write(line)
                proc.stdin.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError) as exc:
                self.state = "error"
                self.last_error = f"写入大肥鱼失败: {exc}"
                logger.warning(self.last_error)
                return False

    async def _send(self, message: dict[str, Any]) -> bool:
        return await asyncio.to_thread(self._send_sync, message)

    async def _send_config(self) -> None:
        await self._send(
            {
                "kind": "config",
                "openUrl": f"http://{self.config.host}:{self.config.port}/",
                "openLabel": "打开小鱼控制台",
                "scale": self.config.pet_scale,
                "bubbleScale": 1.0,
                "reducedMotion": False,
                "soundEnabled": False,
                "activityLevel": "quiet",
                "bubbleMode": "always",
                "bubbleStates": ["SUCCESS", "ERROR", "WAITING", "WORKING"],
            }
        )

    async def wait_ready(self, timeout: float = 2.5) -> bool:
        if self.state == "ready" and self._ready.is_set():
            return True
        if self.state in {"disabled", "missing", "error", "stopped"}:
            return False
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # --------------------------------------------------------------- commands
    async def show(self, plan: ActionPlan, event: Event, cue: VisualCue) -> bool:
        if not await self.wait_ready():
            return False
        detail = f"@{event.user} · {cue.label}"
        if cue.mode == "pulse":
            sent = await self._send(
                {
                    "kind": "pulse",
                    "state": cue.native_state,
                    "ttlMs": cue.native_hold_ms,
                    "resumeState": "IDLE",
                    "activity": cue.native_activity,
                    "message": plan.reply,
                    "detail": detail,
                }
            )
            if sent and plan.motion == "spin":
                # Local helper extension: yaw-style horizontal turn. Unpatched
                # upstream helpers simply ignore this unknown message kind.
                effect_sent = await self._send(
                    {
                        "kind": "effect",
                        "name": "spin",
                        "durationMs": min(2000, max(1200, cue.native_hold_ms)),
                    }
                )
                logger.info("native spin effect sent=%s", effect_sent)
            return sent
        if cue.mode == "talk":
            return await self._send(
                {
                    "kind": "state",
                    "state": "WORKING",
                    "activity": cue.native_activity,
                    "message": plan.reply,
                    "detail": detail,
                }
            )
        return True

    async def idle(self) -> None:
        if self.state == "ready":
            await self._send({"kind": "state", "state": "IDLE", "message": ""})

    async def interrupt(self) -> None:
        if self.state == "ready":
            await self._send({"kind": "state", "state": "IDLE", "message": ""})

    # --------------------------------------------------------------- teardown
    async def _terminate(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                await asyncio.to_thread(proc.wait, 4)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

    async def shutdown(self) -> None:
        self._closed = True
        if self._ready_task is not None and not self._ready_task.done():
            self._ready_task.cancel()
        if self.state == "ready":
            await self._send({"kind": "shutdown"})
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                await asyncio.to_thread(proc.wait, 4)
            except subprocess.TimeoutExpired:
                await self._terminate()
        self.state = "stopped"
        self._ready.clear()
