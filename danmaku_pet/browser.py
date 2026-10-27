"""Open the management page in the user's default browser.

The launch itself is deliberately boring: the URL is handed to Windows' own
shell (``ShellExecute`` / ``os.startfile``), exactly the way a desktop shortcut
or ``Start-Process`` does it.  That is what the reference project uses, and it
keeps working when the default browser or the URL association changes.

Verification never inspects window titles.  A page is considered open when the
HTTP server itself reports a request for ``/``; that is the only signal that
means "the管理页面 really was reached", and it works for every browser, profile
and virtual desktop.  When a browser process starts but the page is never
requested, that is reported separately instead of opening yet another window.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
from collections.abc import Callable, Sequence
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

#: Executables that count as "a browser started", keyed by lower-case file name.
BROWSER_EXECUTABLES: dict[str, str] = {
    "chrome.exe": "Google Chrome",
    "msedge.exe": "Microsoft Edge",
    "firefox.exe": "Firefox",
    "brave.exe": "Brave",
    "vivaldi.exe": "Vivaldi",
    "opera.exe": "Opera",
    "360se.exe": "360 安全浏览器",
    "360chrome.exe": "360 极速浏览器",
    "qqbrowser.exe": "QQ 浏览器",
    "sogouexplorer.exe": "搜狗浏览器",
    "liebao.exe": "猎豹浏览器",
}

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_RESTRICTED = -1
TOKEN_ELEVATION = 20
TOKEN_INTEGRITY_LEVEL = 25
UOI_NAME = 2

INTEGRITY_NAMES: dict[int, str] = {
    0x0000: "不可信",
    0x1000: "低",
    0x2000: "中",
    0x3000: "高（管理员）",
    0x4000: "系统",
}

PageViews = Callable[[], int | None]


# --------------------------------------------------------------------- Win32 ABI
class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TokenMandatoryLabel(ctypes.Structure):
    _fields_ = [("Label", _SidAndAttributes)]


_api_ready = False


def _setup_api() -> None:
    """Give every Win32 call real signatures.

    Without ``restype`` ctypes treats a returned handle as a 32-bit int, which
    truncates the ``GetCurrentProcess()`` pseudo-handle (-1) and makes
    ``OpenProcessToken`` fail with an invalid handle.
    """

    global _api_ready
    if not IS_WINDOWS or _api_ready:
        return
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32
    user32 = ctypes.windll.user32
    psapi = ctypes.windll.psapi

    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcessId.restype = wintypes.DWORD
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel32.ProcessIdToSessionId.restype = wintypes.BOOL

    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.IsTokenRestricted.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    advapi32.IsTokenRestricted.restype = wintypes.BOOL
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

    user32.GetProcessWindowStation.restype = wintypes.HANDLE
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.restype = wintypes.BOOL

    psapi.EnumProcesses.restype = wintypes.BOOL

    _api_ready = True


# --------------------------------------------------------------------------- model
@dataclass(slots=True)
class Launch:
    """A launch call: what was executed and, when available, its process."""

    detail: str = ""
    proc: subprocess.Popen | None = None
    stderr_path: Path | None = None


def _stderr_sink() -> tuple[object | None, Path | None]:
    """A temp file the child may write stderr into without blocking on a pipe."""

    try:
        handle, name = tempfile.mkstemp(prefix="danmaku-open-", suffix=".log")
    except OSError:
        return None, None
    return os.fdopen(handle, "wb"), Path(name)


def _stderr_tail(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def _discard(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


@dataclass(slots=True)
class Attempt:
    """One launch method that was tried."""

    label: str
    launched: bool
    outcome: str = "none"  # page | process | none
    detail: str = ""
    error: str = ""


@dataclass(slots=True)
class BrowserProcess:
    """A running browser process and the integrity level it runs at."""

    pid: int
    name: str
    integrity: str = "未知"

    @property
    def elevated(self) -> bool:
        return self.integrity.startswith("高") or self.integrity.startswith("系统")


@dataclass(slots=True)
class BrowserReport:
    """What this machine looks like from the running process."""

    prog_id: str = ""
    executable: Path | None = None
    session_id: int | None = None
    window_station: str = ""
    desktop: str = ""
    restricted_token: bool | None = None
    elevated: bool | None = None
    integrity: str = "未知"
    powershell: Path | None = None
    chrome_write_error: str | None = None

    @property
    def low_integrity(self) -> bool:
        return self.integrity in {"低", "不可信"}

    def lines(self) -> list[str]:
        exe = str(self.executable) if self.executable else "（未找到）"
        restricted = {True: "是（受限令牌，无法启动图形程序）", False: "否", None: "未知"}
        elevated = {True: "是", False: "否", None: "未知"}
        station = self.window_station or "?"
        desktop = self.desktop or "?"
        lines = [
            f"默认浏览器: {self.prog_id or '未知'}  ->  {exe}",
            f"会话/桌面: session {self.session_id}  {station}\\{desktop}",
            f"完整性级别: {self.integrity}    管理员: {elevated[self.elevated]}",
            f"进程受限: {restricted[self.restricted_token]}    PowerShell: {self.powershell or '（未找到）'}",
        ]
        if self.low_integrity:
            lines.append(
                "！低完整性（Low IL）：Windows 的强制完整性控制会拒绝写入「中等」标记的"
                "用户目录（浏览器配置就在里面），浏览器因此启动后立即退出。"
            )
        if self.chrome_write_error is not None:
            lines.append(f"！Chrome 配置写入测试失败：{self.chrome_write_error}")
        return lines


@dataclass(slots=True)
class OpenResult:
    """Outcome of trying to open the management page."""

    opened: bool
    how: str = ""
    attempts: list[Attempt] = field(default_factory=list)
    report: BrowserReport | None = None

    @property
    def launched_only(self) -> bool:
        return not self.opened and any(a.outcome == "process" for a in self.attempts)


@dataclass(slots=True)
class ProbeResult:
    """Result of one diagnostic launch."""

    label: str
    command: str
    started: bool
    still_running: bool
    exit_code: int | None = None
    stderr: str = ""
    note: str = ""
    proc: subprocess.Popen | None = None

    def summarize(self) -> str:
        if not self.started:
            return f"  - {self.label}: 无法启动（{self.note or '未知原因'}）"
        if self.still_running:
            return f"  - {self.label}: 已启动并保持运行"
        code = self.exit_code if self.exit_code is not None else 0
        tail = self.stderr.strip().splitlines()
        detail = f"  - {self.label}: 启动后立即退出（退出码 {code}，0x{code & 0xFFFFFFFF:08X}）"
        if tail:
            detail += f"  输出: {tail[-1][:160]}"
        return detail


# ------------------------------------------------------------------ environment
def _read_prog_id() -> str:
    if not IS_WINDOWS:
        return ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
        ) as key:
            return str(winreg.QueryValueEx(key, "ProgId")[0])
    except OSError:
        return ""


def _install_roots() -> list[Path]:
    roots = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    return [Path(root) for root in roots if root]


def browser_candidates() -> list[Path]:
    """Installed browser executables, most likely default first."""

    relative = [
        ("Google", "Chrome", "Application", "chrome.exe"),
        ("Microsoft", "Edge", "Application", "msedge.exe"),
        ("BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        ("Mozilla Firefox", "firefox.exe"),
        ("360", "360se", "Application", "360se.exe"),
        ("360Chrome", "Chrome", "Application", "360chrome.exe"),
        ("Tencent", "QQBrowser", "QQBrowser.exe"),
        ("SogouExplorer", "SogouExplorer.exe"),
    ]
    candidates: list[Path] = []
    for root in _install_roots():
        for parts in relative:
            candidates.append(root.joinpath(*parts))

    prog_id = _read_prog_id().lower()
    preferred = ""
    if "chrome" in prog_id and "360" not in prog_id:
        preferred = "chrome.exe"
    elif "edge" in prog_id:
        preferred = "msedge.exe"
    elif "firefox" in prog_id:
        preferred = "firefox.exe"
    if preferred:
        candidates.sort(key=lambda path: path.name.lower() != preferred)
    return candidates


def browser_executable() -> Path | None:
    """First installed browser executable, or ``None``."""

    if not IS_WINDOWS:
        return None
    return next((path for path in browser_candidates() if path.is_file()), None)


def _powershell() -> Path | None:
    for name in ("powershell.exe", "pwsh.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _user_object_name(handle: int) -> str:
    if not handle:
        return ""
    _setup_api()
    buffer = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    ok = ctypes.windll.user32.GetUserObjectInformationW(
        handle, UOI_NAME, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)
    )
    return buffer.value if ok else ""


def _window_station_and_desktop() -> tuple[str, str]:
    if not IS_WINDOWS:
        return "", ""
    _setup_api()
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    station = _user_object_name(user32.GetProcessWindowStation())
    desktop = _user_object_name(user32.GetThreadDesktop(kernel32.GetCurrentThreadId()))
    return station, desktop


def _open_token(process: int = 0) -> int | None:
    """Open the access token of the current (0) or given process."""

    _setup_api()
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32
    token = wintypes.HANDLE()
    handle = kernel32.GetCurrentProcess() if not process else kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, process
    )
    if not handle:
        return None
    try:
        if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return None
    finally:
        if process:
            kernel32.CloseHandle(handle)
    return int(token.value or 0)


def _token_flag(information_class: int) -> bool | None:
    """Read a boolean token flag, ``None`` when the token is unreadable."""

    if not IS_WINDOWS:
        return None
    _setup_api()
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32
    token = _open_token()
    if not token:
        return None
    try:
        size = wintypes.DWORD()
        if information_class == TOKEN_ELEVATION:
            value = wintypes.DWORD()
            ok = advapi32.GetTokenInformation(
                token,
                TOKEN_ELEVATION,
                ctypes.byref(value),
                ctypes.sizeof(value),
                ctypes.byref(size),
            )
            return bool(value.value) if ok else None
        restricted = wintypes.BOOL()
        if not advapi32.IsTokenRestricted(token, ctypes.byref(restricted)):
            return None
        return bool(restricted.value)
    finally:
        kernel32.CloseHandle(token)


def _token_integrity(token: int | None = None) -> str:
    """Integrity level label (低/中/高/系统) of the current or given token."""

    if not IS_WINDOWS:
        return "未知"
    _setup_api()
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32
    owned = token is None
    handle = _open_token() if owned else token
    if not handle:
        return "未知"
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(
            handle, TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size)
        )
        if not size.value:
            return "未知"
        buffer = (ctypes.c_byte * size.value)()
        if not advapi32.GetTokenInformation(
            handle, TOKEN_INTEGRITY_LEVEL, buffer, size.value, ctypes.byref(size)
        ):
            return "未知"
        label = ctypes.cast(buffer, ctypes.POINTER(_TokenMandatoryLabel)).contents
        count = advapi32.GetSidSubAuthorityCount(label.Label.Sid)
        if not count or count.contents.value == 0:
            return "未知"
        rid = advapi32.GetSidSubAuthority(label.Label.Sid, count.contents.value - 1)
        if not rid:
            return "未知"
        value = int(rid.contents.value)
        return INTEGRITY_NAMES.get(value, f"0x{value:x}")
    finally:
        if owned:
            kernel32.CloseHandle(handle)


def chrome_write_error() -> str | None:
    """Why Chrome cannot write its own profile, or ``None`` when it can.

    Chrome aborts on startup when it cannot open ``User Data\\Crashpad``
    (``file_io_win.cc`` reports access denied and the process self-terminates).
    A Low-integrity process hits that even though the files belong to the user,
    because Windows' mandatory integrity policy blocks writes to Medium-labeled
    files.  Testing it here turns a silent browser death into a clear message.
    """

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not IS_WINDOWS or not local_app_data:
        return None
    crashpad = Path(local_app_data) / "Google" / "Chrome" / "User Data" / "Crashpad"
    if not crashpad.is_dir():
        return None
    settings = crashpad / "settings.dat"
    try:
        if settings.is_file():
            with settings.open("ab"):
                pass
        else:
            probe = crashpad / "danmaku-write-probe.tmp"
            with probe.open("wb"):
                pass
            probe.unlink(missing_ok=True)
    except PermissionError as exc:
        code = getattr(exc, "winerror", None)
        return f"拒绝访问（0x{code:X}）" if code else "拒绝访问"
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def environment_report() -> BrowserReport:
    """Collect the facts needed to explain a failed launch."""

    station, desktop = _window_station_and_desktop()
    session_id: int | None = None
    if IS_WINDOWS:
        _setup_api()
        value = wintypes.DWORD()
        if ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(value)):
            session_id = int(value.value)
    return BrowserReport(
        prog_id=_read_prog_id(),
        executable=browser_executable(),
        session_id=session_id,
        window_station=station,
        desktop=desktop,
        restricted_token=_token_flag(TOKEN_RESTRICTED),
        elevated=_token_flag(TOKEN_ELEVATION),
        integrity=_token_integrity(),
        powershell=_powershell(),
        chrome_write_error=chrome_write_error(),
    )


# --------------------------------------------------------------------- processes
def _process_name(pid: int) -> str | None:
    _setup_api()
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        path = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            return None
        return Path(path.value).name.lower()
    finally:
        kernel32.CloseHandle(handle)


def browser_process_details() -> list[BrowserProcess]:
    """Running browsers with the integrity level each one runs at."""

    if not IS_WINDOWS:
        return []
    _setup_api()
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    buffer = (wintypes.DWORD * 2048)()
    needed = wintypes.DWORD()
    if not psapi.EnumProcesses(
        buffer, ctypes.sizeof(buffer), ctypes.byref(needed)
    ):
        return []
    found: list[BrowserProcess] = []
    count = needed.value // ctypes.sizeof(wintypes.DWORD)
    for index in range(count):
        pid = int(buffer[index])
        if not pid:
            continue
        name = _process_name(pid)
        if name not in BROWSER_EXECUTABLES:
            continue
        token = _open_token(pid)
        if token:
            integrity = _token_integrity(token)
            kernel32.CloseHandle(token)
        else:
            integrity = "不可访问（更高权限）"
        found.append(BrowserProcess(pid=pid, name=BROWSER_EXECUTABLES[name], integrity=integrity))
    return found

def browser_processes() -> dict[int, str]:
    """Currently running browser processes as ``{pid: friendly name}``."""

    return {item.pid: item.name for item in browser_process_details()}


# ---------------------------------------------------------------------- launchers
def _detached_flags() -> int:
    if not IS_WINDOWS:
        return 0
    return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "CREATE_NO_WINDOW", 0
    )


def _browser_env() -> dict[str, str]:
    """The user's environment, not the one the launcher scripts patched.

    ``scripts/run.ps1`` points TEMP/TMP at the workspace so the app's own
    caches stay local; a browser started from that environment inherits it too.
    Browsers expect the normal per-user temp directory, so hand them the OS
    default and drop the interpreter variables while keeping everything else.
    """

    env = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONIOENCODING", "PYTHONUTF8", "PIP_CACHE_DIR"):
        env.pop(name, None)
    local_app_data = env.get("LOCALAPPDATA")
    if local_app_data:
        default_temp = Path(local_app_data) / "Temp"
        if default_temp.is_dir():
            env["TEMP"] = str(default_temp)
            env["TMP"] = str(default_temp)
    return env


def launch_via_shell(url: str) -> Launch:
    """Hand the URL to the OS default browser (desktop-shortcut behaviour)."""

    if IS_WINDOWS:
        os.startfile(url)  # type: ignore[attr-defined]
        return Launch("ShellExecute")
    webbrowser.open(url, new=2)
    return Launch("webbrowser")


def launch_via_powershell(url: str) -> Launch:
    powershell = _powershell()
    if powershell is None:
        raise OSError("powershell.exe not found")
    env = _browser_env()
    env["DANMAKU_OPEN_URL"] = url
    sink, sink_path = _stderr_sink()
    try:
        proc = subprocess.Popen(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Start-Process -FilePath $env:DANMAKU_OPEN_URL",
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=sink if sink is not None else subprocess.DEVNULL,
            creationflags=_detached_flags(),
        )
    finally:
        if sink is not None:
            sink.close()
    return Launch(powershell.name, proc, sink_path)


def launch_via_executable(url: str) -> Launch:
    exe = browser_executable()
    if exe is None:
        raise OSError("no browser executable found")
    return launch_with_executable(exe)(url)


def launch_with_executable(exe: Path) -> Callable[[str], Launch]:
    """A launcher bound to one concrete browser executable."""

    def _launch(url: str) -> Launch:
        sink, sink_path = _stderr_sink()
        try:
            proc = subprocess.Popen(
                _browser_args(exe, url),
                env=_browser_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=sink if sink is not None else subprocess.DEVNULL,
                creationflags=_detached_flags(),
            )
        finally:
            if sink is not None:
                sink.close()
        return Launch(exe.name, proc, sink_path)

    return _launch


def launch_via_cmd_start(url: str) -> Launch:
    comspec = os.environ.get("COMSPEC") or "cmd.exe"
    proc = subprocess.Popen(
        [comspec, "/c", "start", "", url],
        env=_browser_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_detached_flags(),
    )
    return Launch("cmd start", proc)


def _isolated_profile_dir() -> Path:
    """A browser profile owned by this app.

    Chrome refuses to start when the user profile is locked by another
    instance (different integrity level, leftover crash, security software).
    A separate profile sidesteps the singleton and still shows the page.
    """

    path = Path(tempfile.gettempdir()) / "browser-profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def launch_via_isolated_profile(url: str) -> Launch:
    exe = browser_executable()
    if exe is None:
        raise OSError("no browser executable found")
    profile = _isolated_profile_dir()
    sink, sink_path = _stderr_sink()
    try:
        proc = subprocess.Popen(
            _browser_args(exe, url, profile=profile),
            env=_browser_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=sink if sink is not None else subprocess.DEVNULL,
            creationflags=_detached_flags(),
        )
    finally:
        if sink is not None:
            sink.close()
    return Launch(f"{exe.name}（独立配置）", proc, sink_path)


def _browser_args(exe: Path, url: str, *, profile: Path | None = None) -> list[str]:
    name = exe.name.lower()
    args = [str(exe)]
    if profile is not None:
        args += [f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check"]
    if name == "firefox.exe":
        args += ["-new-window", url]
    elif name in {"chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe"}:
        args += ["--new-window", url]
    else:
        args.append(url)
    return args


def installed_browsers(limit: int = 3) -> list[Path]:
    """Distinct installed browser executables, default browser first."""

    seen: set[str] = set()
    found: list[Path] = []
    for path in browser_candidates():
        if not path.is_file():
            continue
        key = path.name.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(path)
        if len(found) >= limit:
            break
    return found


def strategies() -> list[tuple[str, Callable[[str], Launch]]]:
    """Every launch method worth trying, cheapest and most native first.

    The default browser is tried first (desktop-shortcut behaviour).  If it
    starts and immediately dies — a broken crashpad handler or a locked profile
    does that and reports nothing — the other installed browsers are tried by
    their own executable before the generic shell commands.
    """

    if not IS_WINDOWS:
        return [("系统默认浏览器", launch_via_shell)]

    entries: list[tuple[str, Callable[[str], Launch]]] = [
        ("系统默认浏览器", launch_via_shell),
        ("PowerShell Start-Process", launch_via_powershell),
    ]
    for index, exe in enumerate(installed_browsers()):
        label = "浏览器可执行文件" if index == 0 else f"备选浏览器（{exe.name}）"
        entries.append((label, launch_with_executable(exe)))
    entries.append(("独立配置的浏览器", launch_via_isolated_profile))
    entries.append(("cmd start", launch_via_cmd_start))
    return entries


# ------------------------------------------------------------------------ opening
async def _await_outcome(
    *,
    page_views: PageViews | None,
    baseline_views: int | None,
    baseline_pids: dict[int, str],
    timeout: float,
    proc: subprocess.Popen | None = None,
) -> tuple[str, str]:
    """Wait for a page request, then for a surviving browser process."""

    deadline = time.monotonic() + timeout
    exited_at: float | None = None
    while time.monotonic() < deadline:
        if page_views is not None and baseline_views is not None:
            views = page_views()
            if views is not None and views > baseline_views:
                return "page", ""
        started = {
            pid: name for pid, name in browser_processes().items() if pid not in baseline_pids
        }
        if started:
            await asyncio.sleep(0.8)
            if page_views is not None and baseline_views is not None:
                views = page_views()
                if views is not None and views > baseline_views:
                    return "page", ""
            alive = {pid: name for pid, name in browser_processes().items() if pid in started}
            if alive:
                return "process", "、".join(sorted(set(alive.values())))
        if proc is not None and proc.poll() is not None:
            # The launcher exited on its own.  A handoff to a running browser
            # shows up as a page request, which is checked above; give that a
            # short grace period and stop waiting instead of burning the full
            # timeout on a browser that refused to start.
            if exited_at is None:
                exited_at = time.monotonic()
            elif time.monotonic() - exited_at > 1.0:
                return "none", ""
        await asyncio.sleep(0.2)
    return "none", ""


async def open_management_page(
    url: str,
    *,
    page_views: PageViews | None = None,
    per_attempt: float = 8.0,
    total_timeout: float = 20.0,
) -> OpenResult:
    """Open ``url`` and report what actually happened.

    ``page_views`` is a zero-argument callable returning how many times the
    server served the page; when it is given, a launch only counts as a success
    once the server sees that request.
    """

    result = OpenResult(opened=False)
    baseline_views = page_views() if page_views is not None else None
    baseline_pids = browser_processes()
    deadline = time.monotonic() + total_timeout

    for label, launch in strategies():
        if time.monotonic() >= deadline:
            break
        attempt = Attempt(label=label, launched=False)
        try:
            started = launch(url)
        except Exception as exc:  # noqa: BLE001 - report, then try the next method
            attempt.error = f"{type(exc).__name__}: {exc}"
            result.attempts.append(attempt)
            continue
        attempt.launched = True
        attempt.detail = started.detail
        remaining = max(1.0, min(per_attempt, deadline - time.monotonic()))
        outcome, where = await _await_outcome(
            page_views=page_views,
            baseline_views=baseline_views,
            baseline_pids=baseline_pids,
            timeout=remaining,
            proc=started.proc,
        )
        attempt.outcome = outcome
        if where:
            attempt.detail = f"{attempt.detail}（{where}）" if attempt.detail else where
        if outcome == "none" and started.proc is not None:
            code = started.proc.poll()
            if code is not None:
                attempt.detail = (
                    f"{attempt.detail}，启动后立即退出（退出码 {code}，"
                    f"0x{code & 0xFFFFFFFF:08X}）"
                )
                tail = _stderr_tail(started.stderr_path)
                if tail:
                    attempt.detail += f"；输出: {tail}"
        _discard(started.stderr_path)
        result.attempts.append(attempt)
        if outcome == "page":
            result.opened = True
            result.how = label
            return result
        if outcome == "process":
            # A browser is starting; opening more windows would only add noise.
            result.how = label
            return result

    if result.attempts and not result.opened:
        result.report = environment_report()
    return result


def summarize(result: OpenResult) -> str:
    """Human-readable multi-line summary for the console."""

    lines: list[str] = []
    for attempt in result.attempts:
        if attempt.error:
            lines.append(f"  - {attempt.label}: 无法调用（{attempt.error}）")
        elif attempt.outcome == "page":
            lines.append(f"  - {attempt.label}: 页面已加载")
        elif attempt.outcome == "process":
            lines.append(f"  - {attempt.label}: 浏览器已启动，但页面请求未到达 {attempt.detail}")
        elif "退出码" in attempt.detail:
            lines.append(f"  - {attempt.label}: {attempt.detail}")
        else:
            lines.append(f"  - {attempt.label}: 已调用，未检测到浏览器")
    if result.report is not None:
        lines.append("  环境：")
        lines.extend(f"    {line}" for line in result.report.lines())
    return "\n".join(lines)


# --------------------------------------------------------------------- diagnosis
def _probe(args: Sequence[str], label: str, command: str, *, timeout: float = 6.0) -> ProbeResult:
    try:
        proc = subprocess.Popen(
            list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=_browser_env(),
            creationflags=_detached_flags(),
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics never raise
        return ProbeResult(label, command, False, False, note=f"{type(exc).__name__}: {exc}")
    try:
        _, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        return ProbeResult(label, command, True, True, proc=proc)
    except Exception as exc:  # noqa: BLE001 - e.g. piped stdio denied
        return ProbeResult(label, command, True, True, note=f"无法读取输出：{exc}", proc=proc)
    return ProbeResult(
        label,
        command,
        True,
        False,
        exit_code=proc.returncode,
        stderr=(err or b"").decode("utf-8", "replace")[:400],
        proc=proc,
    )


def _terminate_tree(proc: subprocess.Popen | None) -> None:
    """Stop one diagnostic process and its children; never touches other windows."""

    if proc is None or proc.poll() is not None or not IS_WINDOWS:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except Exception:  # noqa: BLE001 - best effort
        proc.kill()


def diagnose(url: str | None = None, *, probes: bool = True) -> str:
    """Environment text plus launch probes for ``--check-browser``."""

    report = environment_report()
    lines = ["浏览器启动环境："]
    lines.extend(f"  {line}" for line in report.lines())

    running = browser_process_details()
    if running:
        lines.append("  正在运行的浏览器（完整性级别）：")
        for item in sorted(running, key=lambda entry: entry.name):
            lines.append(f"    {item.name}  pid {item.pid}  {item.integrity}")
        mismatch = [item for item in running if item.elevated]
        if mismatch and not report.integrity.startswith(("高", "系统")):
            lines.append(
                "  ！已有浏览器以更高权限运行，而本项目不是：Windows 会把两者隔开，"
                "自动打开会静默失败。"
            )
    else:
        lines.append("  正在运行的浏览器: 无")

    if not probes:
        return "\n".join(lines)

    lines.append("")
    lines.append("启动测试（可能短暂弹出窗口，测试用的临时窗口会自动关掉）：")
    results: list[ProbeResult] = []
    notepad = _probe(["notepad.exe"], "记事本（任意图形程序）", "notepad.exe", timeout=3.0)
    results.append(notepad)
    lines.append(notepad.summarize())
    _terminate_tree(notepad.proc)

    exe = report.executable or browser_executable()
    url = url or "http://127.0.0.1:8787/"
    if exe is not None:
        try:
            temp_profile = Path(tempfile.mkdtemp(prefix="danmaku-browser-"))
        except OSError:
            temp_profile = None
        if temp_profile is not None:
            profile_probe = _probe(
                _browser_args(exe, url, profile=temp_profile),
                f"{exe.name} + 独立临时配置",
                f"{exe.name} --user-data-dir=<temp> --new-window {url}",
            )
            results.append(profile_probe)
            lines.append(profile_probe.summarize())
            _terminate_tree(profile_probe.proc)
            shutil.rmtree(temp_profile, ignore_errors=True)
        default_probe = _probe(
            _browser_args(exe, url),
            f"{exe.name} + 当前配置",
            f"{exe.name} --new-window {url}",
        )
        results.append(default_probe)
        lines.append(default_probe.summarize())

    lines.append("")
    lines.append(_verdict(report, results))
    return "\n".join(lines)


def _verdict(report: BrowserReport, results: list[ProbeResult]) -> str:
    if not results:
        return "结论：没有可用的启动测试。"
    notepad = results[0]
    if not notepad.started:
        return (
            "结论：连记事本都无法启动，说明当前会话禁止创建图形程序（安全软件、"
            "组策略或受限令牌）。请换一个普通 PowerShell 窗口运行。"
        )
    browsers = results[1:]
    if not browsers:
        return "结论：没有找到浏览器可执行文件。"
    died = [item for item in browsers if item.started and not item.still_running]
    if report.low_integrity and died:
        detail = f"，实测 Chrome 配置目录：{report.chrome_write_error}" if report.chrome_write_error else ""
        return (
            "结论：当前进程运行在「低完整性」（Low IL）{detail}。Windows 的强制完整性控制"
            "会拒绝它写「中等」标记的用户目录，Chrome 因此启动后立即退出（0x80000003）。"
            "换一个从开始菜单打开的普通 PowerShell / Windows Terminal 运行即可；"
            "如果新窗口仍是「低」，就用「以管理员身份运行」启动终端（高完整性同样能写）。"
        ).format(detail=detail)
    if all(item.still_running for item in browsers):
        return (
            "结论：浏览器本身可以正常启动。—— 自动打开失败可能只是检测问题，"
            "请重新运行启动脚本；若仍失败，把上面「启动测试」整段发我。"
        )
    if all(item.started and not item.still_running for item in browsers):
        codes = "，".join(
            f"0x{item.exit_code & 0xFFFFFFFF:08X}" if item.exit_code is not None else "无退出"
            for item in browsers
        )
        return (
            f"结论：可以启动记事本，但浏览器启动后立即退出（{codes}）。"
            "多见于 Chrome 已被以管理员身份运行、配置文件被占用，或被安全软件拦截；"
            "先彻底退出所有 Chrome（任务管理器里也确认没有 chrome.exe）再试，"
            "或把这些退出码发我。"
        )
    return (
        "结论：浏览器启动行为不一致（有的能起、有的立即退出），"
        "把上面「启动测试」整段发我。"
    )
