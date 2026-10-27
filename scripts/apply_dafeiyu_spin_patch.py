"""Apply the small local 'spin' effect extension to dafeiyu's helper.

This is a deliberately narrow patch: it adds one inbound message kind
(``effect`` with name ``spin``) and a yaw-style horizontal flip in paintEvent.
It does not alter the upstream state protocol or animation assets.

Usage:
    .\.conda\env\python.exe scripts\apply_dafeiyu_spin_patch.py
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HELPER = ROOT / "vendor" / "dafeiyu" / "package" / "runtime" / "helper.py"


def patch(text: str) -> tuple[str, bool]:
    if "self.spin_started = 0.0" in text:
        return text, False

    def replace_once(source: str, old: str, new: str, label: str) -> str:
        if old not in source:
            raise RuntimeError(f"patch anchor missing: {label}")
        return source.replace(old, new, 1)

    text = replace_once(text, "import json\nimport os\n", "import json\nimport math\nimport os\n", "math import")
    text = replace_once(
        text,
        "            self.fade_duration = 0.15\n",
        "            self.fade_duration = 0.15\n"
        "            self.spin_started = 0.0\n"
        "            self.spin_duration = 0.0\n",
        "effect fields",
    )
    text = replace_once(
        text,
        "            previous_frame = self.model.frame\n",
        '            if kind == "effect":\n'
        '                if str(message.get("name", "")) == "spin":\n'
        "                    self.spin_started = time.monotonic()\n"
        "                    try:\n"
        '                        duration_ms = float(message.get("durationMs", 1600))\n'
        "                    except (TypeError, ValueError):\n"
        "                        duration_ms = 1600.0\n"
        "                    self.spin_duration = min(5.0, max(0.4, duration_ms / 1000.0))\n"
        "                    self.update()\n"
        "                return\n"
        "            previous_frame = self.model.frame\n",
        "apply_message effect branch",
    )
    text = replace_once(
        text,
        "            now_ms = self._now_ms()\n            elapsed_ms = max(0, now_ms - self.last_tick_ms)\n",
        "            now_ms = self._now_ms()\n"
        "            if self.spin_duration and time.monotonic() - self.spin_started >= self.spin_duration:\n"
        "                self.spin_duration = 0.0\n"
        "            elapsed_ms = max(0, now_ms - self.last_tick_ms)\n",
        "_tick effect expiry",
    )
    text = replace_once(
        text,
        "                painter.setOpacity(alpha)\n                painter.drawPixmap(",
        "                painter.setOpacity(alpha)\n"
        "                spin_scale = 1.0\n"
        "                if self.spin_duration:\n"
        "                    spin_angle = (\n"
        "                        min(1.0, (time.monotonic() - self.spin_started) / self.spin_duration)\n"
        "                        * math.pi\n"
        "                        * 2\n"
        "                    )\n"
        "                    spin_scale = math.cos(spin_angle)\n"
        "                    if abs(spin_scale) < 0.05:\n"
        "                        spin_scale = 0.05 if spin_scale >= 0 else -0.05\n"
        "                if spin_scale != 1.0:\n"
        "                    spin_center_x = x + pw / 2\n"
        "                    painter.translate(spin_center_x, 0)\n"
        "                    painter.scale(spin_scale, 1.0)\n"
        "                    painter.translate(-spin_center_x, 0)\n"
        "                painter.drawPixmap(",
        "paintEvent spin transform",
    )
    return text, True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_HELPER)
    args = parser.parse_args()
    helper = args.path
    if not helper.is_file():
        raise SystemExit(f"helper not found: {helper}")
    original = helper.read_text(encoding="utf-8")
    patched, changed = patch(original)
    if not changed:
        print(f"already patched: {helper}")
        return 0
    backup = helper.with_suffix(".py.spin-patch.bak")
    if not backup.exists():
        shutil.copy2(helper, backup)
    helper.write_text(patched, encoding="utf-8")
    print(f"patched native spin effect: {helper}")
    print(f"backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
