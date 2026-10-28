from __future__ import annotations

import time
from pathlib import Path

import pytest

from danmaku_pet.cloud.config import CloudConfig, ConfigError

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "temp" / "test-artifacts"


def load(monkeypatch: pytest.MonkeyPatch, **overrides) -> CloudConfig:
    data_dir = ARTIFACTS / f"cloud-config-{time.time_ns()}"
    data_dir.mkdir(parents=True, exist_ok=True)
    values = {
        "APP_ENV": "dev",
        "GATEWAY_MODE": "mock",
        "ADMIN_TOKEN": "admin-token-1234567890",
        "CLIENT_TOKEN": "client-token-1234567890",
        "DEFAULT_ROOM_ID": "room-default",
        "ENABLE_TEST_ENDPOINTS": "true",
        "LLM_MODE": "mock",
        "DEEPSEEK_API_KEY": "",
        "CLOUD_DATA_DIR": str(data_dir),
        "DOUYIN_VERIFY_MODE": "disabled",
        "DOUYIN_OPENAPI_BASE_URL": "",
        "DOUYIN_APP_ID": "",
        "DOUYIN_APP_SECRET": "",
        "MAX_CONCURRENT_PER_ROOM": "1",
        "STATE_BACKEND": "memory",
        "ALLOW_EPHEMERAL_STATE": "true",
        "REDIS_URL": "",
        "ALLOW_MOCK_GATEWAY": "true",
    }
    values.update({key: str(value) for key, value in overrides.items()})
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return CloudConfig.load(use_env_file=False)


def test_dev_mode_generates_ephemeral_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load(monkeypatch, ADMIN_TOKEN="", CLIENT_TOKEN="")
    assert config.admin_token_generated is True
    assert config.client_token_generated is True
    assert len(config.admin_token) >= 16
    assert len(config.client_token) >= 16
    status = config.public_status()
    assert config.admin_token not in str(status)
    assert config.client_token not in str(status)


def test_production_requires_an_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError) as exc:
        load(monkeypatch, APP_ENV="prod", ADMIN_TOKEN="", DOUYIN_VERIFY_MODE="hmac")
    assert "ADMIN_TOKEN" in str(exc.value)


def test_production_requires_a_client_token(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError) as exc:
        load(monkeypatch, APP_ENV="prod", CLIENT_TOKEN="", DOUYIN_VERIFY_MODE="hmac")
    assert "CLIENT_TOKEN" in str(exc.value)


def test_production_forbids_disabled_platform_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError) as exc:
        load(monkeypatch, APP_ENV="prod", DOUYIN_VERIFY_MODE="disabled")
    assert "verify" in str(exc.value).lower() or "disabled" in str(exc.value)


def test_douyin_cloud_gateway_requires_a_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError) as exc:
        load(monkeypatch, GATEWAY_MODE="douyin_cloud", DOUYIN_OPENAPI_BASE_URL="")
    assert "DOUYIN_OPENAPI_BASE_URL" in str(exc.value)


def test_production_turns_test_endpoints_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load(
        monkeypatch,
        APP_ENV="prod",
        ENABLE_TEST_ENDPOINTS="true",
        DOUYIN_VERIFY_MODE="hmac",
    )
    assert config.enable_test_endpoints is False


def test_invalid_environment_and_gateway_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError):
        load(monkeypatch, APP_ENV="staging")
    with pytest.raises(ConfigError):
        load(monkeypatch, GATEWAY_MODE="smoke-signal")


def test_gift_ids_and_blocked_words_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load(
        monkeypatch,
        GIFT_ID_MAP="id-1=嘉年华; id-2 = 火箭,",
        BLOCKED_WORDS="敏感词, 另一个|第三个",
    )
    assert config.gift_id_map == {"id-1": "嘉年华", "id-2": "火箭"}
    assert config.blocked_words == ("敏感词", "另一个", "第三个")


def test_concurrency_is_clamped_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load(monkeypatch, MAX_CONCURRENT_PER_ROOM="4")
    assert config.max_concurrent_per_room == 1


def test_display_timezone_must_be_a_valid_iana_name(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load(monkeypatch, DISPLAY_TIMEZONE="Asia/Shanghai")
    assert config.display_timezone == "Asia/Shanghai"
    assert config.public_status()["display_timezone"] == "Asia/Shanghai"
    with pytest.raises(ConfigError):
        load(monkeypatch, DISPLAY_TIMEZONE="Mars/Olympus")


def test_read_only_data_dir_does_not_block_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(CloudConfig, "ensure_dirs", boom)
    config = load(monkeypatch)
    # 人设回退到内置默认值，而不是启动失败。
    assert config.persona.name == "小鱼"


def test_production_requires_redis_state(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError) as exc:
        load(
            monkeypatch,
            APP_ENV="prod",
            DOUYIN_VERIFY_MODE="hmac",
            STATE_BACKEND="redis",
            REDIS_URL="",
        )
    assert "REDIS_URL" in str(exc.value)


def test_production_memory_state_needs_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ConfigError) as exc:
        load(
            monkeypatch,
            APP_ENV="prod",
            DOUYIN_VERIFY_MODE="hmac",
            STATE_BACKEND="memory",
            ALLOW_EPHEMERAL_STATE="false",
        )
    assert "STATE_BACKEND=memory" in str(exc.value)

    config = load(
        monkeypatch,
        APP_ENV="prod",
        DOUYIN_VERIFY_MODE="hmac",
        STATE_BACKEND="memory",
        ALLOW_EPHEMERAL_STATE="true",
    )
    assert config.state_backend == "memory"


def test_production_rejects_mock_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError) as exc:
        load(
            monkeypatch,
            APP_ENV="prod",
            DOUYIN_VERIFY_MODE="hmac",
            GATEWAY_MODE="mock",
            ALLOW_MOCK_GATEWAY="false",
        )
    assert "GATEWAY_MODE=mock" in str(exc.value)


def test_production_redis_config_is_accepted_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load(
        monkeypatch,
        APP_ENV="prod",
        DOUYIN_VERIFY_MODE="hmac",
        STATE_BACKEND="redis",
        REDIS_URL="redis://127.0.0.1:6379/0",
        GATEWAY_MODE="wss",
    )
    assert config.state_backend == "redis"
    status = config.public_status()
    assert status["state_backend"] == "redis"
    assert status["redis_configured"] is True
    assert "redis://127.0.0.1" not in str(status)


def test_viewer_data_is_hidden_from_logs_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load(monkeypatch)
    assert config.log_viewer_data is False
    config = load(monkeypatch, LOG_VIEWER_DATA="true")
    assert config.log_viewer_data is True


def test_public_status_never_contains_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load(
        monkeypatch,
        LLM_MODE="deepseek",
        DEEPSEEK_API_KEY="sk-super-secret",
        DOUYIN_APP_SECRET="app-secret",
        DOUYIN_VERIFY_SECRET="verify-secret",
    )
    text = str(config.public_status())
    assert "sk-super-secret" not in text
    assert "app-secret" not in text
    assert "verify-secret" not in text
    assert config.public_status()["llm_key_configured"] is True
    assert config.effective_llm_mode == "deepseek"
