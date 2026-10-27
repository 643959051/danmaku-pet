"""Isolate the native spin effect and capture one helper snapshot."""

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
SNAPSHOT = ROOT / "temp" / "screenshots" / "native-spin.png"


def main() -> int:
    runtime = ROOT / "temp" / "dafeiyu-runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "TEMP": str(runtime),
            "TMP": str(runtime),
            "LOCALAPPDATA": str(ROOT / ".cache" / "localappdata"),
            "DSH_DAFEIYU_LAYOUT_PATH": str(runtime / "layout.json"),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    proc = subprocess.Popen(
        [str(PYTHON), str(HELPER), "--snapshot", str(SNAPSHOT)],
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
        proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    try:
        line = proc.stdout.readline().strip()
        print("handshake", line)
        send(
            {
                "protocolVersion": 1,
                "kind": "config",
                "scale": 1.0,
                "bubbleScale": 1.0,
                "reducedMotion": False,
                "soundEnabled": False,
                "bubbleMode": "hidden",
            }
        )
        send(
            {
                "protocolVersion": 1,
                "kind": "effect",
                "name": "spin",
                "durationMs": 1000,
            }
        )
        time.sleep(0.45)
        print("snapshot exists", SNAPSHOT.exists(), SNAPSHOT)
        return 0
    finally:
        try:
            send({"protocolVersion": 1, "kind": "shutdown"})
        except Exception:
            pass
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()
        stderr = proc.stderr.read() if proc.stderr else ""
        if stderr.strip():
            print("stderr", stderr.strip()[-1500:])


if __name__ == "__main__":
    raise SystemExit(main())
