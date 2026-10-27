"""Smoke test for the default-off auto-reply switch and pending inbox."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8787"
ROOT = Path(__file__).resolve().parent.parent


def get_status() -> dict:
    return httpx.get(f"{BASE}/api/status", timeout=10).json()


def wait_for(predicate, timeout: float = 25.0) -> dict:
    deadline = time.time() + timeout
    last = get_status()
    while time.time() < deadline:
        last = get_status()
        if predicate(last):
            return last
        time.sleep(0.25)
    raise TimeoutError(json.dumps(last.get("orchestrator", {}).get("stats"), ensure_ascii=False))


def main() -> int:
    initial = get_status()
    assert initial["config"]["auto_reply"] is False, initial["config"]
    assert initial["orchestrator"]["pending"] == []

    # OFF: simulate danmaku, expect a pending inbox and no LLM/TTS.
    httpx.post(
        f"{BASE}/api/simulator/start",
        json={"interval_seconds": 1, "scenario": "chat"},
        timeout=10,
    )
    time.sleep(2.8)
    httpx.post(f"{BASE}/api/simulator/stop", json={}, timeout=10)
    off_status = wait_for(lambda s: len(s["orchestrator"]["pending"]) >= 2, timeout=5)
    pending = off_status["orchestrator"]["pending"]
    assert off_status["orchestrator"]["stats"]["completed"] == 0
    assert all(not entry.get("plan") for entry in off_status["orchestrator"]["recent"])

    # Manual reply to one pending danmaku.
    chosen = pending[0]["event"]
    reply = httpx.post(
        f"{BASE}/api/chat/reply",
        json={"event_id": chosen["event_id"]},
        timeout=10,
    )
    reply.raise_for_status()
    after_reply = wait_for(
        lambda s: s["orchestrator"]["stats"]["completed"] >= 1
        and all(item["event"]["event_id"] != chosen["event_id"] for item in s["orchestrator"]["pending"]),
        timeout=30,
    )

    # ON: new danmaku should be read aloud + answered.
    enabled = httpx.post(
        f"{BASE}/api/settings/auto-reply",
        json={"enabled": True},
        timeout=10,
    )
    enabled.raise_for_status()
    assert enabled.json()["enabled"] is True
    httpx.post(
        f"{BASE}/api/simulator/start",
        json={"interval_seconds": 1, "scenario": "chat"},
        timeout=10,
    )
    time.sleep(1.2)
    httpx.post(f"{BASE}/api/simulator/stop", json={}, timeout=10)
    on_status = wait_for(
        lambda s: any(entry.get("read_aloud") for entry in s["orchestrator"]["recent"])
        and s["orchestrator"]["stats"]["completed"] >= after_reply["orchestrator"]["stats"]["completed"] + 1,
        timeout=30,
    )
    read_entries = [
        entry for entry in on_status["orchestrator"]["recent"] if entry.get("read_aloud")
    ]
    assert read_entries, on_status["orchestrator"]["recent"]

    # Restore default off.
    disabled = httpx.post(
        f"{BASE}/api/settings/auto-reply",
        json={"enabled": False},
        timeout=10,
    )
    disabled.raise_for_status()
    assert disabled.json()["enabled"] is False

    report = {
        "initial_auto_reply": initial["config"]["auto_reply"],
        "pending_before_manual_reply": len(pending),
        "manual_reply_event": reply.json()["event"],
        "manual_completed": after_reply["orchestrator"]["stats"]["completed"],
        "auto_reply_enabled": True,
        "read_aloud_entries": read_entries,
        "final_auto_reply": disabled.json()["enabled"],
    }
    path = ROOT / "temp" / "auto-reply-smoke.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "pending_before": len(pending),
                "manual_completed": report["manual_completed"],
                "read_aloud_count": len(read_entries),
                "final_auto_reply": report["final_auto_reply"],
                "report": str(path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
