# PyInstaller build definition. Build with:  powershell -ExecutionPolicy Bypass -File build.ps1
import os

from PyInstaller.utils.hooks import collect_submodules

root = os.path.abspath(".")
ff = os.path.join(root, "vendor", "ffmpeg")

hidden = collect_submodules("pymavlink.dialects.v10") + collect_submodules("pymavlink.dialects.v20")
hidden += ["pymavlink.dfindexer", "fastcrc"]

datas = [
    (os.path.join(root, "flightoverlay", "assets"), os.path.join("flightoverlay", "assets")),
    (os.path.join(ff, "ffmpeg.exe"), "ffmpeg"),
    (os.path.join(ff, "ffprobe.exe"), "ffmpeg"),
]
if os.path.exists(os.path.join(ff, "FFMPEG_LICENSE.txt")):
    datas.append((os.path.join(ff, "FFMPEG_LICENSE.txt"), "ffmpeg"))

excludes = [
    "tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtGraphs", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtQuick3D", "PySide6.QtBluetooth",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtLocation",
    "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtTextToSpeech", "PySide6.QtSpatialAudio", "PySide6.QtHttpServer",
]

a = Analysis(
    ["launcher.py"],
    pathex=[root],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FlightOverlay",
    icon=os.path.join(root, "flightoverlay", "assets", "icon.ico"),
    console=False,
    debug=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="FlightOverlay")
