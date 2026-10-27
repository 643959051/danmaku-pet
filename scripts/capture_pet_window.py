"""Fast window capture for the dafeiyu helper (used by local diagnostics)."""

from __future__ import annotations

import argparse
import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

from PySide6.QtGui import QGuiApplication


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_window(pid: int) -> tuple[int, int, int, int] | None:
    found: list[tuple[int, int, int, int]] = []

    @EnumWindowsProc
    def callback(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width > 10 and height > 10:
            found.append((rect.left, rect.top, width, height))
        return True

    user32.EnumWindows(callback, 0)
    return max(found, key=lambda item: item[2] * item[3]) if found else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    app = QGuiApplication(sys.argv[:1])
    rect = find_window(args.pid)
    if rect is None:
        raise SystemExit(f"no window for pid {args.pid}")
    x, y, width, height = rect
    screen = app.primaryScreen()
    if screen is None:
        raise SystemExit("no screen")
    pixmap = screen.grabWindow(0, x, y, width, height)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if not pixmap.save(str(args.out), "PNG"):
        raise SystemExit("save failed")
    print(f"saved {width}x{height} {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
