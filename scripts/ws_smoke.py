from __future__ import annotations

import asyncio
import json

import httpx
import websockets


async def main() -> None:
    async with websockets.connect("ws://127.0.0.1:8787/ws") as ws:
        snapshot = json.loads(await ws.recv())
        print("snapshot", snapshot["type"], snapshot["data"]["app"]["name"])
        httpx.post(
            "http://127.0.0.1:8787/api/events/manual",
            json={"text": "WebSocket 测试", "user": "WS"},
        )
        seen: list[str] = []
        for _ in range(15):
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            seen.append(message["type"])
            if message["type"] == "visual":
                print("visual", message["data"]["reply"], message["data"]["cue"]["motion"])
                break
        print("messages", seen)


if __name__ == "__main__":
    asyncio.run(main())
