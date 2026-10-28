"""Cloud backend configuration (抖音云 Docker container).

Only this process may hold model keys, platform app credentials and the
gateway credentials.  Nothing here is ever written back to a ``.env`` file:
the container receives all secrets from the platform's secret management, and
the admin API is read-only with respect to credentials.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from danmaku_pet.paths import APP_ROOT, RESOURCE_ROOT
from danmaku_pet.persona import Persona, load_persona

logger = logging.getLogger(__name__)

ROOT = APP_ROOT
VALID_ENVIRONMENTS = ("dev", "prod")
VALID_GATEWAY_MODES = ("mock", "wss", "douyin_cloud")
VALID_LLM_MODES = ("auto", "deepseek", "mock")
VALID_VERIFY_MODES = ("disabled", "header", "hmac")
VALID_STATE_BACKENDS = ("memory", "redis")


class ConfigError(RuntimeError):
    """The container cannot start safely with the supplied configuration."""


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


def _env_text(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _redis_host_port(address: str, default_port: int = 6379) -> tuple[str, int]:
    """Parse ``10.1.1.123/3306``, ``host:6379`` or ``[::1]:6379``."""

    raw = (address or "").strip()
    if not raw:
        return "", default_port
    if "://" in raw:
        parts = urlsplit(raw)
        return parts.hostname or "", parts.port or default_port
    if raw.startswith("["):
        closing = raw.find("]")
        if closing > 0:
            host = raw[1:closing]
            tail = raw[closing + 1 :].lstrip(":")
            if tail.isdigit():
                return host, int(tail)
            return host, default_port
    if "/" in raw:
        host, _, tail = raw.rpartition("/")
        if host and tail.isdigit():
            return host, int(tail)
    if ":" in raw:
        host, _, tail = raw.rpartition(":")
        if host and tail.isdigit():
            return host, int(tail)
    return raw, default_port


def _redis_url_from_platform_env() -> str:
    """Build REDIS_URL from 抖音云's injected DB_REDIS_* variables.

    The platform injects ``DB_REDIS_ADDRESS`` (e.g. ``10.1.1.123/3306``),
    ``DB_REDIS_ACCOUNT`` and ``DB_REDIS_PASSWORD``.  An explicit REDIS_URL
    always wins so operators can still override it for a local tunnel.
    """

    explicit = _env_text("REDIS_URL")
    if explicit:
        return explicit

    address = _env_text("DB_REDIS_ADDRESS")
    if not address:
        return ""
    if address.startswith(("redis://", "rediss://")):
        return address

    default_port = _env_int("DB_REDIS_PORT", 6379)
    host, port = _redis_host_port(address, default_port)
    if not host:
        return ""
    account = _env_text("DB_REDIS_ACCOUNT")
    password = _env_text("DB_REDIS_PASSWORD")
    database = max(0, _env_int("DB_REDIS_DB", 0))
    scheme = "rediss" if _env_bool("DB_REDIS_TLS", False) else "redis"

    # Redis 6's default ACL user still supports the classic password-only AUTH;
    # omitting ``default:`` also keeps the URL usable with Redis 5.
    auth = ""
    if password:
        if account and account.lower() != "default":
            auth = f"{quote(account, safe='')}:{quote(password, safe='')}@"
        else:
            auth = f":{quote(password, safe='')}@"
    elif account and account.lower() != "default":
        auth = f"{quote(account, safe='')}@"
    host_part = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{scheme}://{auth}{host_part}:{int(port)}/{database}"


def _parse_gift_ids(raw: str) -> dict[str, str]:
    """``giftId=嘉年华,giftId2=火箭`` -> mapping; malformed pairs are ignored."""

    mapping: dict[str, str] = {}
    for chunk in (raw or "").replace(";", ",").split(","):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and value:
            mapping[key] = value
    return mapping


def _parse_words(raw: str) -> tuple[str, ...]:
    words = []
    for chunk in (raw or "").replace(";", ",").replace("|", ",").split(","):
        word = chunk.strip()
        if word:
            words.append(word)
    return tuple(dict.fromkeys(words))


@dataclass(slots=True)
class DouyinSettings:
    """Platform integration settings.

    Every endpoint path is supplied by the operator; nothing is hardcoded while
    the platform capability is unconfirmed (改造计划 阶段 0).  An empty path
    raises a clear ``UnsupportedPlatformOperation`` when it is actually used.
    """

    base_url: str = ""
    app_id: str = ""
    app_secret: str = ""
    timeout_s: float = 10.0
    retries: int = 2
    verify_mode: str = "hmac"
    verify_token: str = ""
    verify_secret: str = ""
    token_header: str = "x-douyin-token"
    signature_header: str = "x-douyin-signature"
    timestamp_header: str = "x-douyin-timestamp"
    timestamp_tolerance_s: int = 300
    token_path: str = ""
    task_start_path: str = ""
    task_stop_path: str = ""
    task_status_path: str = ""
    command_push_path: str = ""
    interrupt_path: str = ""
    content_security_enabled: bool = True
    content_security_base_url: str = "https://developer.toutiao.com"
    content_security_fail_closed: bool = True

    @classmethod
    def from_env(cls) -> "DouyinSettings":
        mode = _env_text("DOUYIN_VERIFY_MODE", "hmac").lower()
        if mode not in VALID_VERIFY_MODES:
            raise ConfigError(
                f"DOUYIN_VERIFY_MODE 必须是 {'/'.join(VALID_VERIFY_MODES)}，收到 {mode!r}"
            )
        is_dev = _env_text("APP_ENV", "prod").lower() == "dev"
        return cls(
            base_url=_env_text("DOUYIN_OPENAPI_BASE_URL").rstrip("/"),
            app_id=_env_text("DOUYIN_APP_ID"),
            app_secret=_env_text("DOUYIN_APP_SECRET"),
            timeout_s=max(1.0, _env_float("DOUYIN_TIMEOUT_SECONDS", 10.0)),
            retries=max(0, _env_int("DOUYIN_RETRIES", 2)),
            verify_mode=mode,
            verify_token=_env_text("DOUYIN_VERIFY_TOKEN"),
            verify_secret=_env_text("DOUYIN_VERIFY_SECRET"),
            token_header=_env_text("DOUYIN_VERIFY_TOKEN_HEADER", "x-douyin-token"),
            signature_header=_env_text(
                "DOUYIN_SIGNATURE_HEADER", "x-douyin-signature"
            ),
            timestamp_header=_env_text(
                "DOUYIN_TIMESTAMP_HEADER", "x-douyin-timestamp"
            ),
            timestamp_tolerance_s=max(30, _env_int("DOUYIN_TIMESTAMP_TOLERANCE_SECONDS", 300)),
            token_path=_env_text("DOUYIN_TOKEN_PATH"),
            task_start_path=_env_text("DOUYIN_TASK_START_PATH"),
            task_stop_path=_env_text("DOUYIN_TASK_STOP_PATH"),
            task_status_path=_env_text("DOUYIN_TASK_STATUS_PATH"),
            command_push_path=_env_text("DOUYIN_COMMAND_PUSH_PATH"),
            interrupt_path=_env_text("DOUYIN_COMMAND_INTERRUPT_PATH"),
            content_security_enabled=_env_bool(
                "DOUYIN_CONTENT_SECURITY_ENABLED", not is_dev
            ),
            content_security_base_url=_env_text(
                "DOUYIN_CONTENT_SECURITY_BASE_URL", "https://developer.toutiao.com"
            ).rstrip("/"),
            content_security_fail_closed=_env_bool(
                "DOUYIN_CONTENT_SECURITY_FAIL_CLOSED", True
            ),
        )

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.app_id and self.app_secret)


@dataclass(slots=True)
class CloudConfig:
    root: Path
    data_dir: Path
    persona_path: Path
    host: str
    port: int
    environment: str
    log_level: str
    # --- decision ---------------------------------------------------------
    llm_mode: str
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    llm_timeout_s: float
    persona: Persona
    # Timezone used when telling the model "what time it is" (the container is
    # usually UTC, the streamer is not).
    display_timezone: str
    # --- rooms ------------------------------------------------------------
    max_context_rounds: int
    queue_max: int
    max_rooms: int
    room_idle_timeout_s: float
    stale_after_s: float
    comment_expires_s: float
    gift_expires_s: float
    result_timeout_s: float
    dedup_ttl_s: float
    max_concurrent_per_room: int
    auto_reply: bool
    default_room_id: str
    # --- auth -------------------------------------------------------------
    admin_token: str
    client_token: str
    admin_token_generated: bool
    client_token_generated: bool
    # --- gateway ----------------------------------------------------------
    gateway_mode: str
    mock_auto_ack: bool
    mock_ack_delay_s: float
    # --- external state (stateless requirement) ---------------------------
    state_backend: str
    redis_url: str
    room_ttl_s: float
    lock_timeout_s: float
    lock_ttl_s: float
    ephemeral_state_allowed: bool
    # --- platform ---------------------------------------------------------
    douyin: DouyinSettings = field(default_factory=DouyinSettings)
    # --- content safety ---------------------------------------------------
    blocked_words: tuple[str, ...] = ()
    max_comment_chars: int = 200
    max_reply_chars: int = 240
    # --- dev-only endpoints ----------------------------------------------
    enable_test_endpoints: bool = False
    gift_id_map: dict[str, str] = field(default_factory=dict)
    # Viewer nicknames/text are personal data; keep them out of logs unless the
    # operator explicitly opts in (改造计划 阶段 2 / 7).
    log_viewer_data: bool = False

    @classmethod
    def load(cls, *, use_env_file: bool = True) -> "CloudConfig":
        # The container receives everything from the platform.  Locally, cloud
        # settings live in a separate ".env.cloud" so the 开播电脑 client's .env
        # (TTS, room binding) can never leak into cloud decisions.
        if use_env_file:
            load_dotenv(APP_ROOT / ".env.cloud", override=False)

        environment = _env_text("APP_ENV", "prod").lower()
        if environment not in VALID_ENVIRONMENTS:
            raise ConfigError(
                f"APP_ENV 必须是 {'/'.join(VALID_ENVIRONMENTS)}，收到 {environment!r}"
            )
        is_dev = environment == "dev"

        gateway_mode = _env_text("GATEWAY_MODE", "mock" if is_dev else "douyin_cloud").lower()
        if gateway_mode not in VALID_GATEWAY_MODES:
            raise ConfigError(
                f"GATEWAY_MODE 必须是 {'/'.join(VALID_GATEWAY_MODES)}，收到 {gateway_mode!r}"
            )
        llm_mode = _env_text("LLM_MODE", "auto").lower()
        if llm_mode not in VALID_LLM_MODES:
            raise ConfigError(f"LLM_MODE 必须是 {'/'.join(VALID_LLM_MODES)}，收到 {llm_mode!r}")

        admin_token = _env_text("ADMIN_TOKEN")
        client_token = _env_text("CLIENT_TOKEN")
        if not is_dev:
            if len(admin_token) < 16:
                raise ConfigError(
                    "正式环境必须配置至少 16 位的 ADMIN_TOKEN（在抖音云密钥管理中设置）"
                )
            if not client_token:
                raise ConfigError("正式环境必须配置 CLIENT_TOKEN，用于本地客户端注册")
        admin_generated = not admin_token
        client_generated = not client_token
        if not admin_token:
            # Dev convenience: an ephemeral token, printed once at startup.
            import secrets

            admin_token = secrets.token_urlsafe(24)
        if not client_token:
            import secrets

            client_token = secrets.token_urlsafe(24)

        douyin = DouyinSettings.from_env()
        if not is_dev and douyin.verify_mode == "disabled":
            raise ConfigError("正式环境禁止 DOUYIN_VERIFY_MODE=disabled，请配置 header 或 hmac")
        if gateway_mode == "douyin_cloud" and not douyin.base_url:
            raise ConfigError(
                "GATEWAY_MODE=douyin_cloud 需要 DOUYIN_OPENAPI_BASE_URL；"
                "平台能力确认前请使用 GATEWAY_MODE=wss 或 mock"
            )
        if douyin.content_security_enabled and not (
            douyin.app_id and douyin.app_secret
        ):
            raise ConfigError(
                "开启 DOUYIN_CONTENT_SECURITY_ENABLED 需要 DOUYIN_APP_ID 和 "
                "DOUYIN_APP_SECRET（用于 getAccessToken + 文本内容安全检测）"
            )

        state_backend = _env_text("STATE_BACKEND", "memory" if is_dev else "redis").lower()
        if state_backend not in VALID_STATE_BACKENDS:
            raise ConfigError(
                f"STATE_BACKEND 必须是 {'/'.join(VALID_STATE_BACKENDS)}，收到 {state_backend!r}"
            )
        redis_url = _redis_url_from_platform_env()
        ephemeral_allowed = _env_bool("ALLOW_EPHEMERAL_STATE", False)
        if state_backend == "redis" and not redis_url:
            raise ConfigError(
                "STATE_BACKEND=redis 需要 REDIS_URL，或抖音云注入的 "
                "DB_REDIS_ADDRESS / DB_REDIS_ACCOUNT / DB_REDIS_PASSWORD"
            )
        if not is_dev and state_backend == "memory" and not ephemeral_allowed:
            raise ConfigError(
                "正式环境禁止 STATE_BACKEND=memory（平台要求服务无状态）；"
                "请配置 REDIS_URL，或显式设置 ALLOW_EPHEMERAL_STATE=true 作为临时降级"
            )
        if not is_dev and gateway_mode == "mock" and not _env_bool(
            "ALLOW_MOCK_GATEWAY", False
        ):
            raise ConfigError(
                "正式环境禁止 GATEWAY_MODE=mock（mock 只用于开发/单元测试）；"
                "请使用 douyin_cloud 或 wss"
            )

        data_dir = Path(_env_text("CLOUD_DATA_DIR", str(APP_ROOT / "data"))).expanduser()
        persona_path = Path(
            _env_text("PERSONA_PATH", str(data_dir / "persona.json"))
        ).expanduser()

        enable_tests = _env_bool("ENABLE_TEST_ENDPOINTS", is_dev)
        if enable_tests and not is_dev and not _env_bool("ALLOW_TEST_ENDPOINTS_IN_PROD", False):
            enable_tests = False

        max_concurrent = _env_int("MAX_CONCURRENT_PER_ROOM", 1)
        if max_concurrent != 1:
            max_concurrent = 1  # v1: one performance at a time per room

        display_timezone = _env_text("DISPLAY_TIMEZONE", "Asia/Shanghai")
        try:
            ZoneInfo(display_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(
                f"DISPLAY_TIMEZONE={display_timezone!r} 不是有效的 IANA 时区"
                "（镜像需要 tzdata；例如 Asia/Shanghai）"
            ) from exc

        cfg = cls(
            root=APP_ROOT,
            data_dir=data_dir,
            persona_path=persona_path,
            host=_env_text("CLOUD_HOST", "0.0.0.0"),
            port=_env_int("CLOUD_PORT", 8000),
            environment=environment,
            log_level=_env_text("LOG_LEVEL", "INFO").upper() or "INFO",
            llm_mode=llm_mode,
            deepseek_api_key=_env_text("DEEPSEEK_API_KEY"),
            deepseek_base_url=_env_text("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            deepseek_model=_env_text("DEEPSEEK_MODEL", "deepseek-chat"),
            llm_timeout_s=max(1.0, _env_float("LLM_TIMEOUT_SECONDS", 15.0)),
            persona=Persona(),
            display_timezone=display_timezone,
            max_context_rounds=max(1, _env_int("MAX_CONTEXT_ROUNDS", 10)),
            queue_max=max(5, _env_int("QUEUE_MAX", 50)),
            max_rooms=max(1, _env_int("MAX_ROOMS", 50)),
            room_idle_timeout_s=max(60.0, _env_float("ROOM_IDLE_TIMEOUT_SECONDS", 1800.0)),
            stale_after_s=max(0.5, _env_float("STALE_AFTER_SECONDS", 3.0)),
            comment_expires_s=max(2.0, _env_float("COMMENT_EXPIRES_SECONDS", 8.0)),
            gift_expires_s=max(5.0, _env_float("GIFT_EXPIRES_SECONDS", 30.0)),
            result_timeout_s=max(5.0, _env_float("RESULT_TIMEOUT_SECONDS", 25.0)),
            dedup_ttl_s=max(30.0, _env_float("DEDUP_TTL_SECONDS", 600.0)),
            max_concurrent_per_room=max_concurrent,
            auto_reply=_env_bool("AUTO_REPLY", False),
            default_room_id=_env_text("DEFAULT_ROOM_ID"),
            admin_token=admin_token,
            client_token=client_token,
            admin_token_generated=admin_generated,
            client_token_generated=client_generated,
            gateway_mode=gateway_mode,
            mock_auto_ack=_env_bool("GATEWAY_MOCK_AUTO_ACK", True),
            mock_ack_delay_s=max(0.0, _env_float("GATEWAY_MOCK_ACK_DELAY_SECONDS", 0.5)),
            state_backend=state_backend,
            redis_url=redis_url,
            room_ttl_s=max(60.0, _env_float("ROOM_STATE_TTL_SECONDS", 1800.0)),
            lock_timeout_s=max(1.0, _env_float("ROOM_LOCK_TIMEOUT_SECONDS", 20.0)),
            lock_ttl_s=max(10.0, _env_float("ROOM_LOCK_TTL_SECONDS", 60.0)),
            ephemeral_state_allowed=ephemeral_allowed,
            douyin=douyin,
            blocked_words=_parse_words(_env_text("BLOCKED_WORDS")),
            max_comment_chars=max(20, _env_int("MAX_COMMENT_CHARS", 200)),
            max_reply_chars=max(20, _env_int("MAX_REPLY_CHARS", 240)),
            enable_test_endpoints=enable_tests,
            gift_id_map=_parse_gift_ids(_env_text("GIFT_ID_MAP")),
            log_viewer_data=_env_bool("LOG_VIEWER_DATA", False),
        )
        try:
            cfg.ensure_dirs()
        except OSError as exc:
            # 无状态/只读文件系统也应该能启动：人设回退到镜像内默认值。
            logger.warning("数据目录不可写，人设将使用镜像内默认值: %s", exc)
        if is_dev:
            cfg.persona = load_persona(cfg.persona_path)
            cfg.seed_persona()
        else:
            # 正式环境的人设从 Redis 读取（见 server lifespan）；本地文件不再是事实来源。
            cfg.persona = Persona()
        return cfg

    # ------------------------------------------------------------------ paths
    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.persona_path.parent):
            path.mkdir(parents=True, exist_ok=True)

    def seed_persona(self) -> None:
        """Copy the bundled default persona once; later edits are preserved.

        Startup must not fail just because ``/app/data`` is read-only: the
        bundled default is already loaded by :func:`load_persona`, so seeding
        is a best-effort convenience only.
        """

        if self.persona_path.exists():
            return
        candidates = (
            RESOURCE_ROOT / "data" / "persona.example.json",
            RESOURCE_ROOT / "data" / "persona.json",
        )
        for bundled in candidates:
            if not bundled.is_file():
                continue
            try:
                if bundled.resolve() == self.persona_path.resolve():
                    continue
                shutil.copyfile(bundled, self.persona_path)
                return
            except OSError as exc:
                logger.warning("播种默认人设失败（继续使用内置默认值）: %s", exc)
                return

    # ------------------------------------------------------------------ llm
    @property
    def effective_llm_mode(self) -> str:
        if self.llm_mode == "mock":
            return "mock"
        if self.llm_mode == "deepseek":
            return "deepseek"
        return "deepseek" if self.deepseek_api_key else "mock"

    # ------------------------------------------------------------------ safe view
    def public_status(self) -> dict[str, Any]:
        """Never includes tokens, app secrets or the model key."""

        return {
            "environment": self.environment,
            "gateway_mode": self.gateway_mode,
            "state_backend": self.state_backend,
            "redis_configured": bool(self.redis_url),
            "ephemeral_state_allowed": self.ephemeral_state_allowed,
            "content_security_enabled": self.douyin.content_security_enabled,
            "content_security_fail_closed": self.douyin.content_security_fail_closed,
            "display_timezone": self.display_timezone,
            "llm_mode": self.effective_llm_mode,
            "llm_requested_mode": self.llm_mode,
            "llm_key_configured": bool(self.deepseek_api_key),
            "model": self.deepseek_model if self.effective_llm_mode == "deepseek" else "mock-local",
            "deepseek_base_url": self.deepseek_base_url,
            "auto_reply": self.auto_reply,
            "stale_after_seconds": self.stale_after_s,
            "comment_expires_seconds": self.comment_expires_s,
            "gift_expires_seconds": self.gift_expires_s,
            "result_timeout_seconds": self.result_timeout_s,
            "queue_max": self.queue_max,
            "max_context_rounds": self.max_context_rounds,
            "max_concurrent_per_room": self.max_concurrent_per_room,
            "max_rooms": self.max_rooms,
            "default_room_id": self.default_room_id,
            "test_endpoints": self.enable_test_endpoints,
            "log_viewer_data": self.log_viewer_data,
            "douyin": {
                "configured": self.douyin.configured,
                "verify_mode": self.douyin.verify_mode,
                "base_url": self.douyin.base_url,
                "command_push_configured": bool(self.douyin.command_push_path),
            },
            "persona": self.persona.to_dict(),
            "persona_path": str(self.persona_path),
            "gift_id_map_size": len(self.gift_id_map),
        }

    def as_json(self) -> str:
        return json.dumps(self.public_status(), ensure_ascii=False, indent=2)
