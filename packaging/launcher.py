"""Entry point for the frozen (PyInstaller) build."""

from __future__ import annotations

import sys

from danmaku_pet.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
