# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the portable DanmakuPet build (onedir).

The output is a self-contained folder:

    dist/DanmakuPet/
        DanmakuPet.exe
        _internal/...            runtime, PySide6, the web page, the pet assets
        .env                     created on demand (via the settings page)

Everything the app writes stays next to the exe (see danmaku_pet/paths.py), so
the folder can be copied anywhere and deleted without leaving traces behind.
"""

from pathlib import Path
import sys

ROOT = Path(SPECPATH).resolve().parent
VENDOR = ROOT / "vendor" / "dafeiyu" / "package"

datas = []


def add(source: Path, target: str) -> None:
    """Bundle ``source`` when it exists; skip optional pieces with a notice."""

    if source.exists():
        datas.append((str(source), target))
    else:
        print(f"[spec] 跳过不存在的资源：{source}", file=sys.stderr)


# The management page is served from RESOURCE_ROOT/danmaku_pet/web.
add(ROOT / "danmaku_pet" / "web", "danmaku_pet/web")
# Seed persona for a fresh portable folder.
add(ROOT / "data" / "persona.json", "data")
# The desktop pet is executed from these sources at runtime (runpy), so they
# must stay plain .py files, not bytecode inside the archive.  Only the sources
# are needed: the npm package also ships a 174 MB prebuilt helper under
# runtime/bin plus a __pycache__, and none of that is used here.
for module in sorted((VENDOR / "runtime").glob("*.py")):
    add(module, "vendor/dafeiyu/package/runtime")
add(VENDOR / "assets", "vendor/dafeiyu/package/assets")
for extra in ("LICENSE", "ASSET_LICENSE.md"):
    add(VENDOR / extra, "vendor/dafeiyu/package")

hiddenimports = [
    # uvicorn resolves its loop/protocol implementations at runtime.
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "websockets",
    "websockets.legacy",
    "websockets.legacy.server",
    "wsproto",
    "httptools",
    # TTS / audio / config.
    "aiohttp",
    "certifi",
    "edge_tts",
    "pygame",
    "dotenv",
    # The vendored desktop pet is started with runpy, so PyInstaller cannot see
    # its imports. Only QtCore/QtGui/QtWidgets are needed there.
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]

excludes = [
    "tkinter",
    "matplotlib",
    "numpy",
    "pandas",
    "scipy",
    "IPython",
    "pytest",
    "unittest",
    "pydoc",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtGraphs",
    "PySide6.QtHelp",
    "PySide6.QtHttpServer",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialBus",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    "PySide6.QtXml",
]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DanmakuPet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    # Modules are also importable by the runpy-loaded desktop pet helper.
    contents_directory="_internal",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DanmakuPet",
)
