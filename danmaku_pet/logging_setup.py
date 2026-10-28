"""Console (and optional file) logging with secret masking.

Cloud containers log to stdout only; the local client also keeps a rotating
file under ``temp/logs``.  Any value listed in ``secrets`` is replaced with
``***`` before a record reaches a handler, so tokens and API keys cannot leak
through an exception message or a config dump.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

MASK = "***"


def mask(value: object) -> str:
    """Render a secret for logs: never the value itself."""

    text = str(value or "")
    return MASK if text else ""


class SecretMaskFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self.secrets = tuple(sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True))

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.secrets:
            return True
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        masked = message
        for secret in self.secrets:
            masked = masked.replace(secret, MASK)
        if masked != message:
            record.msg = masked
            record.args = ()
        return True


def configure_root(
    *,
    level: int = logging.INFO,
    logs_dir: Path | None = None,
    secrets: Iterable[str] = (),
    marker: str = "_danmaku_pet_configured",
) -> None:
    """Idempotently attach handlers; ``logs_dir=None`` means stdout only."""

    root = logging.getLogger()
    if getattr(root, marker, False):
        return
    root.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    secret_filter = SecretMaskFilter(secrets)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(secret_filter)
    root.addHandler(console)

    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            logs_dir / "app.log",
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(secret_filter)
        root.addHandler(file_handler)

    setattr(root, marker, True)
    # Keep third-party transport noise out of the operator log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def setup_logging(
    logs_dir: Path | None,
    *,
    level: int = logging.INFO,
    secrets: Iterable[str] = (),
) -> None:
    """Backward-compatible helper used by the local client."""

    configure_root(level=level, logs_dir=logs_dir, secrets=secrets)


def setup_cloud_logging(
    *,
    level: int = logging.INFO,
    secrets: Iterable[str] = (),
) -> None:
    """Container logging: standard output only, no file, masked secrets."""

    configure_root(level=level, logs_dir=None, secrets=secrets, marker="_danmaku_cloud_logging")
