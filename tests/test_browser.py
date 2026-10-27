from __future__ import annotations

import os
from pathlib import Path

import pytest

from danmaku_pet import browser


def test_browser_candidates_prefers_the_default_prog_id(monkeypatch: pytest.MonkeyPatch) -> None:
    roots = [Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")]
    monkeypatch.setattr(browser, "_install_roots", lambda: roots)
    monkeypatch.setattr(browser, "_read_prog_id", lambda: "MSEdgeHTM")

    names = [path.name.lower() for path in browser.browser_candidates()]
    assert names[0] == "msedge.exe"
    assert "chrome.exe" in names
    assert names.count("msedge.exe") == len(roots)


def test_browser_candidates_follow_a_chrome_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser, "_install_roots", lambda: [Path(r"C:\Program Files")])
    monkeypatch.setattr(browser, "_read_prog_id", lambda: "ChromeHTML")
    names = [path.name.lower() for path in browser.browser_candidates()]
    assert names[0] == "chrome.exe"


def test_browser_executable_is_none_without_installed_browsers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(browser, "_install_roots", lambda: [])
    monkeypatch.setattr(browser, "_read_prog_id", lambda: "")
    assert browser.browser_executable() is None


def test_environment_report_has_actionable_fields() -> None:
    report = browser.environment_report()
    assert isinstance(report.prog_id, str)
    lines = report.lines()
    assert len(lines) >= 4
    assert any("默认浏览器" in line for line in lines)
    assert any("会话/桌面" in line for line in lines)
    assert any("完整性级别" in line for line in lines)


def test_verdict_explains_low_integrity() -> None:
    report = browser.BrowserReport(
        integrity="低", chrome_write_error="拒绝访问（0x5）"
    )
    results = [
        browser.ProbeResult("记事本", "notepad.exe", started=True, still_running=True),
        browser.ProbeResult(
            "chrome.exe", "chrome.exe", started=True, still_running=False, exit_code=0x80000003
        ),
    ]
    text = browser._verdict(report, results)
    assert "低完整性" in text
    assert "0x5" in text
    assert "0x80000003" in text or "管理员身份运行" in text


def test_verdict_reports_blocked_graphics() -> None:
    results = [
        browser.ProbeResult(
            "记事本", "notepad.exe", started=False, still_running=False, note="拒绝访问"
        )
    ]
    assert "禁止创建图形程序" in browser._verdict(browser.BrowserReport(), results)


def test_verdict_reports_a_browser_that_exits_immediately() -> None:
    results = [
        browser.ProbeResult("记事本", "notepad.exe", started=True, still_running=True),
        browser.ProbeResult(
            "chrome.exe",
            "chrome.exe --new-window url",
            started=True,
            still_running=False,
            exit_code=0xFFFF7001,
        ),
    ]
    text = browser._verdict(browser.BrowserReport(), results)
    assert "立即退出" in text
    assert "0xFFFF7001" in text


def test_verdict_accepts_a_browser_that_keeps_running() -> None:
    results = [
        browser.ProbeResult("记事本", "notepad.exe", started=True, still_running=True),
        browser.ProbeResult("chrome.exe", "chrome.exe", started=True, still_running=True),
    ]
    assert "浏览器本身可以正常启动" in browser._verdict(browser.BrowserReport(), results)


def test_browser_env_restores_system_temp(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = str(Path(os.environ["LOCALAPPDATA"]) / "Temp")
    monkeypatch.setenv("TEMP", r"D:\project\temp\process-temp")
    monkeypatch.setenv("TMP", r"D:\project\temp\process-temp")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    env = browser._browser_env()
    assert env["TEMP"] == expected
    assert env["TMP"] == expected
    assert "PYTHONIOENCODING" not in env


def test_summarize_explains_every_attempt() -> None:
    result = browser.OpenResult(
        opened=False,
        attempts=[
            browser.Attempt(label="系统默认浏览器", launched=True, outcome="none"),
            browser.Attempt(
                label="PowerShell Start-Process",
                launched=False,
                error="OSError: powershell.exe not found",
            ),
            browser.Attempt(
                label="浏览器可执行文件", launched=True, outcome="process", detail="Google Chrome"
            ),
        ],
        report=browser.BrowserReport(prog_id="ChromeHTML"),
    )
    text = browser.summarize(result)
    assert "系统默认浏览器: 已调用，未检测到浏览器" in text
    assert "powershell.exe not found" in text
    assert "浏览器已启动" in text
    assert "ChromeHTML" in text


async def test_open_management_page_reports_success_when_the_page_is_served(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    views = {"count": 0}

    def fake_launch(url: str) -> browser.Launch:
        views["count"] += 1
        return browser.Launch("fake")

    monkeypatch.setattr(browser, "browser_processes", lambda: {})
    monkeypatch.setattr(browser, "strategies", lambda: [("fake", fake_launch)])
    result = await browser.open_management_page(
        "http://127.0.0.1:8787/", page_views=lambda: views["count"], per_attempt=1.0
    )
    assert result.opened is True
    assert result.how == "fake"
    assert result.attempts[0].outcome == "page"


async def test_open_management_page_falls_through_failed_launchers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(url: str) -> browser.Launch:
        raise OSError("cannot launch")

    monkeypatch.setattr(browser, "browser_processes", lambda: {})
    monkeypatch.setattr(browser, "strategies", lambda: [("boom", boom)])
    monkeypatch.setattr(browser, "environment_report", lambda: browser.BrowserReport())
    result = await browser.open_management_page(
        "http://127.0.0.1:8787/", page_views=lambda: 0, per_attempt=0.3, total_timeout=2
    )
    assert result.opened is False
    assert result.report is not None
    assert result.attempts[0].error is not None


async def test_open_management_page_stops_at_a_started_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = {"value": False}

    def fake_processes() -> dict[int, str]:
        return {4321: "Google Chrome"} if started["value"] else {}

    def fake_launch(url: str) -> browser.Launch:
        started["value"] = True
        return browser.Launch("ShellExecute")

    monkeypatch.setattr(browser, "browser_processes", fake_processes)
    monkeypatch.setattr(
        browser,
        "strategies",
        lambda: [
            ("shell", fake_launch),
            ("second", lambda url: pytest.fail("must not launch a second window")),
        ],
    )
    result = await browser.open_management_page(
        "http://127.0.0.1:8787/", page_views=lambda: 0, per_attempt=1.0
    )
    assert result.opened is False
    assert result.launched_only is True
    assert len(result.attempts) == 1
