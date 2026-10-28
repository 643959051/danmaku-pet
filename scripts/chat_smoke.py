"""Smoke test: 待回复队列 + 手动回复（云端现在负责决策）。

Usage:
    $env:ADMIN_TOKEN = "..."; $env:ROOM_ID = "dev-room"
    .\\.conda\\env\\python.exe scripts\\chat_smoke.py
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

BASE = os.getenv("CLOUD_BASE", "http://127.0.0.1:8000").rstrip("/")
ROOM = os.getenv("ROOM_ID", "dev-room").strip() or "dev-room"
HEADERS = {"Authorization": f"Bearer {os.getenv('ADMIN_TOKEN', '').strip()}"}
ROOT = Path(__file__).resolve().parent.parent


def get_room() -> dict:
    status = httpx.get(f"{BASE}/api/status", headers=HEADERS, timeout=10).json()
    return next(
        (room for room in status["orchestrator"]["rooms"] if room["room_id"] == ROOM),
        {"stats": {}, "pending": [], "recent": []},
    )


def main() -> int:
    # 云端默认自动回复关闭：新弹幕进入 pending，不调用 LLM。
    sent = httpx.post(
        f"{BASE}/api/rooms/{ROOM}/events/manual",
        headers=HEADERS,
        json={"text": "你好，我是主动回复测试", "user": "弹幕观众"},
        timeout=15,
    )
    sent.raise_for_status()
    time.sleep(0.4)
    room = get_room()
    pending = room.get("pending") or []
    assert pending, "手动输入也应进入队列/待回复（若自动回复关闭则进入 pending）"

    # 没有可回复弹幕时应返回 400；这里先清空 pending 再验证会破坏状态，跳过。
    replied = httpx.post(
        f"{BASE}/api/rooms/{ROOM}/reply",
        headers=HEADERS,
        json={},
        timeout=15,
    )
    print("reply", replied.status_code, json.dumps(replied.json(), ensure_ascii=False))

    deadline = time.time() + 30
    final: dict = {}
    while time.time() < deadline:
        final = get_room()
        if (
            final.get("stats", {}).get("dispatched", 0) >= 1
            and final.get("queue", {}).get("pending", 0) == 0
            and not final.get("pending")
        ):
            break
        time.sleep(0.25)
    assert final.get("stats", {}).get("received", 0) >= 1
    report = {
        "reply": replied.json().get("event") if replied.status_code == 200 else None,
        "stats": final.get("stats"),
        "recent_sources": [entry["event"]["source"] for entry in final.get("recent", [])[:4]],
    }
    path = ROOT / "temp" / "reply-smoke.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "stats": report["stats"], "report": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
