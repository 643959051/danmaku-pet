"""Shared fixtures: isolate the cloud config from the developer's env file.

Tests never hit the network and never read the workspace ``.env`` / ``.env.cloud``:
``load(use_env_file=False)`` plus explicitly patched variables makes the result
deterministic on every machine.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from danmaku_pet.cloud.config import CloudConfig

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "temp" / "test-artifacts"


@pytest.fixture
def workspace() -> Path:
    """A unique workspace directory.

    We deliberately avoid pytest's ``tmp_path``: on this Windows sandbox a
    directory created with an explicit security descriptor can end up outside
    the sandbox's own grant, which makes the fixture fail before the test runs.
    """

    path = ARTIFACTS / f"ws-{time.time_ns()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def cloud_config(monkeypatch: pytest.MonkeyPatch) -> CloudConfig:
    data_dir = ARTIFACTS / f"cloud-{time.time_ns()}"
    data_dir.mkdir(parents=True, exist_ok=True)
    values = {
        "APP_ENV": "dev",
        "GATEWAY_MODE": "mock",
        "ADMIN_TOKEN": "test-admin-token-1234567890",
        "CLIENT_TOKEN": "test-client-token-1234567890",
        "DEFAULT_ROOM_ID": "room-default",
        "ENABLE_TEST_ENDPOINTS": "true",
        "ALLOW_TEST_ENDPOINTS_IN_PROD": "false",
        "LLM_MODE": "mock",
        "DEEPSEEK_API_KEY": "",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
        "DEEPSEEK_MODEL": "deepseek-chat",
        "DISPLAY_TIMEZONE": "Asia/Shanghai",
        "AUTO_REPLY": "false",
        "LOG_VIEWER_DATA": "false",
        "CLOUD_DATA_DIR": str(data_dir),
        "GIFT_ID_MAP": "",
        "BLOCKED_WORDS": "",
        "DOUYIN_VERIFY_MODE": "disabled",
        "DOUYIN_OPENAPI_BASE_URL": "",
        "DOUYIN_TASK_START_PATH": "",
        "DOUYIN_TASK_STOP_PATH": "",
        "DOUYIN_TASK_STATUS_PATH": "",
        "DOUYIN_COMMAND_PUSH_PATH": "",
        "DOUYIN_COMMAND_INTERRUPT_PATH": "",
        "DOUYIN_TOKEN_PATH": "",
        "GATEWAY_MOCK_AUTO_ACK": "false",
        "GATEWAY_MOCK_ACK_DELAY_SECONDS": "0",
        "STATE_BACKEND": "memory",
        "ALLOW_EPHEMERAL_STATE": "true",
        "REDIS_URL": "",
        "DB_REDIS_ADDRESS": "",
        "DB_REDIS_ACCOUNT": "",
        "DB_REDIS_PASSWORD": "",
        "DOUYIN_CONTENT_SECURITY_ENABLED": "false",
        "COMMENT_EXPIRES_SECONDS": "8",
        "GIFT_EXPIRES_SECONDS": "30",
        "RESULT_TIMEOUT_SECONDS": "5",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    cfg = CloudConfig.load(use_env_file=False)
    assert cfg.environment == "dev"
    assert cfg.effective_llm_mode == "mock"
    return cfg
