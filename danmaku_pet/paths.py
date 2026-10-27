"""Filesystem anchors shared by the source run and the frozen exe.

A source run keeps everything in the project directory.  The PyInstaller build
is a portable folder, so:

* read-only bundled files (web page, vendored helper, default persona) live in
  the bundle, which PyInstaller exposes as ``sys._MEIPASS``;
* everything the app writes (.env, persona edits, TTS cache, logs, layouts)
  lives next to ``DanmakuPet.exe`` so the folder stays self-contained.
"""

from __future__ import annotations

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))

APP_ROOT = (
    Path(sys.executable).resolve().parent
    if FROZEN
    else Path(__file__).resolve().parents[1]
)
