"""WebSocket smoke test for the cloud admin console.

Usage:
    $env:ADMIN_TOKEN = "..."; $env:ROOM_ID = "dev-room"
    .\\.conda\\env\\python.exe scripts\\ws_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx
import websockets

BASE = os.getenv("CLOUD_BASE", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.getenv("ADMIN_TOKEN", "").strip()
ROOM = os.getenv("ROOM_ID", "dev-room").strip() or "dev-room"
WS_BASE = BASE.replace("https://", "wss://").replace("http://", "ws://")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


async def main() -> None:
    async with websockets.connect(f"{WS_BASE}/ws?token={TOKEN}") as ws:
        snapshot = json.loads(await ws.recv())
        print("snapshot", snapshot["type"], snapshot["data"]["app"]["name"])
        httpx.post(
            f"{BASE}/api/rooms/{ROOM}/events/gift",
            headers=HEADERS,
            json={"gift_name": "火箭", "gift_count": 1, "user": "WS"},
            timeout=15,
        )
        seen: list[str] = []
        for _ in range(20):
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
            seen.append(message["type"])
            if message["type"] == "command":
                command = message["data"]["command"]
                print("command", command["plan"]["motion"], command["plan"]["reply"])
                break
        print("messages", seen)
        assert "command" in seen, seen


if __name__ == "__main__":
    asyncio.run(main())
