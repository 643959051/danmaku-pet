from __future__ import annotations

import time

import pytest

from danmaku_pet.douyin.adapter import DouyinEventAdapter, MessageFormatError


def test_comment_mapping_with_official_shapes() -> None:
    event = DouyinEventAdapter().from_comment(
        {
            "room_id": "7123",
            "message_id": "msg-1",
            "user": {"nickname": "小满", "id": "u-1"},
            "content": "你好呀",
            "common": {"createTime": "1710000000000"},
        }
    )
    assert event.source == "douyin_comment"
    assert event.message_type == "comment"
    assert event.room_id == "7123"
    assert event.message_id == "msg-1"
    assert event.user == "小满"
    assert event.user_id == "u-1"
    assert event.text == "你好呀"
    assert 1710000000 <= event.timestamp < 1710000100


def test_gift_mapping_and_count() -> None:
    event = DouyinEventAdapter().from_gift(
        {
            "room_id": "7123",
            "user": {"nickname": "阿星"},
            "gift": {"id": 987, "name": "小心心"},
            "repeatCount": 3,
            "common": {"createTime": 1710000000000},
        }
    )
    assert event.source == "douyin_gift"
    assert event.message_type == "gift"
    assert event.gift_name == "小心心"
    assert event.gift_id == "987"
    assert event.gift_count == 3
    assert event.is_gift


def test_gift_with_only_id_still_matches_by_id() -> None:
    event = DouyinEventAdapter().from_gift(
        {"room_id": "r", "user": {"nickname": "A"}, "gift": {"id": "g-1"}}
    )
    assert event.gift_id == "g-1"
    assert event.gift_name == "礼物"


def test_field_aliases_are_tolerated() -> None:
    event = DouyinEventAdapter(default_room_id="fallback-room").from_comment(
        {
            "msgId": "alias-1",
            "user_info": {"display_name": "别名观众", "sec_openid": "open-1"},
            "text": "别名弹幕",
            "common_info": {"create_time": 1710000000},
        }
    )
    assert event.message_id == "alias-1"
    assert event.user == "别名观众"
    assert event.user_id == "open-1"
    assert event.room_id == "fallback-room"


def test_missing_comment_content_is_rejected() -> None:
    with pytest.raises(MessageFormatError):
        DouyinEventAdapter().from_comment({"user": {"nickname": "A"}})
    with pytest.raises(MessageFormatError):
        DouyinEventAdapter().from_comment({"content": "   "})


def test_gift_without_id_or_name_is_rejected() -> None:
    with pytest.raises(MessageFormatError):
        DouyinEventAdapter().from_gift({"user": {"nickname": "A"}, "gift": {}})


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(MessageFormatError):
        DouyinEventAdapter().normalize({}, kind="like")


def test_missing_timestamp_falls_back_to_now() -> None:
    before = time.time()
    event = DouyinEventAdapter().from_comment({"content": "x", "room_id": "r"})
    assert event.timestamp >= before


def test_bad_gift_count_falls_back_to_one() -> None:
    event = DouyinEventAdapter().from_gift(
        {"room_id": "r", "user": {"nickname": "A"}, "gift": {"name": "玫瑰"}, "repeatCount": "x"}
    )
    assert event.gift_count == 1
