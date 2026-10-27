"""Integration spike: talk to dsh-dafeiyu's own helper.py from source.

Runs the unmodified helper from vendor/dafeiyu with PySide6 from the project
conda env. All writable locations point inside the workspace.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "vendor" / "dafeiyu" / "package"
HELPER = PACKAGE / "runtime" / "helper.py"
PYTHON = ROOT / ".conda" / "env" / "python.exe"


def main() -> int:
    if not HELPER.is_file():
        print(f"helper not found: {HELPER}")
        return 2

    runtime = ROOT / "temp" / "dafeiyu-runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "TEMP": str(runtime),
            "TMP": str(runtime),
            "LOCALAPPDATA": str(ROOT / ".cache" / "localappdata"),
            "DSH_DAFEIYU_LAYOUT_PATH": str(runtime / "layout.json"),
            "PYTHONUTF8": "1",
            "QT_LOGGING_RULES": "qt.qpa.*=false",
        }
    )

    headless = "--headless" in sys.argv
    command = [str(PYTHON), str(HELPER)]
    if headless:
        command.append("--headless")
    print("launch:", command)
    started = time.perf_counter()
    proc = subprocess.Popen(
        command,
        cwd=str(PACKAGE),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdin and proc.stdout

    def send(message: dict) -> None:
        line = json.dumps(message, ensure_ascii=False) + "\n"
        proc.stdin.write(line)
        proc.stdin.flush()
        print("send:", line.strip())

    try:
        line = proc.stdout.readline().strip()
        print(f"ready in {time.perf_counter() - started:.2f}s: {line}")
        ready = json.loads(line) if line else {}
        if ready.get("kind") != "ready":
            print("unexpected handshake")
            return 3
        send(
            {
                "protocolVersion": 1,
                "kind": "config",
                "scale": 0.8,
                "bubbleScale": 1.0,
                "reducedMotion": False,
                "bubbleMode": "always",
                "bubbleStates": ["SUCCESS", "ERROR", "WAITING", "WORKING"],
            }
        )
        send(
            {
                "protocolVersion": 1,
                "kind": "task",
                "task": "MVP 联调",
                "message": "你好，我是大肥鱼。",
                "detail": "来自手动输入",
            }
        )
        send(
            {
                "protocolVersion": 1,
                "kind": "pulse",
                "state": "SUCCESS",
                "ttlMs": 1800,
                "resumeState": "IDLE",
                "message": "闭环成功！",
                "detail": "pulse 状态已接收",
            }
        )
        time.sleep(2.5)
        send({"protocolVersion": 1, "kind": "state", "state": "IDLE", "message": ""})
        time.sleep(0.5)
        return 0
    finally:
        try:
            send({"protocolVersion": 1, "kind": "shutdown"})
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        stderr = proc.stderr.read() if proc.stderr else ""
        if stderr.strip():
            print("stderr (tail):", stderr.strip()[-2000:])
        print("exit:", proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
