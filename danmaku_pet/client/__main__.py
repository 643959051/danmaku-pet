"""``python -m danmaku_pet.client`` (and the frozen ``DanmakuPet.exe``).

Only the 开播电脑 runs this: local page + desktop pet + cloud downlink.  There
is no DeepSeek client and no key field anywhere in this process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import runpy
import sys

from danmaku_pet import browser
from danmaku_pet.client.startup import browser_url, build_config, environment_report
from danmaku_pet.config import Config
from danmaku_pet.logging_setup import setup_logging


def _run_dafeiyu_helper(argv: list[str]) -> int:
    """Run the vendored dafeiyu helper inside this interpreter.

    The desktop pet lives in ``vendor/dafeiyu/package/runtime`` and is normally
    started as a separate Python script.  A frozen build has no such script
    interpreter, so the exe re-enters itself with ``--run-dafeiyu-helper``.
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


def _report_open(result: browser.OpenResult, url: str) -> None:
    if result.opened:
        print(f"  已在浏览器打开状态页：{url}  （{result.how}）\n", flush=True)
        return
    if result.launched_only:
        print("  浏览器已启动，但状态页请求还没有到达。", flush=True)
        print("  页面可能仍在加载；如果没看到页面，请手动访问上面的地址。\n", flush=True)
        return
    print("  未能自动打开浏览器；请手动访问上面的地址。\n", flush=True)
    print(browser.summarize(result), flush=True)
    report = result.report
    if report is not None and report.low_integrity:
        print(
            "  当前进程运行在「低完整性」：Windows 会拒绝它写 Chrome 的配置目录，"
            "Chrome 启动后立即退出。请从开始菜单新开一个 PowerShell 再运行。\n",
            flush=True,
        )
        return
    if report is not None and report.restricted_token:
        print("  当前进程令牌受限，无法启动图形程序。\n", flush=True)
        return
    print(
        "  运行 `DanmakuPet.exe --check-browser`（或 python -m danmaku_pet --check-browser）"
        "会做实际启动测试。\n",
        flush=True,
    )


async def _serve(config: Config) -> None:
    import uvicorn

    from danmaku_pet.client.status_server import ClientRuntime, create_app

    runtime = ClientRuntime(config)
    app = create_app(config, runtime)
    url = browser_url(config)
    print(f"\n  弹幕宠物本地客户端  ->  {url}", flush=True)
    print(
        f"  房间={config.room_id or '(未绑定)'}  云端={config.cloud_url}  传输={config.transport}",
        flush=True,
    )

    uv_config = uvicorn.Config(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
        access_log=False,
    )
    uvicorn_server = uvicorn.Server(uv_config)
    serve_task = asyncio.create_task(uvicorn_server.serve())

    if config.auto_open_browser:
        for _ in range(200):
            if uvicorn_server.started or serve_task.done():
                break
            await asyncio.sleep(0.05)
        if uvicorn_server.started:
            try:
                result = await browser.open_management_page(
                    url, page_views=lambda: runtime.page_views
                )
                _report_open(result, url)
            except Exception as exc:  # noqa: BLE001 - never block the client
                print(f"  自动打开浏览器失败：{exc}\n", flush=True)

    await serve_task


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["--run-dafeiyu-helper"]:
        return _run_dafeiyu_helper(arguments[1:])

    config, args = build_config(arguments)
    setup_logging(
        config.logs_dir,
        level=getattr(logging, config.log_level, logging.INFO),
        secrets=(config.client_token,),
    )
    if args.check_browser:
        print(browser.diagnose(browser_url(config)))
        return 0
    if args.check or args.print_config:
        print(json.dumps(environment_report(config), ensure_ascii=False, indent=2))
        return 0

    problems = environment_report(config)["validation"]
    for problem in problems:
        print(f"  [提示] {problem}", flush=True)

    try:
        asyncio.run(_serve(config))
    except KeyboardInterrupt:
        return 0
    return 0


# Backwards-compatible name used by older tests/scripts.
_browser_url = browser_url

if __name__ == "__main__":
    raise SystemExit(main())
