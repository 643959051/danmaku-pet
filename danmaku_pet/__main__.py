"""CLI entry point: ``python -m danmaku_pet``."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import runpy
import sys

from danmaku_pet import browser
from danmaku_pet.config import Config


def _run_dafeiyu_helper(argv: list[str]) -> int:
    """Run the vendored dafeiyu helper inside this interpreter.

    The desktop pet lives in ``vendor/dafeiyu/package/runtime`` and is normally
    started as a separate Python script.  A frozen build has no such script
    interpreter, so the exe re-enters itself with ``--run-dafeiyu-helper`` and
    executes the same sources from the bundle.
    """

    from danmaku_pet.paths import RESOURCE_ROOT

    package = RESOURCE_ROOT / "vendor" / "dafeiyu" / "package"
    runtime = package / "runtime"
    helper = runtime / "helper.py"
    if not helper.is_file():
        print(f"找不到大肥鱼 runtime：{helper}", file=sys.stderr, flush=True)
        return 2

    sys.path.insert(0, str(runtime))
    try:
        import asset_paths

        # The helper resolves its frame assets from ``sys._MEIPASS`` when frozen;
        # point it at the bundled package so both modes find the same files.
        asset_paths.bundle_root = lambda: package  # type: ignore[assignment]
    except ImportError:
        pass
    sys.argv = [str(helper), *argv]
    try:
        runpy.run_path(str(helper), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 0
    return 0


def _browser_url(config: Config) -> str:
    host = config.host
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    return f"http://{host}:{config.port}/"


def _report_open(result: browser.OpenResult, url: str) -> None:
    if result.opened:
        print(f"  已在浏览器打开管理页面：{url}  （{result.how}）\n", flush=True)
        return
    if result.launched_only:
        print("  浏览器已启动，但管理页面请求还没有到达。", flush=True)
        print("  页面可能仍在加载；如果没看到页面，请手动访问上面的地址。\n", flush=True)
        return
    print("  未能自动打开浏览器；请手动访问上面的地址。\n", flush=True)
    print(browser.summarize(result), flush=True)
    report = result.report
    if report is not None and report.low_integrity:
        print(
            "  当前进程运行在「低完整性」（Low IL）：Windows 会拒绝它写 Chrome 的配置目录，"
            "Chrome 启动后立即退出（0x80000003）。",
            flush=True,
        )
        print(
            "  请从开始菜单打开一个新的 PowerShell / Windows Terminal 再运行启动脚本"
            "（不要用 DSH、沙箱或受限的终端）；若新窗口仍是「低」，就用"
            "「以管理员身份运行」打开终端。\n",
            flush=True,
        )
        return
    if report is not None and report.restricted_token:
        print(
            "  当前进程令牌受限，无法启动图形程序：请在自己的 PowerShell 窗口里运行启动脚本。",
            flush=True,
        )
    elif report is not None and report.elevated is None:
        print(
            "  读不到进程令牌：如果这是自动化/沙箱终端，浏览器不会弹出来；"
            "请在自己的 PowerShell 窗口里运行启动脚本。",
            flush=True,
        )
    elif report is not None:
        print(
            "  进程能启动，但没有浏览器窗口：常见原因是已有 Chrome 以管理员身份运行"
            "（两者被 Windows 隔开）、配置文件被占用，或被安全软件拦截。",
            flush=True,
        )
    print(
        "  运行 `python -m danmaku_pet --check-browser` 会做实际启动测试（记事本 + 浏览器），"
        "把输出发我即可定位。\n",
        flush=True,
    )


async def _serve(config: Config) -> None:
    import uvicorn

    from danmaku_pet import server

    server.CONFIG.host = config.host
    server.CONFIG.port = config.port
    server.CONFIG.auto_open_browser = config.auto_open_browser
    url = _browser_url(config)
    print(f"\n  弹幕小人 MVP  ->  {url}\n", flush=True)

    uv_config = uvicorn.Config(
        server.app,
        host=config.host,
        port=config.port,
        log_level="info",
    )
    uvicorn_server = uvicorn.Server(uv_config)
    serve_task = asyncio.create_task(uvicorn_server.serve())

    if config.auto_open_browser:
        # Wait for the socket + lifespan startup, not just process start.
        for _ in range(200):
            if uvicorn_server.started or serve_task.done():
                break
            await asyncio.sleep(0.05)
        if uvicorn_server.started:
            try:
                result = await browser.open_management_page(
                    url, page_views=server.page_views
                )
                _report_open(result, url)
            except Exception as exc:  # noqa: BLE001 - never block the server
                print(f"  自动打开浏览器失败：{exc}\n", flush=True)

    await serve_task


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["--run-dafeiyu-helper"]:
        return _run_dafeiyu_helper(arguments[1:])

    parser = argparse.ArgumentParser(description="弹幕驱动虚拟小人 MVP")
    parser.add_argument("--host", default=None, help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=None, help="监听端口，默认 8787")
    parser.add_argument("--mock", action="store_true", help="强制使用本地模拟决策")
    parser.add_argument("--no-desktop", action="store_true", help="不启动 dafeiyu 桌面小人")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--check", action="store_true", help="只检查环境并输出 JSON，不启动服务")
    parser.add_argument(
        "--check-browser",
        action="store_true",
        help="诊断浏览器启动环境（完整性级别、正在运行的浏览器、实际启动测试）",
    )
    args = parser.parse_args(arguments)

    if args.check_browser:
        config = Config.load()
        print(browser.diagnose(_browser_url(config)))
        return 0

    if args.mock:
        os.environ["LLM_MODE"] = "mock"
    if args.no_desktop:
        os.environ["DESKTOP_PET"] = "false"

    config = Config.load()
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.no_browser:
        config.auto_open_browser = False

    if args.check:
        print(
            json.dumps(
                {
                    "python": sys.executable,
                    "prefix": sys.prefix,
                    "root": str(config.root),
                    "config": config.public_status(),
                    "helper": str(config.helper_path),
                    "helper_exists": config.helper_path.is_file(),
                    "dafeiyu_assets": str(config.dafeiyu_assets),
                    "browser_url": _browser_url(config),
                    "browser": {
                        "prog_id": browser.environment_report().prog_id,
                        "executable": str(browser.browser_executable() or ""),
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    try:
        asyncio.run(_serve(config))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
