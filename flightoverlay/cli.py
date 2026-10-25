"""Command-line tools: probe videos, dump log channels, render overlay frames, export projects.

    python -m flightoverlay.cli probe VIDEO
    python -m flightoverlay.cli dump-channels LOG [--at SECONDS ...]
    python -m flightoverlay.cli render-frame LOG --t SECONDS --out frame.png [--size 1920x1080] [--preset NAME]
    python -m flightoverlay.cli export PROJECT.fovproj [--out OUT.mp4]
    python -m flightoverlay.cli demo FOLDER
"""
from __future__ import annotations

import argparse
import sys
import time


def _progress_printer(prefix: str):
    last = [-1]

    def cb(frac: float, msg: str = "") -> None:
        pct = int(frac * 100)
        if pct != last[0]:
            last[0] = pct
            print(f"\r{prefix} {pct:3d}% {msg:<40}", end="", file=sys.stderr, flush=True)
            if pct >= 100:
                print(file=sys.stderr)
    return cb


def cmd_probe(a) -> int:
    from .core.media import probe
    info = probe(a.video)
    for k, v in info.to_dict().items():
        print(f"{k:16} {v}")
    return 0


def cmd_dump(a) -> int:
    from .telemetry.channels import CHANNELS
    from .telemetry.loader import load_telemetry
    from .telemetry.units import UnitPrefs, format_value

    t0 = time.time()
    tel = load_telemetry(a.log, progress=_progress_printer("Loading"), use_cache=not a.no_cache)
    print(f"Loaded in {time.time() - t0:.2f}s: {tel.source_type}, vehicle={tel.vehicle}, "
          f"duration={tel.duration:.1f}s, utc_offset={tel.utc_offset}, home={tel.home}")
    u = UnitPrefs()
    times = a.at or [tel.duration / 2]
    print(f"{'channel':16} {'source':24} {'n':>7}  " + "  ".join(f"t={t:<10.1f}" for t in times))
    for c in CHANNELS:
        s = tel.channels.get(c.key)
        if s is None:
            continue
        vals = []
        for t in times:
            txt, unit = format_value(c.quantity, s.at(t), u)
            if c.key == "mode":
                txt, unit = (tel.mode_name(t) or "--"), ""
            vals.append(f"{txt + ' ' + unit:<12}")
        print(f"{c.key:16} {tel.sources.get(c.key, ''):24} {len(s):7d}  " + "".join(vals))
    missing = [c.key for c in CHANNELS if c.key not in tel.channels]
    if missing:
        print("not available:", ", ".join(missing))
    print("events:")
    for e in tel.events:
        print(f"  {e.t:8.2f}  {e.label}")
    return 0


def _qt_app():
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.instance() or QGuiApplication(sys.argv[:1])


def grab_video_frame(path: str, t: float, W: int, H: int):
    """Decode one frame of a video at source time t, scaled/padded to WxH, as a QImage."""
    from PySide6.QtGui import QImage
    import subprocess
    from .core import ffmpeg_tools
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,format=bgra")
    proc = subprocess.run([ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}",
                           "-i", path, "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-"],
                          capture_output=True, creationflags=ffmpeg_tools.NO_WINDOW)
    if proc.returncode != 0 or len(proc.stdout) < W * H * 4:
        raise RuntimeError(f"Could not read a frame at {t:.2f}s from {path}")
    img = QImage(proc.stdout[:W * H * 4], W, H, W * 4, QImage.Format.Format_ARGB32)
    return img.copy()


def cmd_render_frame(a) -> int:
    from PySide6.QtGui import QColor, QImage, QPainter
    from .overlay.presets import make_preset
    from .overlay.renderer import OverlayRenderer
    from .telemetry.loader import load_telemetry
    from .telemetry.units import UnitPrefs

    _app = _qt_app()
    W, H = (int(x) for x in a.size.lower().split("x"))
    tel = load_telemetry(a.log) if a.log else None
    layout = make_preset(a.preset)
    rend = OverlayRenderer(layout, UnitPrefs(system=a.units), tel)
    if a.video:
        out = grab_video_frame(a.video, a.t - a.offset, W, H).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    else:
        out = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(QColor(a.bg))
    t0 = time.perf_counter()
    ov = rend.render_image(W, H, a.t)
    dt = time.perf_counter() - t0
    p = QPainter(out)
    p.drawImage(0, 0, ov)
    p.end()
    out.save(a.out)
    print(f"wrote {a.out} (overlay render {dt * 1000:.1f} ms)")
    return 0


def cmd_demo(a) -> int:
    from .demo import make_demo_project
    _app = _qt_app()
    pr = _progress_printer("Demo")
    path = make_demo_project(a.folder, progress=lambda f: pr(f))
    print("project:", path)
    return 0


def cmd_export(a) -> int:
    from .core.project import Project
    from .render.exporter import Exporter
    from .telemetry.loader import load_telemetry

    _app = _qt_app()
    proj = Project.load(a.project)
    if a.resolution:
        proj.export.resolution = a.resolution
    if a.encoder:
        proj.export.encoder = a.encoder
    if a.layout:
        proj.export.layout = a.layout
    tel = load_telemetry(proj.log_path) if proj.log_path else None
    ex = Exporter(proj, tel, progress=_progress_printer("Export"))
    plan = ex.plan()
    print(f"Output {plan.width}x{plan.height} @ {float(plan.fps):.3f} fps, encoder {plan.encoder}, "
          f"{len(plan.segments)} segment(s), {plan.duration:.1f} s")
    for s in plan.segments:
        print(f"  log {s.start:8.2f} - {s.end:8.2f}  frames {s.frames:6d}  A={s.a.name if s.a else '-'}"
              f"  B={s.b.name if s.b else '-'}")
    t0 = time.time()
    out = ex.run(a.out)
    print(f"wrote {out} in {time.time() - t0:.1f}s")
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="flightoverlay")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("probe", help="show video file info")
    s.add_argument("video")
    s.set_defaults(fn=cmd_probe)
    s = sub.add_parser("dump-channels", help="list data channels found in a log")
    s.add_argument("log")
    s.add_argument("--at", type=float, nargs="*", help="log times (s) to print values at")
    s.add_argument("--no-cache", action="store_true")
    s.set_defaults(fn=cmd_dump)
    s = sub.add_parser("render-frame", help="render the overlay at one moment to a PNG")
    s.add_argument("log", nargs="?")
    s.add_argument("--t", type=float, required=True, help="log time in seconds")
    s.add_argument("--out", required=True)
    s.add_argument("--size", default="1920x1080")
    s.add_argument("--preset", default="Dashboard")
    s.add_argument("--units", default="metric", choices=["metric", "imperial"])
    s.add_argument("--bg", default="#406080", help="background colour when no video is given")
    s.add_argument("--video", help="draw on top of a frame from this video")
    s.add_argument("--offset", type=float, default=0.0, help="log time at which the video starts")
    s.set_defaults(fn=cmd_render_frame)
    s = sub.add_parser("export", help="export a project to a video file")
    s.add_argument("project")
    s.add_argument("--out")
    s.add_argument("--resolution", choices=["match", "720p", "1080p", "1440p", "2160p"])
    s.add_argument("--encoder", choices=["auto", "gpu", "cpu"])
    s.add_argument("--layout", choices=["pip", "side"])
    s.set_defaults(fn=cmd_export)
    s = sub.add_parser("demo", help="create a demo log and videos")
    s.add_argument("folder")
    s.set_defaults(fn=cmd_demo)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
