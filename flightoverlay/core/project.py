"""Project model: the log, the video clips on the timeline, overlay layout and export settings."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from fractions import Fraction

from ..overlay.layout import OverlayLayout
from ..telemetry.units import UnitPrefs
from .media import MediaInfo

TRACK_NAMES = ["Cam A", "Cam B"]


@dataclass
class Clip:
    path: str
    info: MediaInfo | None = None
    track: int = 0                 # 0 = A (main camera), 1 = B (second camera)
    offset: float = 0.0            # log time (s) at which the video's first frame plays
    trim_in: float = 0.0           # seconds trimmed from the start of the video
    trim_out: float | None = None  # end point in video seconds (None = full length)
    tz_shift_h: float = 0.0        # timestamp correction, hours
    stamp_is_end: bool = False     # timestamp marks the end of recording
    auto_placed: bool = False
    verified: bool = False         # user has aligned or moved it
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def duration(self) -> float:
        return self.info.duration if self.info else 0.0

    @property
    def src_out(self) -> float:
        d = self.duration
        return min(self.trim_out, d) if self.trim_out is not None else d

    @property
    def start(self) -> float:
        return self.offset + self.trim_in

    @property
    def end(self) -> float:
        return self.offset + self.src_out

    def contains(self, t: float) -> bool:
        return self.start <= t < self.end

    def to_dict(self, base_dir: str | None = None) -> dict:
        d = {k: v for k, v in asdict(self).items() if k != "info"}
        d["info"] = self.info.to_dict() if self.info else None
        if base_dir:
            try:
                d["rel_path"] = os.path.relpath(self.path, base_dir)
            except ValueError:
                pass
        return d

    @classmethod
    def from_dict(cls, d: dict, base_dir: str | None = None) -> "Clip":
        known = set(cls.__dataclass_fields__) - {"info"}
        c = cls(**{k: v for k, v in d.items() if k in known})
        if d.get("info"):
            c.info = MediaInfo.from_dict(d["info"])
        c.path = _resolve(c.path, d.get("rel_path"), base_dir)
        if c.info:
            c.info.path = c.path
        return c


@dataclass
class ExportSettings:
    output_path: str = ""
    resolution: str = "match"     # match, 720p, 1080p, 1440p, 2160p
    fps: str = "match"            # match, 24, 25, 30, 50, 60
    codec: str = "h264"           # h264, hevc
    quality: str = "balanced"     # fast, balanced, high
    encoder: str = "auto"         # auto, gpu, cpu
    layout: str = "pip"           # pip, side
    pip_x: float = 0.80           # PiP centre, fraction of frame
    pip_y: float = 0.30
    pip_size: float = 0.32        # PiP width as fraction of frame width
    audio: str = "A"              # A, B, none
    gaps: str = "skip"            # skip, black
    hdr_tonemap: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "ExportSettings":
        d = d or {}
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


_RES = {"720p": 720, "1080p": 1080, "1440p": 1440, "2160p": 2160}


@dataclass
class Project:
    log_path: str | None = None
    clips: list[Clip] = field(default_factory=list)
    overlay: OverlayLayout = field(default_factory=lambda: OverlayLayout.from_dict(None))
    units: UnitPrefs = field(default_factory=UnitPrefs)
    export: ExportSettings = field(default_factory=ExportSettings)
    range_in: float | None = None
    range_out: float | None = None
    graph_channels: list[str] = field(default_factory=lambda: ["throttle", "alt_rel"])
    path: str | None = None        # where the project file is saved (not stored in the file)

    # ---------------------------------------------------------------- clips

    def clip(self, cid: str) -> Clip | None:
        return next((c for c in self.clips if c.id == cid), None)

    def clips_on(self, track: int) -> list[Clip]:
        return sorted((c for c in self.clips if c.track == track), key=lambda c: c.start)

    def clip_at(self, track: int, t: float) -> Clip | None:
        for c in self.clips:
            if c.track == track and c.contains(t):
                return c
        return None

    def overlaps(self, track: int, start: float, end: float, ignore: str | None = None) -> bool:
        eps = 1e-6
        for c in self.clips:
            if c.track == track and c.id != ignore and start < c.end - eps and end > c.start + eps:
                return True
        return False

    def clips_span(self) -> tuple[float, float] | None:
        if not self.clips:
            return None
        return min(c.start for c in self.clips), max(c.end for c in self.clips)

    def output_range(self) -> tuple[float, float]:
        span = self.clips_span() or (0.0, 0.0)
        a = self.range_in if self.range_in is not None else span[0]
        b = self.range_out if self.range_out is not None else span[1]
        if b < a:
            a, b = b, a
        return a, b

    # ---------------------------------------------------------------- output format

    def main_info(self) -> MediaInfo | None:
        a, b = self.output_range()
        for track in (0, 1):
            for c in self.clips_on(track):
                if c.info and c.end > a and c.start < b:
                    return c.info
        return self.clips[0].info if self.clips and self.clips[0].info else None

    def output_size(self) -> tuple[int, int]:
        info = self.main_info()
        src_w, src_h = (info.width, info.height) if info and info.width else (1920, 1080)
        portrait = src_h > src_w
        res = self.export.resolution
        if res in _RES:
            short = _RES[res]
            long_ = int(round(short * 16 / 9))
            w, h = (short, long_) if portrait else (long_, short)
        else:
            w, h = src_w, src_h
            longest = max(w, h)
            if longest > 4096:
                k = 4096 / longest
                w, h = int(w * k), int(h * k)
        return max(2, w // 2 * 2), max(2, h // 2 * 2)

    def output_fps(self) -> Fraction:
        if self.export.fps != "match":
            return Fraction(int(self.export.fps))
        info = self.main_info()
        fr = info.fps_fraction if info else Fraction(30)
        if fr > 60:
            fr = Fraction(60000, 1001) if fr.denominator == 1001 else Fraction(60)
        return fr

    # ---------------------------------------------------------------- persistence

    def to_dict(self) -> dict:
        base = os.path.dirname(self.path) if self.path else None
        d = {
            "app": "FlightOverlay",
            "version": 1,
            "log_path": self.log_path,
            "clips": [c.to_dict(base) for c in self.clips],
            "overlay": self.overlay.to_dict(),
            "units": self.units.to_dict(),
            "export": self.export.to_dict(),
            "range_in": self.range_in,
            "range_out": self.range_out,
            "graph_channels": self.graph_channels,
        }
        if base and self.log_path:
            try:
                d["log_rel_path"] = os.path.relpath(self.log_path, base)
            except ValueError:
                pass
        return d

    @classmethod
    def from_dict(cls, d: dict, path: str | None = None) -> "Project":
        base = os.path.dirname(path) if path else None
        p = cls(path=path)
        p.log_path = _resolve(d.get("log_path"), d.get("log_rel_path"), base) if d.get("log_path") else None
        p.clips = [Clip.from_dict(c, base) for c in d.get("clips", [])]
        p.overlay = OverlayLayout.from_dict(d.get("overlay"))
        p.units = UnitPrefs.from_dict(d.get("units"))
        p.export = ExportSettings.from_dict(d.get("export"))
        p.range_in = d.get("range_in")
        p.range_out = d.get("range_out")
        p.graph_channels = d.get("graph_channels") or ["throttle", "alt_rel"]
        return p

    def save(self, path: str | None = None) -> str:
        if path:
            self.path = os.path.abspath(path)
        if not self.path:
            raise ValueError("No project file path")
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=1)
        os.replace(tmp, self.path)
        return self.path

    @classmethod
    def load(cls, path: str) -> "Project":
        path = os.path.abspath(path)
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f), path)


def _resolve(abs_path: str | None, rel_path: str | None, base_dir: str | None) -> str | None:
    """Prefer the stored absolute path; fall back to the path relative to the project file."""
    if abs_path and os.path.exists(abs_path):
        return abs_path
    if rel_path and base_dir:
        cand = os.path.normpath(os.path.join(base_dir, rel_path))
        if os.path.exists(cand):
            return cand
    return abs_path
