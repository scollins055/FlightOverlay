"""Checks that a (packaged) install works: FFmpeg, log reading, overlay, preview playback, export.

    FlightOverlay.exe --self-test report.txt
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import traceback


def run(report_path: str) -> int:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["selftest"])
    lines: list[str] = []
    ok = True

    def step(name, fn):
        nonlocal ok
        t0 = time.time()
        try:
            res = fn()
            lines.append(f"PASS {name} ({time.time() - t0:.1f}s) {res or ''}")
        except Exception:  # noqa: BLE001
            ok = False
            lines.append(f"FAIL {name}\n{traceback.format_exc()}")

    def wait(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    tmp = tempfile.mkdtemp(prefix="flightoverlay_selftest_")
    try:
        from . import demo
        from .core import ffmpeg_tools
        from .core.media import probe
        from .core.project import Clip, Project
        from .overlay.presets import make_preset
        from .overlay.renderer import OverlayRenderer
        from .render.exporter import Exporter
        from .telemetry.loader import load_telemetry

        state = {}
        step("ffmpeg found", lambda: f"{ffmpeg_tools.ffmpeg()} / {ffmpeg_tools.ffprobe()}")
        step("encoder", lambda: ffmpeg_tools.pick_encoder("h264", "auto"))

        def make_files():
            sim = demo.simulate()
            state["bin"] = os.path.join(tmp, "f.bin")
            state["tlog"] = os.path.join(tmp, "f.tlog")
            demo.write_bin(state["bin"], sim)
            demo.write_tlog(state["tlog"], sim)
            state["vid"] = os.path.join(tmp, "v.mp4")
            demo.write_camera_video(state["vid"], sim, 100.0, 3.0, size=(640, 360))
        step("demo files", make_files)

        def load_logs():
            state["tel"] = load_telemetry(state["bin"], use_cache=False)
            t2 = load_telemetry(state["tlog"], use_cache=False)
            return f"bin {len(state['tel'].channels)} channels, tlog {len(t2.channels)} channels"
        step("read logs (pymavlink)", load_logs)

        def overlay():
            r = OverlayRenderer(make_preset("Dashboard"), telemetry=state["tel"])
            img = r.render_image(1280, 720, 150.0)
            img.save(os.path.join(tmp, "overlay.png"))
            return f"{img.width()}x{img.height()}"
        step("draw overlay", overlay)

        def playback():
            from .ui.playback import TrackPlayer
            tp = TrackPlayer(0)
            c = Clip(path=state["vid"], info=probe(state["vid"]))
            tp.show(c, 1.0, False)
            t0 = time.time()
            while tp.image() is None and time.time() - t0 < 10:
                wait(50)
            if tp.image() is None:
                raise RuntimeError(f"no video frame from Qt Multimedia (error: {tp.error!r})")
            return f"frame {tp.image().width()}x{tp.image().height()}"
        step("preview playback (Qt Multimedia)", playback)

        def export():
            p = Project()
            p.clips = [Clip(path=state["vid"], info=probe(state["vid"]), offset=100.0)]
            out = Exporter(p, state["tel"]).run(os.path.join(tmp, "out.mp4"))
            return f"{probe(out).duration:.2f}s"
        step("export", export)
    finally:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + f"\nRESULT {'OK' if ok else 'FAILED'}\n")
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1
