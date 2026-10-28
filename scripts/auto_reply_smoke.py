"""Smoke test: 自动回复开关与待回复列表（云端多房间版）。

Usage:
    $env:ADMIN_TOKEN = "..."; $env:ROOM_ID = "dev-room"
    .\\.conda\\env\\python.exe scripts\\auto_reply_smoke.py
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
        {"stats": {}, "pending": [], "recent": [], "auto_reply": False},
    )


def wait_for(predicate, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    last = get_room()
    while time.time() < deadline:
        last = get_room()
        if predicate(last):
            return last
        time.sleep(0.25)
    return last


def set_auto(enabled: bool) -> dict:
    response = httpx.post(
        f"{BASE}/api/settings/auto-reply",
        headers=HEADERS,
        json={"enabled": enabled, "room_id": ROOM},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    # 房间是按事件懒创建的；先用一条评论把房间建出来，否则 auto-reply 接口会 404。
    httpx.post(
        f"{BASE}/api/rooms/{ROOM}/events/manual",
        headers=HEADERS,
        json={"text": "初始化房间（auto-reply smoke）", "user": "脚本"},
        timeout=10,
    )
    set_auto(False)
    initial = get_room()
    assert initial.get("auto_reply") is False, initial.get("auto_reply")

    # OFF：模拟评论进入 pending，不产生决策。
    httpx.post(
        f"{BASE}/api/simulator/start",
        headers=HEADERS,
        json={"interval_seconds": 1, "scenario": "chat", "room_id": ROOM},
        timeout=10,
    ).raise_for_status()
    time.sleep(2.8)
    httpx.post(f"{BASE}/api/simulator/stop", headers=HEADERS, json={}, timeout=10)
    off_room = wait_for(lambda room: len(room.get("pending") or []) >= 1, timeout=8)
    pending = off_room.get("pending") or []
    assert pending, "自动回复关闭时应进入待回复列表"
    assert off_room.get("stats", {}).get("completed", 0) == 0

    # 手动回复其中一条。
    chosen = pending[0]["event"]
    reply = httpx.post(
        f"{BASE}/api/rooms/{ROOM}/reply",
        headers=HEADERS,
        json={"event_id": chosen["event_id"]},
        timeout=15,
    )
    reply.raise_for_status()
    after_reply = wait_for(
        lambda room: room.get("stats", {}).get("dispatched", 0) >= 1
        and all(item["event"]["event_id"] != chosen["event_id"] for item in (room.get("pending") or [])),
        timeout=30,
    )

    # ON：新评论会先念出来再回复。
    set_auto(True)
    httpx.post(
        f"{BASE}/api/simulator/start",
        headers=HEADERS,
        json={"interval_seconds": 1, "scenario": "chat", "room_id": ROOM},
        timeout=10,
    ).raise_for_status()
    time.sleep(1.4)
    httpx.post(f"{BASE}/api/simulator/stop", headers=HEADERS, json={}, timeout=10)
    on_room = wait_for(
        lambda room: any(entry.get("read_aloud") for entry in room.get("recent") or []),
        timeout=30,
    )
    read_entries = [entry for entry in on_room.get("recent") or [] if entry.get("read_aloud")]
    assert read_entries, "自动回复开启后应有 read_aloud 事件"

    set_auto(False)
    report = {
        "pending_before_manual_reply": len(pending),
        "manual_reply_event": reply.json().get("event"),
        "dispatched_after_reply": after_reply.get("stats", {}).get("dispatched"),
        "read_aloud_entries": read_entries[:3],
    }
    path = ROOT / "temp" / "auto-reply-smoke.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "pending_before": len(pending),
                "read_aloud_count": len(read_entries),
                "report": str(path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
