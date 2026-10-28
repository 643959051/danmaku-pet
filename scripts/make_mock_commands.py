"""Generate a local command file for the client's ``static`` transport.

This is the 阶段 3 acceptance path: no cloud, no network — just simulated
PerformCommands that the real Windows client must speak and perform.

Usage:
    .\\.conda\\env\\python.exe scripts\\make_mock_commands.py
    # then start the client:
    .\\dist\\DanmakuPet\\DanmakuPet.exe --transport static --static-commands .\\temp\\mock-commands.jsonl --room-id dev-room
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from danmaku_pet.decision.gifts import decide_gift  # noqa: E402
from danmaku_pet.models import Event  # noqa: E402
from danmaku_pet.shared.commands import PerformCommand, speech_text_for  # noqa: E402
from danmaku_pet.shared.plans import ActionPlan  # noqa: E402
from danmaku_pet.shared.protocol import make_command_id  # noqa: E402

ROOM = "dev-room"


def main() -> int:
    now = time.time()
    events = [
        Event(source="danmaku_sim", room_id=ROOM, user="小满", text="小鱼，你好呀", timestamp=now),
        Event(
            source="gift_sim",
            room_id=ROOM,
            user="阿星",
            text="送出 火箭",
            gift_name="火箭",
            gift_count=1,
            timestamp=now,
        ),
        Event(source="danmaku_sim", room_id=ROOM, user="橘子汽水", text="转个圈我看看", timestamp=now),
    ]
    lines = []
    for index, event in enumerate(events):
        if event.is_gift:
            plan = decide_gift(event).plan
        else:
            plan = ActionPlan(
                reply=f"我是小鱼，收到{event.user}的弹幕：{event.text}",
                emotion="happy",
                motion="wave" if index == 0 else "spin",
                priority="normal",
                interrupt=False,
            )
        command = PerformCommand(
            command_id=make_command_id(ROOM, event),
            room_id=ROOM,
            event=event,
            plan=plan,
            created_at=now,
            # 本地联调给足有效期
            expires_at=now + 3600,
            read_aloud=True,
        )
        lines.append(command.to_dict())
    path = ROOT / "temp" / "mock-commands.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in lines) + "\n",
        encoding="utf-8",
    )
    for item in lines:
        event = Event.from_dict(item["event"])
        print(f"- {event.user}: {event.display_text} -> {speech_text_for(event, PerformCommand.from_dict(item).plan, True)}")
    print(f"\n已生成 {len(lines)} 条模拟指令：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
