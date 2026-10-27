from __future__ import annotations

import time
from pathlib import Path

from danmaku_pet.settings_store import update_env_file


def test_update_env_preserves_comments_and_unrelated_keys() -> None:
    directory = Path(__file__).resolve().parent.parent / "temp" / "test-artifacts" / f"env-{time.time_ns()}"
    directory.mkdir(parents=True, exist_ok=True)
    env = directory / ".env"
    env.write_text(
        "# keep this comment\nTTS_VOICE=zh-CN-XiaoxiaoNeural\nDEEPSEEK_API_KEY=old\n",
        encoding="utf-8",
    )
    update_env_file(
        env,
        {
            "DEEPSEEK_API_KEY": "new-secret",
            "LLM_MODE": "deepseek",
        },
    )
    text = env.read_text(encoding="utf-8")
    assert "# keep this comment" in text
    assert "TTS_VOICE=zh-CN-XiaoxiaoNeural" in text
    assert "DEEPSEEK_API_KEY=new-secret" in text
    assert "DEEPSEEK_API_KEY=old" not in text
    assert "LLM_MODE=deepseek" in text
    assert not (directory / ".env.tmp").exists()


def test_update_env_appends_missing_keys() -> None:
    directory = Path(__file__).resolve().parent.parent / "temp" / "test-artifacts" / f"env2-{time.time_ns()}"
    directory.mkdir(parents=True, exist_ok=True)
    env = directory / ".env"
    env.write_text("LLM_MODE=auto\n", encoding="utf-8")
    update_env_file(env, {"DEEPSEEK_MODEL": "deepseek-chat"})
    text = env.read_text(encoding="utf-8")
    assert "LLM_MODE=auto" in text
    assert "DEEPSEEK_MODEL=deepseek-chat" in text
