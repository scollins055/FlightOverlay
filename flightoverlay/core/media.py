"""Reading video file information with ffprobe."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from fractions import Fraction

from . import ffmpeg_tools

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".m2ts", ".webm", ".lrv", ".insv"}
LOG_EXTS = {".bin", ".tlog", ".log"}

# Common frame rates; phone files often report rates like 29.98 that should be 29.97.
_STANDARD_RATES = [
    Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
    Fraction(48), Fraction(50), Fraction(60000, 1001), Fraction(60), Fraction(100),
    Fraction(120000, 1001), Fraction(120), Fraction(240000, 1001), Fraction(240),
]


@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    width: int = 0              # displayed size (after rotation)
    height: int = 0
    fps_num: int = 30
    fps_den: int = 1
    rotation: int = 0           # degrees, from display matrix
    codec: str = ""
    pix_fmt: str = ""
    color_transfer: str = ""
    has_audio: bool = False
    creation_utc: float | None = None   # unix seconds of recording start (best guess)
    creation_source: str = ""           # 'apple', 'creation_time', 'file_mtime', ''
    size_bytes: int = 0
    mtime: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def fps(self) -> float:
        return self.fps_num / self.fps_den if self.fps_den else 30.0

    @property
    def fps_fraction(self) -> Fraction:
        return Fraction(self.fps_num, self.fps_den or 1)

    @property
    def is_hdr(self) -> bool:
        return self.color_transfer in ("smpte2084", "arib-std-b67")

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MediaInfo":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


def snap_fps(fr: Fraction) -> Fraction:
    if fr <= 0:
        return Fraction(30)
    best = min(_STANDARD_RATES, key=lambda std: abs(float(fr) - float(std)) / float(std))
    if abs(float(fr) - float(best)) / float(best) < 0.004:
        return best
    # Keep odd rates but limit denominator size.
    return fr.limit_denominator(1001)


def _parse_rate(s: str | None) -> Fraction | None:
    if not s or s in ("0/0", "0"):
        return None
    try:
        fr = Fraction(s)
        return fr if fr > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def parse_timestamp(s: str) -> float | None:
    """Parse ISO-8601 timestamps as written by cameras. Naive times are treated as UTC."""
    if not s:
        return None
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ts = dt.timestamp()
    # 1904/1970 epoch placeholders from cameras without a clock
    if ts < 946684800:  # 2000-01-01
        return None
    return ts


def probe(path: str) -> MediaInfo:
    path = os.path.abspath(path)
    r = ffmpeg_tools.run(
        [ffmpeg_tools.ffprobe(), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        timeout=60,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Could not read video file {os.path.basename(path)}: {r.stderr.strip()}")
    data = json.loads(r.stdout or "{}")
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    vstreams = [s for s in streams if s.get("codec_type") == "video"
                and not s.get("disposition", {}).get("attached_pic")]
    if not vstreams:
        raise RuntimeError(f"{os.path.basename(path)} has no video stream")
    v = vstreams[0]
    st = os.stat(path)
    info = MediaInfo(path=path, size_bytes=st.st_size, mtime=st.st_mtime)

    info.codec = v.get("codec_name", "")
    info.pix_fmt = v.get("pix_fmt", "")
    info.color_transfer = v.get("color_transfer", "") or ""
    w, h = int(v.get("width", 0)), int(v.get("height", 0))

    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                rot = int(round(float(sd["rotation"])))
            except (TypeError, ValueError):
                pass
    if not rot:
        try:
            rot = int(v.get("tags", {}).get("rotate", 0))
        except (TypeError, ValueError):
            rot = 0
    info.rotation = rot % 360
    if info.rotation in (90, 270):
        w, h = h, w
    info.width, info.height = w, h

    fr = _parse_rate(v.get("avg_frame_rate")) or _parse_rate(v.get("r_frame_rate")) or Fraction(30)
    fr = snap_fps(fr)
    info.fps_num, info.fps_den = fr.numerator, fr.denominator

    dur = None
    for src in (fmt.get("duration"), v.get("duration")):
        try:
            if src is not None:
                dur = float(src)
                break
        except ValueError:
            pass
    info.duration = dur or 0.0
    info.has_audio = any(s.get("codec_type") == "audio" for s in streams)

    # Recording start time
    ftags = {k.lower(): val for k, val in (fmt.get("tags") or {}).items()}
    vtags = {k.lower(): val for k, val in (v.get("tags") or {}).items()}
    apple = ftags.get("com.apple.quicktime.creationdate")
    ts = parse_timestamp(apple) if apple else None
    if ts is not None:
        info.creation_utc, info.creation_source = ts, "apple"
    else:
        ct = ftags.get("creation_time") or vtags.get("creation_time")
        ts = parse_timestamp(ct) if ct else None
        if ts is not None:
            info.creation_utc, info.creation_source = ts, "creation_time"
        else:
            # File modified time is usually when recording stopped.
            info.creation_utc, info.creation_source = st.st_mtime - info.duration, "file_mtime"
    info.extra = {"format_name": fmt.get("format_name", ""),
                  "encoder": ftags.get("encoder", ""),
                  "make": ftags.get("com.apple.quicktime.make", ftags.get("make", "")),
                  "model": ftags.get("com.apple.quicktime.model", ftags.get("model", ""))}
    return info


def is_video_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def is_log_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in LOG_EXTS
