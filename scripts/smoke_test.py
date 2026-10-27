"""Smoke test against a running local server.

Usage:
    .\.conda\env\python.exe scripts\smoke_test.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8787"
ROOT = Path(__file__).resolve().parent.parent


def wait_for(predicate, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = httpx.get(f"{BASE}/api/status", timeout=5).json()
        if predicate(last):
            return last
        time.sleep(0.25)
    raise TimeoutError(f"condition not met; last stats={last.get('orchestrator', {}).get('stats')}")


def main() -> int:
    health = httpx.get(f"{BASE}/api/health", timeout=5).json()
    assert health["ok"], health
    manual = httpx.post(
        f"{BASE}/api/events/manual",
        json={"text": "小鱼，你好呀，挥挥手", "user": "最终验收"},
        timeout=10,
    )
    manual.raise_for_status()
    gift = httpx.post(
        f"{BASE}/api/events/gift",
        json={"gift_name": "嘉年华", "gift_count": 1, "user": "礼物验收"},
        timeout=10,
    )
    gift.raise_for_status()
    status = wait_for(
        lambda s: int(s["orchestrator"]["stats"]["completed"]) >= 1
        and s["orchestrator"]["queue"]["pending"] == 0,
        timeout=30,
    )
    recent = status["orchestrator"]["recent"]
    gift_entry = next(
        (entry for entry in recent if entry["event"]["source"] == "gift_sim"), None
    )
    result = {
        "health": health,
        "manual_event": manual.json()["event"],
        "gift_event": gift.json()["event"],
        "stats": status["orchestrator"]["stats"],
        "queue": status["orchestrator"]["queue"],
        "desktop_pet": status["orchestrator"]["backend"]["desktop_pet"],
        "gift_plan": gift_entry["plan"] if gift_entry else None,
        "recent_statuses": [entry["status"] for entry in recent[:5]],
        "actions_count": len(httpx.get(f"{BASE}/api/actions", timeout=5).json()["motions"]),
    }
    assert result["actions_count"] == 9
    assert result["desktop_pet"]["state"] in {"ready", "starting"}
    assert result["gift_plan"] is not None
    assert result["gift_plan"]["priority"] == "high"
    assert result["gift_plan"]["motion"] == "spin"
    path = ROOT / "temp" / "smoke-result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "python": "conda-prefix",
                "desktop_pet": result["desktop_pet"]["state"],
                "completed": result["stats"]["completed"],
                "interrupted": result["stats"]["interrupted"],
                "gift_motion": result["gift_plan"]["motion"],
                "actions_count": result["actions_count"],
                "report": str(path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
