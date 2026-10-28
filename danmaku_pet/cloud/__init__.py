"""Cloud backend package: receives official interaction data, decides, and
sends :class:`~danmaku_pet.shared.commands.PerformCommand` to the local client.

Entry point: ``python -m danmaku_pet.cloud``.
"""

from danmaku_pet.cloud.config import CloudConfig, ConfigError

__all__ = ["CloudConfig", "ConfigError"]
