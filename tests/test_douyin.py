from __future__ import annotations

import time

from danmaku_pet.inputs.douyin_webcast import DouyinWebcastAdapter


def test_chat_message_mapping() -> None:
    adapter = DouyinWebcastAdapter()
    event = adapter.from_webcast_chat(
        {
            "user": {"nickname": "小满"},
            "content": "你好呀",
            "common": {"createTime": "1710000000000"},
        }
    )
    assert event.source == "danmaku_sim"
    assert event.user == "小满"
    assert event.text == "你好呀"
    assert 1710000000 <= event.timestamp < 1710000100


def test_gift_message_mapping() -> None:
    adapter = DouyinWebcastAdapter()
    event = adapter.from_webcast_gift(
        {
            "user": {"nickname": "阿星"},
            "gift": {"name": "小心心"},
            "repeatCount": 3,
            "common": {"createTime": 1710000000000},
        }
    )
    assert event.source == "gift_sim"
    assert event.gift_name == "小心心"
    assert event.gift_count == 3
    assert event.is_gift


def test_missing_timestamp_uses_now() -> None:
    before = time.time()
    event = DouyinWebcastAdapter().from_webcast_chat({"content": "x"})
    assert event.timestamp >= before
