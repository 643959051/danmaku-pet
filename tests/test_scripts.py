from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("script", sorted((ROOT / "scripts").glob("*.ps1")), ids=lambda p: p.name)
def test_non_ascii_powershell_scripts_are_utf8_with_bom(script: Path) -> None:
    """Windows PowerShell 5.1 decodes BOM-less files as ANSI.

    Scripts that contain Chinese text must carry the BOM, otherwise
    ``powershell -File`` misreads them and can fail to parse. Pure-ASCII
    scripts do not need it.
    """

    raw = script.read_bytes()
    if all(byte < 0x80 for byte in raw):
        return
    assert raw.startswith(b"\xef\xbb\xbf"), (
        f"{script.name} 含非 ASCII 字符但缺少 UTF-8 BOM —— "
        "PowerShell 5.1 会把中文按 ANSI 解码"
    )
    raw.decode("utf-8-sig")  # must stay decodable as UTF-8


def test_release_script_packages_one_versioned_zip() -> None:
    text = (ROOT / "scripts" / "release.ps1").read_text(encoding="utf-8-sig")
    assert "danmaku_pet.__version__" in text
    assert "attach_files" in text
    # 默认打一个带版本号的包，只有 -Split 才拆成两个
    assert 'DanmakuPet-$Version-win64.zip' in text
    assert "[switch]$Split" in text
    # Gitee 单附件上限 100 MB，脚本必须自己守住这个门槛
    assert "$LimitBytes = 100MB" in text
