"""Smoke test against a running **cloud** backend.

Usage (PowerShell):
    $env:CLOUD_BASE = "http://127.0.0.1:8000"
    $env:ADMIN_TOKEN = "<dev 启动时打印的 ADMIN_TOKEN>"
    $env:ROOM_ID = "dev-room"
    .\\.conda\\env\\python.exe scripts\\smoke_test.py

It exercises: health -> admin auth -> simulated comment -> simulated gift ->
room stats (decision + gateway).  It does not need a real Douyin account.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

BASE = os.getenv("CLOUD_BASE", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.getenv("ADMIN_TOKEN", "").strip()
ROOM = os.getenv("ROOM_ID", "dev-room").strip() or "dev-room"
ROOT = Path(__file__).resolve().parent.parent
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}


def wait_for(predicate, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = httpx.get(f"{BASE}/api/status", headers=HEADERS, timeout=10).json()
        if predicate(last):
            return last
        time.sleep(0.25)
    raise TimeoutError(f"condition not met; last gateway={last.get('orchestrator', {}).get('gateway')}")


def main() -> int:
    if not TOKEN:
        raise SystemExit("缺少 ADMIN_TOKEN 环境变量（dev 启动日志里会打印）")
    health = httpx.get(f"{BASE}/api/health", timeout=10).json()
    assert health["ok"], health
    assert health["protocol_version"] >= 1

    unauthorized = httpx.get(f"{BASE}/api/status", timeout=10)
    assert unauthorized.status_code == 401, unauthorized.status_code

    manual = httpx.post(
        f"{BASE}/api/rooms/{ROOM}/events/manual",
        headers=HEADERS,
        json={"text": "小鱼，你好呀，挥挥手", "user": "最终验收"},
        timeout=15,
    )
    manual.raise_for_status()
    gift = httpx.post(
        f"{BASE}/api/rooms/{ROOM}/events/gift",
        headers=HEADERS,
        json={"gift_name": "嘉年华", "gift_count": 1, "user": "礼物验收"},
        timeout=15,
    )
    gift.raise_for_status()

    status = wait_for(
        lambda s: any(
            room["room_id"] == ROOM and room["stats"]["received"] >= 2
            for room in s["orchestrator"]["rooms"]
        )
    )
    room = next(item for item in status["orchestrator"]["rooms"] if item["room_id"] == ROOM)
    gateway = status["orchestrator"]["gateway"]
    assert gateway["mode"] in {"mock", "wss", "douyin_cloud"}, gateway
    assert room["stats"]["dispatched"] >= 1, room["stats"]
    gift_entries = [
        entry for entry in room["recent"] if entry["event"]["source"] == "gift_sim"
    ]
    assert gift_entries, [entry["event"]["source"] for entry in room["recent"]]
    gift_plan = gift_entries[0]["plan"] or {}
    assert gift_plan.get("priority") == "high", gift_plan
    assert gift_plan.get("motion") == "spin", gift_plan

    result = {
        "health": health,
        "manual_event": manual.json()["event"],
        "gift_event": gift.json()["event"],
        "room_stats": room["stats"],
        "gateway": gateway,
        "gift_plan": gift_plan,
        "recent_statuses": [entry["status"] for entry in room["recent"][:5]],
    }
    path = ROOT / "temp" / "smoke-result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "room": ROOM,
                "gateway_mode": gateway["mode"],
                "received": room["stats"]["received"],
                "dispatched": room["stats"]["dispatched"],
                "completed": room["stats"]["completed"],
                "gift_motion": gift_plan.get("motion"),
                "report": str(path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
