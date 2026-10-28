"""Dry-run validation for the root ``Dockerfile`` and ``.dockerignore``.

Docker is not available in every dev/CI environment.  This script performs the
checks a real build would fail on early:

* the Dockerfile is at the repository root and every ``COPY`` source exists
  relative to the root build context;
* no ``COPY`` source is excluded by ``.dockerignore``;
* the runtime contract (base image, ``EXPOSE 8000``, ``CMD``, ``USER``,
  ``HEALTHCHECK``, ``APP_ENV/CLOUD_*`` env) is intact;
* the build context does not contain secrets (``.env``, ``.env.cloud``,
  ``deploy/cloud.env``), caches, logs, tests, docs, build artifacts or the
  vendored desktop pet;
* the three files the image actually needs are present in the context.

Usage:
    .\\.conda\\env\\python.exe scripts\\verify_dockerfile.py
    .\\.conda\\env\\python.exe scripts\\verify_dockerfile.py --json

Exit code 0 = all checks passed; 1 = at least one error.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"

REQUIRED_CONTEXT = (
    "requirements-cloud.txt",
    "run.sh",
    "danmaku_pet/__main__.py",
    "danmaku_pet/cloud/__main__.py",
    "danmaku_pet/cloud/server.py",
    "danmaku_pet/cloud/config.py",
    "danmaku_pet/decision/llm.py",
    "danmaku_pet/web/cloud/index.html",
    "data/persona.example.json",
)
FORBIDDEN_CONTEXT = (
    ".env",
    ".env.cloud",
    ".env.local",
    "deploy/cloud.env",
    "temp/anything.txt",
    "dist/DanmakuPet/DanmakuPet.exe",
    "build/tmp.txt",
    ".conda/env/python.exe",
    ".cache/pip/x.whl",
    "tests/test_deploy.py",
    "scripts/run.ps1",
    "docs/README.md",
    "vendor/dafeiyu/package/runtime/helper.py",
    "assets/tts_cache/cached.mp3",
    "data/persona.json",
    "danmaku_pet/__pycache__/server.cpython-311.pyc",
    "runtime.log",
    "release.zip",
    "node_modules/pkg/index.js",
)
EXPECTED_CMD = ["/opt/application/run.sh"]

KNOWN_INSTRUCTIONS = {
    "ADD",
    "ARG",
    "CMD",
    "COPY",
    "ENTRYPOINT",
    "ENV",
    "EXPOSE",
    "FROM",
    "HEALTHCHECK",
    "LABEL",
    "MAINTAINER",
    "ONBUILD",
    "RUN",
    "SHELL",
    "STOPSIGNAL",
    "USER",
    "VOLUME",
    "WORKDIR",
}


class IgnoreMatcher:
    """A pragmatic subset of Docker's last-match-wins .dockerignore semantics."""

    def __init__(self, lines: list[str]) -> None:
        self.patterns: list[tuple[bool, str]] = []
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            pattern = line[1:].strip() if negated else line
            pattern = pattern.lstrip("./")
            if pattern:
                self.patterns.append((negated, pattern))

    def ignored(self, rel_path: str) -> bool:
        rel = rel_path.replace("\\", "/").lstrip("./")
        if not rel:
            return False
        # Docker always sends the Dockerfile and .dockerignore themselves.
        if rel in {"Dockerfile", ".dockerignore"}:
            return False
        result = False
        for negated, pattern in self.patterns:
            if _matches(pattern, rel):
                result = not negated
        return result


def _matches(pattern: str, rel: str) -> bool:
    p = pattern.rstrip("/")
    if not p:
        return False
    if p in {".", "*", "**"}:
        return True
    parts = rel.split("/")
    if p.startswith("**/"):
        tail = p[3:]
        return any(fnmatch.fnmatchcase(part, tail) for part in parts) or fnmatch.fnmatchcase(
            rel, tail
        )
    if "/" in p:
        return fnmatch.fnmatchcase(rel, p) or rel.startswith(p + "/")
    return any(fnmatch.fnmatchcase(part, p) for part in parts)


def parse_instructions(text: str) -> list[str]:
    instructions: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = f"{buffer} {line}".strip() if buffer else line
        if line.endswith("\\"):
            buffer = line[:-1].rstrip()
            continue
        buffer = ""
        instructions.append(line)
    if buffer:
        instructions.append(buffer)
    return instructions


def _copy_sources(instruction: str) -> list[str]:
    body = instruction[len("COPY") :].strip()
    parts = body.split()
    while parts and parts[0].startswith("--"):
        parts.pop(0)
    if len(parts) < 2:
        return []
    return parts[:-1]


def _context_files(matcher: IgnoreMatcher) -> dict[str, int]:
    """Walk the root context, pruning ignored directories before descending."""

    files: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = Path(dirpath).relative_to(ROOT).as_posix()
        if rel_dir == ".":
            rel_dir = ""
        dirnames[:] = [
            name
            for name in dirnames
            if not matcher.ignored(f"{rel_dir}/{name}" if rel_dir else name)
        ]
        for name in filenames:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if matcher.ignored(rel):
                continue
            try:
                files[rel] = (ROOT / rel).stat().st_size
            except OSError:
                files[rel] = 0
    return files


def verify() -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    if not DOCKERFILE.is_file():
        errors.append("仓库根目录缺少 Dockerfile")
        return {"ok": False, "errors": errors, "warnings": warnings}
    if (ROOT / "deploy" / "Dockerfile").exists():
        errors.append("deploy/Dockerfile 仍然存在；Dockerfile 应只保留在仓库根目录")
    if not DOCKERIGNORE.is_file():
        errors.append("仓库根目录缺少 .dockerignore")

    text = DOCKERFILE.read_text(encoding="utf-8")
    instructions = parse_instructions(text)
    matcher = IgnoreMatcher(
        DOCKERIGNORE.read_text(encoding="utf-8").splitlines() if DOCKERIGNORE.is_file() else []
    )
    context = _context_files(matcher)

    # ---------------------------------------------------------- instructions
    seen: set[str] = set()
    copies: list[dict[str, Any]] = []
    cmd: list[str] | None = None
    for instruction in instructions:
        keyword = instruction.split(maxsplit=1)[0].upper()
        seen.add(keyword)
        if keyword not in KNOWN_INSTRUCTIONS:
            errors.append(f"未知 Dockerfile 指令：{instruction[:80]}")
        if keyword == "FROM" and "python:3.11-slim" not in instruction:
            errors.append(f"基础镜像不是 python:3.11-slim：{instruction}")
        if keyword == "COPY":
            sources = _copy_sources(instruction)
            info: dict[str, Any] = {"sources": sources, "ok": True}
            if not sources:
                errors.append(f"COPY 指令没有源路径：{instruction}")
                info["ok"] = False
            for source in sources:
                clean = source.lstrip("./")
                target = ROOT / clean
                info["sources"] = sources
                if not target.exists():
                    errors.append(f"COPY 源不存在（构建上下文 = 仓库根目录）：{source}")
                    info["ok"] = False
                elif target.is_dir():
                    prefix = clean.rstrip("/") + "/"
                    included = [rel for rel in context if rel.startswith(prefix)]
                    if not included:
                        errors.append(f"COPY 源目录在构建上下文中为空：{source}")
                        info["ok"] = False
                elif matcher.ignored(clean):
                    errors.append(f"COPY 源被 .dockerignore 排除：{source}")
                    info["ok"] = False
            copies.append(info)
        if keyword == "CMD":
            body = instruction[len("CMD") :].strip()
            if body.startswith("["):
                try:
                    cmd = json.loads(body)
                except json.JSONDecodeError:
                    errors.append(f"CMD JSON 解析失败：{body}")
            else:
                warnings.append("CMD 使用 shell 形式；本项目约定 exec 形式")
        if keyword == "EXPOSE" and "8000" not in instruction:
            errors.append("EXPOSE 不是 8000")
        if keyword == "USER" and instruction.split(maxsplit=1)[-1].strip() in {"root", "0"}:
            errors.append("容器不允许以 root 运行")
        if keyword == "HEALTHCHECK" and "/v1/ping" not in instruction:
            errors.append("HEALTHCHECK 未检查 /v1/ping")

    for required in ("FROM", "WORKDIR", "COPY", "RUN", "USER", "EXPOSE", "HEALTHCHECK", "CMD"):
        if required not in seen:
            errors.append(f"Dockerfile 缺少 {required} 指令")
    if cmd != EXPECTED_CMD:
        errors.append(f"CMD 与约定不一致：{cmd!r}")
    for expected in (
        "APP_ENV=prod",
        "CLOUD_HOST=0.0.0.0",
        "CLOUD_PORT=8000",
        "PYTHONUNBUFFERED=1",
    ):
        if expected not in text:
            errors.append(f"ENV 缺少 {expected}")
    if "COPY run.sh /opt/application/run.sh" not in text:
        errors.append("Dockerfile 未把根目录 run.sh 复制到 /opt/application/run.sh")
    if "chmod a+x /opt/application/run.sh" not in text:
        errors.append("Dockerfile 未给 /opt/application/run.sh 添加可执行权限")

    run_sh = ROOT / "run.sh"
    if not run_sh.is_file():
        errors.append("仓库根目录缺少 run.sh")
    else:
        run_text = run_sh.read_text(encoding="utf-8")
        if not run_text.startswith("#!/bin/sh"):
            errors.append("run.sh 缺少 #!/bin/sh")
        if "--port 8000" not in run_text:
            errors.append("run.sh 未固定监听 8000")
        if "danmaku_pet.cloud" not in run_text:
            errors.append("run.sh 未启动 python -m danmaku_pet.cloud")

    server_py = ROOT / "danmaku_pet" / "cloud" / "server.py"
    if server_py.is_file() and '"/v1/ping"' not in server_py.read_text(encoding="utf-8"):
        errors.append("cloud/server.py 缺少 GET /v1/ping")

    # ------------------------------------------------------------- context
    for required in REQUIRED_CONTEXT:
        if required not in context:
            if (ROOT / required).is_file():
                errors.append(f"构建上下文缺少必要文件（被 .dockerignore 排除）：{required}")
            else:
                errors.append(f"仓库中缺少必要文件：{required}")
    for forbidden in FORBIDDEN_CONTEXT:
        if forbidden in context:
            errors.append(f"构建上下文包含应排除的内容：{forbidden}")
    for secret in (".env", ".env.cloud", "deploy/cloud.env"):
        if (ROOT / secret).is_file() and secret in context:
            errors.append(f"密钥文件进入了构建上下文：{secret}")

    total_bytes = sum(context.values())
    top: dict[str, int] = {}
    for rel in context:
        head = rel.split("/")[0]
        top[head] = top.get(head, 0) + 1

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "dockerfile": {
            "path": str(DOCKERFILE),
            "instructions": len(instructions),
            "copies": copies,
            "cmd": cmd,
        },
        "context": {
            "files": len(context),
            "bytes": total_bytes,
            "mib": round(total_bytes / (1024 * 1024), 2),
            "top_level": dict(sorted(top.items(), key=lambda item: (-item[1], item[0]))[:12]),
        },
        "ignored_checks": {
            "secrets": all(
                matcher.ignored(item) for item in (".env", ".env.cloud", "deploy/cloud.env")
            ),
            "caches": all(
                matcher.ignored(item)
                for item in (
                    ".cache/pip/x.whl",
                    ".conda/env/python.exe",
                    "danmaku_pet/__pycache__/x.pyc",
                    "node_modules/pkg/index.js",
                )
            ),
            "logs": all(
                matcher.ignored(item) for item in ("temp/x.txt", "runtime.log", "logs/app.log")
            ),
            "artifacts": all(
                matcher.ignored(item)
                for item in ("dist/x.zip", "build/x", "release.zip", "out/app.exe")
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="无 Docker 守护进程时的 Dockerfile/上下文校验")
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = parser.parse_args(argv)
    report = verify()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        status = "OK" if report["ok"] else "FAILED"
        print(f"[{status}] Dockerfile / .dockerignore 校验")
        if "context" in report:
            context = report["context"]
            print(
                f"  构建上下文：{context['files']} 个文件，{context['mib']} MiB"
            )
            print(f"  顶层分布：{context['top_level']}")
        for copy in report.get("dockerfile", {}).get("copies", []):
            print(f"  COPY {copy['sources']} -> {'OK' if copy['ok'] else 'ERROR'}")
        for warning in report["warnings"]:
            print(f"  [warn] {warning}")
        for error in report["errors"]:
            print(f"  [error] {error}")
        print(
            "  提示：本脚本只做无守护进程的静态/上下文校验；真正构建请运行 "
            "`docker build -t danmaku-pet-cloud:dev .`"
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
