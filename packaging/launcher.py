"""Entry point for the frozen (PyInstaller) build: the Windows client only."""

from __future__ import annotations

import sys

from danmaku_pet.client.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
