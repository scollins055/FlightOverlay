"""End-to-end export tests with small synthetic videos (needs FFmpeg)."""
import json
import subprocess

import numpy as np
import pytest

from flightoverlay import demo
from flightoverlay.core import ffmpeg_tools
from flightoverlay.core.media import probe
from flightoverlay.core.project import Clip, Project
from flightoverlay.render.exporter import Exporter

W, H = 640, 360


def _frame(path: str, t: float | None = None, index: int | None = None) -> np.ndarray:
    """Decode one frame as an RGB array (by time, or by exact frame index)."""
    args = [ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error"]
    if index is not None:
        args += ["-i", path, "-vf", f"select=eq(n\\,{index}),scale={W}:{H}", "-vsync", "0"]
    else:
        args += ["-ss", f"{t:.4f}", "-i", path, "-vf", f"scale={W}:{H}"]
    args += ["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    out = subprocess.run(args, capture_output=True, creationflags=ffmpeg_tools.NO_WINDOW).stdout
    return np.frombuffer(out, dtype=np.uint8).reshape(H, W, 3).astype(np.float32)


@pytest.fixture(scope="module")
def videos(tmp_path_factory, qapp, sim):
    d = tmp_path_factory.mktemp("vids")
    a = str(d / "camA.mp4")
    b = str(d / "camB.mp4")
    demo.write_camera_video(a, sim, 100.0, 6.0, size=(W, H), fps=30, label="A")
    demo.write_camera_video(b, sim, 103.0, 3.0, size=(W, H), fps=30, label="B")
    return a, b


def _project(videos, layout="pip"):
    a, b = videos
    p = Project()
    p.clips = [Clip(path=a, info=probe(a), track=0, offset=100.0),
               Clip(path=b, info=probe(b), track=1, offset=103.0)]
    p.export.encoder = "cpu"
    p.export.quality = "fast"
    p.export.layout = layout
    return p


def test_export_pip(videos, bin_tel, tmp_path):
    p = _project(videos)
    ex = Exporter(p, bin_tel)
    plan = ex.plan()
    assert [bool(s.second) for s in plan.segments] == [False, True]
    assert plan.total_frames == 180
    out = ex.run(str(tmp_path / "out.mp4"))
    info = json.loads(ffmpeg_tools.run([ffmpeg_tools.ffprobe(), "-v", "error", "-count_frames", "-print_format",
                                        "json", "-show_streams", out]).stdout)
    v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    assert int(v["nb_read_frames"]) == 180
    assert (v["width"], v["height"]) == (W, H)
    assert any(s["codec_type"] == "audio" for s in info["streams"])


def test_export_uses_the_right_source_frames(videos, bin_tel, tmp_path):
    """Output frame i must show the source frame nearest to its log time (same as the preview)."""
    p = _project(videos)
    p.clips = p.clips[:1]
    p.overlay.style.panels = False
    for w in p.overlay.widgets:
        w.enabled = False                       # compare pure video
    p.clips[0].offset = 100.0 + 0.4 / 30        # offset by a fraction of a frame
    out = Exporter(p, bin_tel).run(str(tmp_path / "frames.mp4"))
    src = videos[0]
    for i in (5, 47, 120):
        log_t = p.clips[0].start + i / 30
        k = round((log_t - p.clips[0].offset) * 30)
        got = _frame(out, index=i)
        errs = {d: float(np.mean((got - _frame(src, index=k + d)) ** 2)) for d in (-1, 0, 1)}
        assert min(errs, key=errs.get) == 0, errs


def test_side_by_side_and_cancel(videos, bin_tel, tmp_path):
    p = _project(videos, layout="side")
    out = Exporter(p, bin_tel).run(str(tmp_path / "side.mp4"))
    assert probe(out).duration == pytest.approx(6.0, abs=0.05)
    ex = Exporter(p, bin_tel, progress=lambda f, m: ex.cancel() if f > 0.2 else None)
    from flightoverlay.render.exporter import ExportCancelled
    with pytest.raises(ExportCancelled):
        ex.run(str(tmp_path / "cancelled.mp4"))
    assert not (tmp_path / "cancelled.mp4").exists()
