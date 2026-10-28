"""Compatibility entry point.

    python -m danmaku_pet cloud  ...   抖音云后端（等价 python -m danmaku_pet.cloud）
    python -m danmaku_pet client ...   开播电脑客户端（等价 python -m danmaku_pet.client）
    python -m danmaku_pet --no-desktop 旧写法，等价 client 的对应参数

``--run-dafeiyu-helper`` is kept here because the frozen client re-enters its own
executable with that flag.
"""

from __future__ import annotations

import sys

from danmaku_pet.client.startup import browser_url as _browser_url

USAGE = """弹幕宠物

  python -m danmaku_pet cloud [...]     云端后端（抖音云 Docker）
  python -m danmaku_pet client [...]    开播电脑客户端
  python -m danmaku_pet --no-desktop    旧写法：等同 client

两端的详细参数：
  python -m danmaku_pet.cloud --help
  python -m danmaku_pet.client --help
"""


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)

    if arguments[:1] == ["--run-dafeiyu-helper"]:
        from danmaku_pet.client.__main__ import main as client_main

        return client_main(arguments)

    if arguments[:1] == ["cloud"]:
        from danmaku_pet.cloud.__main__ import main as cloud_main

        return cloud_main(arguments[1:])

    if arguments[:1] == ["client"]:
        from danmaku_pet.client.__main__ import main as client_main

        return client_main(arguments[1:])

    if not arguments or arguments[0] in {"-h", "--help", "help"}:
        print(USAGE, flush=True)
        return 0

    # Backward compatibility: `python -m danmaku_pet --mock` used to start the
    # all-in-one local app; it now starts the local client (the cloud decision
    # runs there instead).  Deprecated flags are reported by the client parser.
    from danmaku_pet.client.__main__ import main as client_main

    return client_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
