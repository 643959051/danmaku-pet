"""Cloud/local shared protocol: events, plans, commands and wire helpers.

This package is the *only* contract between the cloud backend (decision) and
the Windows client (execution).  Neither side may import the other's modules.
"""

from danmaku_pet.shared.commands import (
    PERFORM_STATUSES,
    TERMINAL_STATUSES,
    PerformCommand,
    PerformResult,
    PerformStatus,
    speech_text_for,
)
from danmaku_pet.shared.events import (
    MESSAGE_TYPES,
    SOURCES,
    Event,
    MessageType,
    Source,
)
from danmaku_pet.shared.plans import (
    EMOTIONS,
    MOTIONS,
    ActionPlan,
    DecisionResult,
    Emotion,
    Motion,
    Priority,
    estimate_speech_ms,
)
from danmaku_pet.shared.protocol import (
    MAX_SUPPORTED_PROTOCOL,
    MIN_SUPPORTED_PROTOCOL,
    PROTOCOL_VERSION,
    DedupCache,
    Envelope,
    ProtocolError,
    VersionMismatch,
    check_protocol_version,
    decode_message,
    encode_message,
    make_command_id,
)

__all__ = [
    "ActionPlan",
    "DedupCache",
    "DecisionResult",
    "EMOTIONS",
    "Emotion",
    "Envelope",
    "Event",
    "MAX_SUPPORTED_PROTOCOL",
    "MESSAGE_TYPES",
    "MIN_SUPPORTED_PROTOCOL",
    "MOTIONS",
    "MessageType",
    "Motion",
    "PERFORM_STATUSES",
    "PROTOCOL_VERSION",
    "PerformCommand",
    "PerformResult",
    "PerformStatus",
    "Priority",
    "ProtocolError",
    "SOURCES",
    "Source",
    "TERMINAL_STATUSES",
    "VersionMismatch",
    "check_protocol_version",
    "decode_message",
    "encode_message",
    "estimate_speech_ms",
    "make_command_id",
    "speech_text_for",
]
