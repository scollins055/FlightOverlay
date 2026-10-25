"""Locating and running the bundled FFmpeg / ffprobe executables."""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Stops a console window flashing up for every ffmpeg call on Windows.
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get("FLIGHTOVERLAY_FFMPEG_DIR")
    if env:
        dirs.append(Path(env))
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(Path(meipass) / "ffmpeg")
        dirs.append(Path(sys.executable).parent / "ffmpeg")
    project_root = Path(__file__).resolve().parents[2]
    dirs.append(project_root / "vendor" / "ffmpeg")
    return dirs


@functools.cache
def find_tool(name: str) -> str:
    """Return the full path of 'ffmpeg' or 'ffprobe'."""
    exe = name + (".exe" if sys.platform == "win32" else "")
    for d in _candidate_dirs():
        p = d / exe
        if p.is_file():
            return str(p)
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(
        f"{name} was not found. Run tools\\get_ffmpeg.ps1 or install FFmpeg and add it to PATH."
    )


def ffmpeg() -> str:
    return find_tool("ffmpeg")


def ffprobe() -> str:
    return find_tool("ffprobe")


def run(args: list[str], timeout: float | None = None, check: bool = False) -> subprocess.CompletedProcess:
    """Run a command, capture text output, never show a console window."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=check,
        creationflags=NO_WINDOW,
    )


def popen(args: list[str], **kw) -> subprocess.Popen:
    kw.setdefault("creationflags", NO_WINDOW)
    return subprocess.Popen(args, **kw)


@functools.cache
def list_filters() -> frozenset[str]:
    out = run([ffmpeg(), "-hide_banner", "-filters"], timeout=30).stdout
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and len(parts[0]) == 3:
            names.add(parts[1])
    return frozenset(names)


def has_filter(name: str) -> bool:
    try:
        return name in list_filters()
    except Exception:
        return False


@functools.cache
def encoder_works(name: str) -> bool:
    """True if this encoder can actually encode here (e.g. NVENC needs an NVIDIA GPU)."""
    try:
        r = run(
            [ffmpeg(), "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "color=c=black:s=256x256:r=30:d=0.2",
             "-c:v", name, "-pix_fmt", "yuv420p", "-f", "null", "-"],
            timeout=30,
        )
        return r.returncode == 0
    except Exception:
        return False


def pick_encoder(codec: str, preference: str = "auto") -> str:
    """codec: 'h264' or 'hevc'. preference: 'auto', 'gpu' or 'cpu'."""
    cpu = {"h264": "libx264", "hevc": "libx265"}[codec]
    gpu_candidates = {"h264": ["h264_nvenc", "h264_qsv", "h264_amf"],
                      "hevc": ["hevc_nvenc", "hevc_qsv", "hevc_amf"]}[codec]
    if preference == "cpu":
        return cpu
    for enc in gpu_candidates:
        if encoder_works(enc):
            return enc
    return cpu
