"""Project configuration.

Every mutable/cache path is resolved inside the app root: the project directory
for a source run, the folder holding ``DanmakuPet.exe`` for the packaged build.
Read-only bundled files come from :mod:`danmaku_pet.paths`.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from danmaku_pet.paths import APP_ROOT, RESOURCE_ROOT
from danmaku_pet.persona import Persona, load_persona

ROOT = APP_ROOT


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


@dataclass(slots=True)
class Config:
    root: Path
    host: str
    port: int
    llm_mode: str
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    llm_timeout_s: float
    max_context_rounds: int
    stale_after_s: float
    queue_max: int
    tts_voice: str
    tts_rate: str
    tts_volume: str
    tts_pitch: str
    tts_cache_enabled: bool
    desktop_pet_enabled: bool
    auto_reply: bool
    auto_open_browser: bool
    pet_scale: float
    dafeiyu_package: Path
    assets_dir: Path
    tts_cache_dir: Path
    temp_dir: Path
    logs_dir: Path
    layout_path: Path
    node_temp_dir: Path
    default_user: str
    persona_path: Path
    persona: Persona

    @classmethod
    def load(cls) -> "Config":
        load_dotenv(APP_ROOT / ".env", override=False)
        package = RESOURCE_ROOT / "vendor" / "dafeiyu" / "package"
        temp_dir = APP_ROOT / "temp"
        assets_dir = APP_ROOT / "assets"
        persona_path = APP_ROOT / "data" / "persona.json"
        cfg = cls(
            root=APP_ROOT,
            host=os.getenv("APP_HOST", "127.0.0.1"),
            port=_env_int("APP_PORT", 8787),
            llm_mode=os.getenv("LLM_MODE", "auto").strip().lower(),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip(),
            llm_timeout_s=_env_float("LLM_TIMEOUT_SECONDS", 15.0),
            max_context_rounds=max(1, _env_int("MAX_CONTEXT_ROUNDS", 10)),
            stale_after_s=max(0.5, _env_float("STALE_AFTER_SECONDS", 3.0)),
            queue_max=max(5, _env_int("QUEUE_MAX", 50)),
            tts_voice=os.getenv("TTS_VOICE", "zh-CN-XiaoxiaoNeural").strip(),
            tts_rate=os.getenv("TTS_RATE", "+8%").strip(),
            tts_volume=os.getenv("TTS_VOLUME", "+0%").strip(),
            tts_pitch=os.getenv("TTS_PITCH", "+0Hz").strip(),
            tts_cache_enabled=_env_bool("TTS_CACHE", True),
            desktop_pet_enabled=_env_bool("DESKTOP_PET", True),
            auto_reply=_env_bool("AUTO_REPLY", False),
            auto_open_browser=_env_bool("AUTO_OPEN_BROWSER", True),
            pet_scale=min(1.4, max(0.55, _env_float("PET_SCALE", 0.8))),
            dafeiyu_package=package,
            assets_dir=assets_dir,
            tts_cache_dir=assets_dir / "tts_cache",
            temp_dir=temp_dir,
            logs_dir=temp_dir / "logs",
            layout_path=temp_dir / "dafeiyu-layout.json",
            node_temp_dir=APP_ROOT / ".cache" / "node-temp",
            default_user=os.getenv("DEFAULT_USER", "观众").strip() or "观众",
            persona_path=persona_path,
            persona=load_persona(persona_path),
        )
        cfg.ensure_dirs()
        cfg.seed_persona()
        return cfg

    def seed_persona(self) -> None:
        """Give a packaged build the persona that shipped with it.

        Source runs use the working copy directly; the frozen app starts with an
        empty ``data`` folder, so the bundled default is copied once and later
        edits in the web UI keep that file.
        """

        bundled = RESOURCE_ROOT / "data" / "persona.json"
        if self.persona_path.exists() or not bundled.is_file():
            return
        if bundled.resolve() == self.persona_path.resolve():
            return
        self.persona_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundled, self.persona_path)

    @property
    def effective_llm_mode(self) -> str:
        if self.llm_mode == "mock":
            return "mock"
        if self.llm_mode == "deepseek":
            return "deepseek"
        return "deepseek" if self.deepseek_api_key else "mock"

    @property
    def helper_path(self) -> Path:
        return self.dafeiyu_package / "runtime" / "helper.py"

    @property
    def dafeiyu_assets(self) -> Path:
        return self.dafeiyu_package / "assets"

    def ensure_dirs(self) -> None:
        for path in (
            self.assets_dir,
            self.tts_cache_dir,
            self.temp_dir,
            self.logs_dir,
            self.temp_dir / "dafeiyu-runtime",
            self.node_temp_dir,
            self.root / ".cache" / "localappdata",
            self.root / "data",
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
        return {
            "llm_mode": self.effective_llm_mode,
            "llm_requested_mode": self.llm_mode,
            "llm_key_configured": bool(self.deepseek_api_key),
            "model": self.deepseek_model if self.effective_llm_mode == "deepseek" else "mock-local",
            "deepseek_model": self.deepseek_model,
            "deepseek_base_url": self.deepseek_base_url,
            "stale_after_seconds": self.stale_after_s,
            "queue_max": self.queue_max,
            "max_context_rounds": self.max_context_rounds,
            "tts_voice": self.tts_voice,
            "desktop_pet_enabled": self.desktop_pet_enabled,
            "auto_reply": self.auto_reply,
            "auto_open_browser": self.auto_open_browser,
            "pet_scale": self.pet_scale,
            "tts_cache": str(self.tts_cache_dir),
            "persona_path": str(self.persona_path),
            "persona": self.persona.to_dict(),
        }
