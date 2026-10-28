from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def test_dockerfile_lives_at_the_repository_root() -> None:
    assert (ROOT / "Dockerfile").is_file()
    assert not (ROOT / "deploy" / "Dockerfile").exists()
    assert (ROOT / ".dockerignore").is_file()
    assert not (ROOT / "deploy" / ".dockerignore").exists()


def test_cloud_image_excludes_the_windows_stack() -> None:
    dockerfile = read("Dockerfile")
    assert "requirements-cloud.txt" in dockerfile
    for forbidden in ("PySide6", "pygame", "edge-tts", "vendor/dafeiyu", "COPY .env"):
        assert forbidden not in dockerfile, f"云端镜像不应包含 {forbidden}"
    assert "HEALTHCHECK" in dockerfile
    assert "/v1/ping" in dockerfile
    assert "USER appuser" in dockerfile
    assert "danmaku_pet.cloud" in read("run.sh")


def test_dockerfile_keeps_the_runtime_contract() -> None:
    dockerfile = read("Dockerfile")
    assert "FROM python:3.11-slim" in dockerfile
    assert "APP_ENV=prod" in dockerfile
    assert "CLOUD_HOST=0.0.0.0" in dockerfile
    assert "CLOUD_PORT=8000" in dockerfile
    assert "EXPOSE 8000" in dockerfile
    assert "USER appuser" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "http://127.0.0.1:8000/v1/ping" in dockerfile
    assert "COPY run.sh /opt/application/run.sh" in dockerfile
    assert "chmod a+x /opt/application/run.sh" in dockerfile
    assert 'CMD ["/opt/application/run.sh"]' in dockerfile

    run_sh = read("run.sh")
    assert run_sh.startswith("#!/bin/sh")
    assert "--port 8000" in run_sh
    assert "danmaku_pet.cloud" in run_sh


def test_dockerfile_copy_sources_are_root_relative_and_exist() -> None:
    dockerfile = read("Dockerfile")
    copies = [line.strip() for line in dockerfile.splitlines() if line.strip().startswith("COPY ")]
    assert copies, "Dockerfile 应至少有一条 COPY"
    for line in copies:
        parts = line.split()
        assert len(parts) >= 3, f"COPY 缺少源/目标：{line}"
        assert parts[0] == "COPY"
        assert parts[1].startswith("--") is False, f"本项目 COPY 不需要额外参数：{line}"
        sources = parts[1:-1]
        for source in sources:
            assert not source.startswith("deploy/"), f"根目录上下文不应从 deploy/ 复制：{line}"
            assert (ROOT / source.lstrip("./")).exists(), f"COPY 源不存在：{source}"


def test_dockerfile_references_the_root_context() -> None:
    dockerfile = read("Dockerfile")
    assert "构建上下文 = 仓库根目录" in dockerfile
    assert "docker build -t danmaku-pet-cloud:dev ." in dockerfile
    assert "deploy/Dockerfile" not in dockerfile


def test_cloud_image_runs_a_single_worker() -> None:
    dockerfile = read("Dockerfile")
    assert "workers" not in dockerfile
    assert "--port 8000" in read("run.sh")
    compose = read("deploy/docker-compose.yml")
    assert "APP_ENV: dev" in compose
    assert "8000:8000" in compose


def test_compose_builds_from_the_root_dockerfile() -> None:
    compose = read("deploy/docker-compose.yml")
    assert "context: .." in compose
    assert re.search(r"^\s*dockerfile:\s*Dockerfile\s*$", compose, re.MULTILINE), compose


def test_dockerignore_keeps_secrets_caches_logs_and_artifacts_out() -> None:
    ignore = read(".dockerignore")
    for pattern in (
        ".env",
        ".env.*",
        ".env.cloud",
        "deploy/cloud.env",
        ".conda",
        ".cache",
        "__pycache__",
        "*.log",
        "temp",
        "vendor",
        "tests",
        "docs",
        "build",
        "dist",
        "*.zip",
        "node_modules",
        "data/persona.json",
    ):
        assert pattern in ignore, f".dockerignore 缺少 {pattern}"


def test_dockerignore_keeps_the_files_the_image_needs() -> None:
    ignore = read(".dockerignore")
    patterns = [
        line.strip()
        for line in ignore.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    # The context must keep the package tree and the persona seed, and must
    # exclude only the runtime persona file.
    assert "requirements-cloud.txt" not in patterns
    assert "danmaku_pet/" not in patterns
    assert "data/persona.example.json" not in patterns
    assert "data/persona.json" in patterns


def test_docker_build_context_dry_run_passes() -> None:
    spec = importlib.util.spec_from_file_location(
        "verify_dockerfile", ROOT / "scripts" / "verify_dockerfile.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.verify()
    assert report["ok"], "\n".join(report["errors"])
    assert report["dockerfile"]["cmd"] == ["/opt/application/run.sh"]
    assert all(report["ignored_checks"].values()), report["ignored_checks"]
    assert report["context"]["files"] > 10
    assert "requirements-cloud.txt" in report["context"]["top_level"]
    assert "data" in report["context"]["top_level"]
    json.dumps(report)  # must be serializable


def test_no_stale_deploy_dockerfile_references() -> None:
    files = [ROOT / "README.md", ROOT / "deploy" / "docker-compose.yml"]
    files.extend(sorted((ROOT / "docs").glob("*.md")))
    for path in files:
        assert "deploy/Dockerfile" not in path.read_text(encoding="utf-8-sig"), path.name


def test_cloud_env_example_has_no_secret_values() -> None:
    example = read("deploy/cloud.env.example")
    assert "ADMIN_TOKEN=\n" in example
    assert "CLIENT_TOKEN=\n" in example
    assert "DEEPSEEK_API_KEY=\n" in example
    assert "sk-" not in example
    for key in (
        "APP_ENV",
        "GATEWAY_MODE",
        "DOUYIN_OPENAPI_BASE_URL",
        "DOUYIN_VERIFY_MODE",
        "ENABLE_TEST_ENDPOINTS",
        "COMMENT_EXPIRES_SECONDS",
    ):
        assert key in example


def test_client_env_example_has_no_model_key() -> None:
    example = read(".env.example")
    assert "DEEPSEEK" not in example
    assert "API_KEY" not in example
    assert "CLOUD_URL" in example
    assert "CLIENT_TOKEN" in example


def test_requirements_are_split_between_cloud_and_client() -> None:
    cloud = read("requirements-cloud.txt")
    client = read("requirements-client.txt")
    assert "PySide6" not in cloud
    assert "pygame" not in cloud
    assert "edge-tts" not in cloud
    assert "PySide6" in client
    assert "pygame" in client
    assert "edge-tts" in client
    assert "redis" in cloud
    assert "redis>=5" in read("requirements.txt")
    assert "-r requirements-client.txt" in read("requirements.txt")


def test_cloud_package_never_imports_client_only_libraries() -> None:
    cloud_dir = ROOT / "danmaku_pet" / "cloud"
    for path in cloud_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for banned in ("PySide6", "pygame", "edge_tts", "danmaku_pet.client"):
            assert banned not in text, f"{path.name} 不应依赖 {banned}"


def test_client_package_never_imports_the_cloud_or_deepseek() -> None:
    client_dir = ROOT / "danmaku_pet" / "client"
    for path in client_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "danmaku_pet.cloud" not in text, path.name
        assert "danmaku_pet.decision.llm" not in text, path.name
        assert "DEEPSEEK_API_KEY" not in text, path.name


def test_cloud_server_never_writes_secrets_to_env_files() -> None:
    server = (ROOT / "danmaku_pet" / "cloud" / "server.py").read_text(encoding="utf-8")
    assert "update_env_file" not in server
    assert "settings_store" not in server
    assert "/api/settings/llm" not in server


def test_packaging_targets_the_client_only() -> None:
    launcher = read("packaging/launcher.py")
    assert "danmaku_pet.client.__main__" in launcher
    spec = read("packaging/DanmakuPet.spec")
    assert "web/client" in spec
    assert "danmaku_pet/decision" not in spec
    assert "danmaku_pet.cloud" not in spec
