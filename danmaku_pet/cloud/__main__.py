"""``python -m danmaku_pet.cloud`` — 抖音云 Docker container entry point.

One uvicorn worker per instance; room state lives in Redis (production), so
multiple stateless instances can share the same rooms.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from danmaku_pet.cloud.config import CloudConfig, ConfigError
from danmaku_pet.cloud.server import create_app
from danmaku_pet.shared.protocol import PROTOCOL_VERSION


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m danmaku_pet.cloud",
        description="弹幕宠物云端后端（抖音云 Docker）",
    )
    parser.add_argument("--host", default=None, help="监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=None, help="监听端口，默认 8000")
    parser.add_argument("--log-level", default=None, help="日志级别，默认 INFO")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只输出脱敏后的配置摘要，不启动服务",
    )
    parser.add_argument(
        "--access-log",
        action="store_true",
        help="开启 uvicorn 访问日志（默认关闭：URL 可能带令牌）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = CloudConfig.load()
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr, flush=True)
        return 2

    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.log_level:
        config.log_level = args.log_level.upper()

    if args.check:
        print(
            json.dumps(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "host": config.host,
                    "port": config.port,
                    "config": config.public_status(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
        return 0

    app = create_app(config)
    base = f"http://{config.host}:{config.port}"
    print(f"\n  弹幕宠物云端后端  ->  {base}/", flush=True)
    print(f"  环境={config.environment}  网关={config.gateway_mode}  LLM={config.effective_llm_mode}  状态={config.state_backend}  内容安全={'on' if config.douyin.content_security_enabled else 'off'}", flush=True)
    if config.environment == "prod" and config.gateway_mode == "mock":
        print("  [警告] 正式环境使用 mock 网关：指令不会到达任何开播电脑。", flush=True)
    if config.environment == "prod" and config.effective_llm_mode == "mock":
        print("  [警告] 正式环境没有可用的 DeepSeek Key：回复将使用本地模拟。", flush=True)
    if config.state_backend == "memory" and config.environment == "prod":
        print("  [警告] 正式环境使用内存状态：实例重启/扩缩容会丢房间状态。", flush=True)
    print(f"  健康检查 {base}/v1/ping（详细状态 {base}/api/health）   客户端通道 ws://.../ws/client", flush=True)
    if config.admin_token_generated:
        print("\n  【开发模式】本次启动生成了临时管理令牌：", flush=True)
        print(f"  ADMIN_TOKEN={config.admin_token}", flush=True)
    if config.client_token_generated:
        print("\n  【开发模式】本次启动生成了临时客户端令牌：", flush=True)
        print(f"  CLIENT_TOKEN={config.client_token}", flush=True)
    if config.admin_token_generated or config.client_token_generated:
        print("  正式环境请在抖音云密钥管理中配置固定令牌，重启后不再打印。\n", flush=True)
    print(
        f"  每实例单 Worker；房间状态在 {config.state_backend}；"
        "prod 需 Redis 且可多实例共享。\n",
        flush=True,
    )

    import uvicorn

    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level=(config.log_level or "INFO").lower(),
        access_log=bool(args.access_log),
        workers=1,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
