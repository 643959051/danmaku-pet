"""Smoke test for manual re-reply to the latest danmaku."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8787"
ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    empty = httpx.post(f"{BASE}/api/chat/reply", json={}, timeout=10)
    print("empty_reply", empty.status_code, empty.json().get("detail"))
    sent = httpx.post(
        f"{BASE}/api/events/manual",
        json={"text": "你好，我是主动回复测试", "user": "弹幕观众"},
        timeout=10,
    )
    sent.raise_for_status()
    time.sleep(0.7)
    replied = httpx.post(f"{BASE}/api/chat/reply", json={}, timeout=10)
    print("reply", replied.status_code, json.dumps(replied.json().get("event"), ensure_ascii=False))
    status = {}
    for _ in range(100):
        status = httpx.get(f"{BASE}/api/status", timeout=5).json()
        if (
            status["orchestrator"]["stats"]["completed"] >= 2
            and status["orchestrator"]["queue"]["pending"] == 0
        ):
            break
        time.sleep(0.25)
    assert status["orchestrator"]["stats"]["completed"] >= 2
    assert status["orchestrator"]["last_danmaku"]["text"] == "你好，我是主动回复测试"
    report = {
        "empty_reply": {"status": empty.status_code, "detail": empty.json().get("detail")},
        "reply": replied.json().get("event"),
        "stats": status["orchestrator"]["stats"],
        "recent_sources": [
            entry["event"]["source"] for entry in status["orchestrator"]["recent"][:4]
        ],
    }
    path = ROOT / "temp" / "reply-smoke.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "completed": report["stats"]["completed"],
                "reply_source": report["reply"]["source"] if report["reply"] else None,
                "last_text": status["orchestrator"]["last_danmaku"]["text"],
                "report": str(path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
