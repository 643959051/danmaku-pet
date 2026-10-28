"""Client startup: command line, platform launch parameters, runtime paths.

The final platform launch-parameter names are only fixed after 抖音开放平台
confirms them (改造计划 阶段 0/6), so this module:

* supports explicit flags (``--room-id`` / ``--cloud-url`` / ``--client-token``);
* accepts any ``--key=value`` pair and maps the known aliases;
* keeps every unknown pair in ``config.platform_params`` for diagnostics.

This keeps us from hardcoding a launcher contract that may change.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from danmaku_pet.config import Config

# Platform launcher key -> local config field.  Extend here once the platform
# documents its exact names; unknown keys are preserved, never dropped.
LAUNCH_KEY_MAP: dict[str, str] = {
    "room_id": "room_id",
    "roomId": "room_id",
    "room": "room_id",
    "client_id": "client_id",
    "clientId": "client_id",
    "cloud_url": "cloud_url",
    "cloudUrl": "cloud_url",
    "cloud_ws": "cloud_url",
    "ws": "cloud_url",
    "token": "client_token",
    "client_token": "client_token",
    "clientToken": "client_token",
}

TRANSPORTS = ("websocket", "static", "platform")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="DanmakuPet.exe",
        description="弹幕宠物开播电脑客户端（只执行云端指令，不含 DeepSeek Key）",
    )
    parser.add_argument("--room-id", default=None, help="绑定直播间 room_id")
    parser.add_argument("--client-id", default=None, help="客户端标识，默认取主机名")
    parser.add_argument("--cloud-url", default=None, help="云端下行通道地址")
    parser.add_argument("--client-token", default=None, help="云端下发的一次性/固定客户端令牌")
    parser.add_argument(
        "--transport",
        default=None,
        choices=TRANSPORTS,
        help="websocket=云端 WSS（开发/备用）；static=本地模拟指令；platform=抖音云网关客户端通道（待平台参数）",
    )
    parser.add_argument(
        "--static-commands",
        default=None,
        help="本地模拟云端指令的 JSONL 文件（自动切换为 static）",
    )
    parser.add_argument("--host", default=None, help="本地状态页监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=None, help="本地状态页端口，默认 8787")
    parser.add_argument("--no-desktop", action="store_true", help="不启动桌面小人")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开状态页")
    parser.add_argument("--check", action="store_true", help="输出环境 JSON 后退出")
    parser.add_argument("--check-browser", action="store_true", help="诊断浏览器启动环境")
    parser.add_argument("--print-config", action="store_true", help="输出脱敏配置后退出")
    parser.add_argument("--verbose", action="store_true", help="DEBUG 日志")
    parser.add_argument(
        "--launch",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="平台启动参数（可重复）；未知键会保留在诊断信息里",
    )
    return parser


def _collect_platform_pairs(argv: list[str], known: argparse.Namespace) -> dict[str, str]:
    """Collect ``--key=value`` pairs that argparse did not recognize."""

    parser = build_parser()
    _, extras = parser.parse_known_args(argv)
    pairs: dict[str, str] = {}
    for raw in known.launch:
        if "=" in raw:
            key, value = raw.split("=", 1)
            pairs[key.strip()] = value.strip()
    for item in extras:
        if not item.startswith("--") or "=" not in item:
            continue
        key, value = item[2:].split("=", 1)
        pairs[key.strip()] = value.strip()
    return {key: value for key, value in pairs.items() if key}


def build_config(argv: list[str] | None = None) -> tuple[Config, argparse.Namespace]:
    arguments = list(argv if argv is not None else [])
    parser = build_parser()
    # ``parse_known_args`` keeps unknown ``--key=value`` platform launch
    # parameters for the mapping below instead of aborting the client.
    args, _extras = parser.parse_known_args(arguments)
    config = Config.load()

    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.room_id:
        config.room_id = args.room_id.strip()
    if args.client_id:
        config.client_id = args.client_id.strip()[:64]
    if args.cloud_url:
        config.cloud_url = args.cloud_url.strip()
    if args.client_token:
        config.client_token = args.client_token.strip()
    if args.transport:
        config.transport = args.transport
    if args.static_commands:
        config.static_commands = Path(args.static_commands).expanduser()
        config.transport = "static"
    if args.no_desktop:
        config.desktop_pet_enabled = False
    if args.no_browser:
        config.auto_open_browser = False
    if args.verbose:
        config.log_level = "DEBUG"

    pairs = _collect_platform_pairs(arguments, args)
    config.platform_params = pairs
    for key, value in pairs.items():
        target = LAUNCH_KEY_MAP.get(key)
        if not value:
            continue
        # Launcher parameters are explicit per-launch input, so they take
        # precedence over the machine-wide .env.
        if target == "room_id":
            config.room_id = value
        elif target == "client_id":
            config.client_id = value[:64]
        elif target == "cloud_url":
            config.cloud_url = value
        elif target == "client_token":
            config.client_token = value
    return config, args


def browser_url(config: Config) -> str:
    host = config.host
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    return f"http://{host}:{config.port}/"


def validate(config: Config) -> list[str]:
    """Return human-readable problems that would stop a useful connection."""

    problems: list[str] = []
    if not config.room_id:
        problems.append("没有绑定 room_id：请用 --room-id 或 --launch room_id=... 指定")
    if config.host not in {"127.0.0.1", "localhost", "::1", "[::1]"}:
        problems.append(
            "本地状态页绑定在非回环地址：它没有登录鉴权，请不要把端口暴露到公网/局域网"
        )
    if config.transport not in TRANSPORTS:
        problems.append(f"未知 transport：{config.transport}")
    if config.transport == "websocket":
        url = config.cloud_url
        if not url.startswith(("ws://", "wss://")):
            problems.append("cloud_url 必须形如 ws://host:port/ws/client 或 wss://...")
        if not config.cloud_url.rstrip("/").endswith("/ws/client"):
            problems.append("cloud_url 应指向云端的 /ws/client")
        if not config.client_token:
            problems.append("没有配置 CLIENT_TOKEN：请用 --client-token 或环境变量提供")
    if config.transport == "static":
        if config.static_commands is None:
            problems.append("transport=static 需要 --static-commands <文件>")
        elif not config.static_commands.is_file():
            problems.append(f"模拟指令文件不存在：{config.static_commands}")
    return problems


def environment_report(config: Config) -> dict[str, Any]:
    return {
        "python": os.sys.executable,
        "prefix": os.sys.prefix,
        "root": str(config.root),
        "config": config.public_status(),
        "validation": validate(config),
        "helper": str(config.helper_path),
        "helper_exists": config.helper_path.is_file(),
        "dafeiyu_assets": str(config.dafeiyu_assets),
        "browser_url": browser_url(config),
    }
