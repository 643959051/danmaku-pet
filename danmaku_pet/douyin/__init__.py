"""Official Douyin interaction integration (comments, gifts, cloud gateway).

Nothing in this package hardcodes platform endpoint paths or field names:
those live in configuration until the platform permission and field samples are
confirmed (改造计划 阶段 0 / 阶段 5).
"""

from danmaku_pet.douyin.adapter import DouyinEventAdapter, MessageFormatError
from danmaku_pet.douyin.dedup import MessageDeduplicator, event_dedup_key
from danmaku_pet.douyin.openapi import (
    DouyinAPIError,
    DouyinOpenAPI,
    PlatformNotConfigured,
)
from danmaku_pet.douyin.verifier import MessageVerifier, VerificationError

__all__ = [
    "DouyinAPIError",
    "DouyinEventAdapter",
    "DouyinOpenAPI",
    "MessageDeduplicator",
    "MessageFormatError",
    "MessageVerifier",
    "PlatformNotConfigured",
    "VerificationError",
    "event_dedup_key",
]
