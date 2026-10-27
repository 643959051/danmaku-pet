"""Run pip with workspace-friendly temp directories.

The DSH Windows sandbox creates directories made with mode 0700 without a
usable DACL in this workspace. Python's tempfile module uses 0700 by default,
so pip's unpack directories become unreadable. This launcher only changes the
process-local tempfile implementation to create 0777 directories, then runs
pip unchanged. It never edits Python's standard library or system policy.
"""

from __future__ import annotations

import os
import runpy
import secrets
import sys
import tempfile
from pathlib import Path


def _mkdtemp(suffix: str = "", prefix: str = "tmp", dir: str | None = None) -> str:
    base = Path(dir or tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        candidate = base / f"{prefix}{secrets.token_hex(4)}{suffix}"
        try:
            os.mkdir(candidate, 0o777)
        except FileExistsError:
            continue
        return str(candidate)
    raise FileExistsError("could not create a unique temporary directory")


tempfile.mkdtemp = _mkdtemp  # type: ignore[assignment]
sys.argv[0] = "pip"
runpy.run_module("pip", run_name="__main__", alter_sys=True)
