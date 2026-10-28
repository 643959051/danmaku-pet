"""Local Windows client configuration.

The client is deliberately "dumb": it only knows where the cloud is, how to
talk to it, and how to render speech + motion locally.  All DeepSeek / persona /
gift-script state lives in :mod:`danmaku_pet.cloud.config`.

Every mutable path is resolved inside the app root (the project directory for a
source run, the folder holding ``DanmakuPet.exe`` for the packaged build).
Read-only bundled files come from :mod:`danmaku_pet.paths`.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from danmaku_pet.paths import APP_ROOT, RESOURCE_ROOT

ROOT = APP_ROOT

DEFAULT_CLOUD_URL = "ws://127.0.0.1:8000/ws/client"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _default_client_id() -> str:
    raw = os.getenv("CLIENT_ID", "").strip()
    if raw:
        return raw[:64]
    try:
        host = socket.gethostname() or "windows"
    except OSError:
        host = "windows"
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in host.lower())
    return f"client-{cleaned[:40] or 'local'}"


@dataclass(slots=True)
class Config:
    """Everything the local client needs; no secrets beyond the client token."""

    root: Path
    host: str
    port: int
    log_level: str
    # --- connection ------------------------------------------------------
    room_id: str
    client_id: str
    cloud_url: str
    client_token: str
    transport: str
    heartbeat_interval_s: float
    reconnect_min_s: float
    reconnect_max_s: float
    command_grace_s: float
    static_commands: Path | None
    # --- local execution -------------------------------------------------
    tts_voice: str
    tts_rate: str
    tts_volume: str
    tts_pitch: str
    tts_cache_enabled: bool
    desktop_pet_enabled: bool
    auto_open_browser: bool
    pet_scale: float
    default_user: str
    # --- paths -----------------------------------------------------------
    dafeiyu_package: Path
    assets_dir: Path
    tts_cache_dir: Path
    temp_dir: Path
    logs_dir: Path
    layout_path: Path
    node_temp_dir: Path
    # Raw key=value pairs handed over by the platform launcher (mapping to be
    # confirmed per 类目; unknown keys are kept for diagnostics).
    platform_params: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, *, use_env_file: bool = True) -> "Config":
        if use_env_file:
            load_dotenv(APP_ROOT / ".env", override=False)
        package = RESOURCE_ROOT / "vendor" / "dafeiyu" / "package"
        assets_dir = APP_ROOT / "assets"
        temp_dir = APP_ROOT / "temp"
        static_raw = os.getenv("STATIC_COMMANDS", "").strip()
        cfg = cls(
            root=APP_ROOT,
            host=os.getenv("APP_HOST", "127.0.0.1"),
            port=_env_int("APP_PORT", 8787),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            room_id=os.getenv("ROOM_ID", "").strip(),
            client_id=_default_client_id(),
            cloud_url=os.getenv("CLOUD_URL", DEFAULT_CLOUD_URL).strip() or DEFAULT_CLOUD_URL,
            client_token=os.getenv("CLIENT_TOKEN", "").strip(),
            transport=os.getenv("CLIENT_TRANSPORT", "websocket").strip().lower() or "websocket",
            heartbeat_interval_s=max(2.0, _env_float("HEARTBEAT_INTERVAL_SECONDS", 8.0)),
            reconnect_min_s=max(0.5, _env_float("RECONNECT_MIN_SECONDS", 1.0)),
            reconnect_max_s=max(2.0, _env_float("RECONNECT_MAX_SECONDS", 20.0)),
            command_grace_s=max(0.0, _env_float("COMMAND_GRACE_SECONDS", 0.5)),
            static_commands=Path(static_raw).expanduser() if static_raw else None,
            tts_voice=os.getenv("TTS_VOICE", "zh-CN-XiaoxiaoNeural").strip(),
            tts_rate=os.getenv("TTS_RATE", "+8%").strip(),
            tts_volume=os.getenv("TTS_VOLUME", "+0%").strip(),
            tts_pitch=os.getenv("TTS_PITCH", "+0Hz").strip(),
            tts_cache_enabled=_env_bool("TTS_CACHE", True),
            desktop_pet_enabled=_env_bool("DESKTOP_PET", True),
            auto_open_browser=_env_bool("AUTO_OPEN_BROWSER", True),
            pet_scale=min(1.4, max(0.55, _env_float("PET_SCALE", 0.8))),
            default_user=os.getenv("DEFAULT_USER", "观众").strip() or "观众",
            dafeiyu_package=package,
            assets_dir=assets_dir,
            tts_cache_dir=assets_dir / "tts_cache",
            temp_dir=temp_dir,
            logs_dir=temp_dir / "logs",
            layout_path=temp_dir / "dafeiyu-layout.json",
            node_temp_dir=APP_ROOT / ".cache" / "node-temp",
        )
        cfg.ensure_dirs()
        return cfg

    @property
    def helper_path(self) -> Path:
        return self.dafeiyu_package / "runtime" / "helper.py"

    @property
    def dafeiyu_assets(self) -> Path:
        return self.dafeiyu_package / "assets"

    @property
    def bound(self) -> bool:
        return bool(self.room_id and self.cloud_url)

    def ensure_dirs(self) -> None:
        for path in (
            self.assets_dir,
            self.tts_cache_dir,
            self.temp_dir,
            self.logs_dir,
            self.temp_dir / "dafeiyu-runtime",
            self.node_temp_dir,
            self.root / ".cache" / "localappdata",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def env_for_child(self) -> dict[str, str]:
        env = os.environ.copy()
        runtime = self.temp_dir / "dafeiyu-runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        env.update(
            {
                "TEMP": str(runtime),
                "TMP": str(runtime),
                "LOCALAPPDATA": str(self.root / ".cache" / "localappdata"),
                "DSH_DAFEIYU_LAYOUT_PATH": str(self.layout_path),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "QT_LOGGING_RULES": "qt.qpa.*=false",
            }
        )
        return env

    def public_status(self) -> dict[str, Any]:
        """Safe to show in the local page, logs and support tickets."""

        return {
            "room_id": self.room_id,
            "client_id": self.client_id,
            "cloud_url": self.cloud_url,
            "host": self.host,
            "port": self.port,
            "transport": self.transport,
            "cloud_token_configured": bool(self.client_token),
            "heartbeat_interval_s": self.heartbeat_interval_s,
            "reconnect_min_s": self.reconnect_min_s,
            "reconnect_max_s": self.reconnect_max_s,
            "command_grace_s": self.command_grace_s,
            "tts_voice": self.tts_voice,
            "desktop_pet_enabled": self.desktop_pet_enabled,
            "auto_open_browser": self.auto_open_browser,
            "pet_scale": self.pet_scale,
            "tts_cache": str(self.tts_cache_dir),
            "static_commands": str(self.static_commands) if self.static_commands else "",
            "platform_param_keys": sorted(self.platform_params),
        }
