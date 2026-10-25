"""Building FFmpeg filter graphs and encoder arguments for export."""
from __future__ import annotations

from fractions import Fraction

from ..core.media import MediaInfo
from ..core.project import ExportSettings

TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
           "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def fps_str(fps: Fraction) -> str:
    return f"{fps.numerator}/{fps.denominator}" if fps.denominator != 1 else str(fps.numerator)


def fit_chain(info: MediaInfo | None, w: int, h: int, fps: Fraction, tonemap: bool, pad: bool = True) -> str:
    """Video chain: reset timestamps, constant frame rate, scale to fit w x h (letterboxed if pad)."""
    parts = ["setpts=PTS-STARTPTS", f"fps={fps_str(fps)}",
             f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=bicubic"]
    if tonemap and info is not None and info.is_hdr:
        parts.append(TONEMAP)
    if pad:
        parts.append(f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    parts += ["setsar=1", "format=yuv420p", "tpad=stop_mode=clone:stop_duration=10"]
    return ",".join(parts)


def pip_box(W: int, H: int, s: ExportSettings, info: MediaInfo | None) -> tuple[int, int, int, int]:
    """Pixel rectangle (x, y, w, h) of the picture-in-picture, keeping the clip's aspect ratio."""
    aspect = (info.width / info.height) if info and info.height else 16 / 9
    pw = W * s.pip_size
    ph = pw / aspect
    if ph > H * 0.6:
        ph = H * 0.6
        pw = ph * aspect
    pw, ph = int(pw) // 2 * 2, int(ph) // 2 * 2
    x = int(round(s.pip_x * W - pw / 2))
    y = int(round(s.pip_y * H - ph / 2))
    x = max(0, min(W - pw, x))
    y = max(0, min(H - ph, y))
    return x, y, pw, ph


def segment_graph(main: MediaInfo | None, second: MediaInfo | None, W: int, H: int, fps: Fraction,
                  s: ExportSettings, main_idx: int, second_idx: int | None, overlay_idx: int) -> str:
    """Filter graph producing [vout]: main video (+ second camera) + telemetry overlay on top."""
    g = []
    if second is None or second_idx is None:
        g.append(f"[{main_idx}:v]{fit_chain(main, W, H, fps, s.hdr_tonemap)}[base]")
    elif s.layout == "side":
        lw = (W // 2) // 2 * 2
        rw = W - lw
        g.append(f"[{main_idx}:v]{fit_chain(main, lw, H, fps, s.hdr_tonemap)}[l]")
        g.append(f"[{second_idx}:v]{fit_chain(second, rw, H, fps, s.hdr_tonemap)}[r]")
        g.append("[l][r]hstack=inputs=2[base]")
    else:
        x, y, pw, ph = pip_box(W, H, s, second)
        b = max(2, H // 360 * 2)
        g.append(f"[{main_idx}:v]{fit_chain(main, W, H, fps, s.hdr_tonemap)}[bg]")
        g.append(f"[{second_idx}:v]{fit_chain(second, pw, ph, fps, s.hdr_tonemap)},"
                 f"pad=iw+{2 * b}:ih+{2 * b}:{b}:{b}:color=white@0.85[pip]")
        g.append(f"[bg][pip]overlay=x={x - b}:y={y - b}:eof_action=repeat:format=auto[base]")
    g.append(f"[{overlay_idx}:v]setpts=PTS-STARTPTS[ovl]")
    g.append("[base][ovl]overlay=0:0:format=auto:alpha=premultiplied:eof_action=repeat,format=yuv420p[vout]")
    return ";".join(g)


_QUALITY = {
    "libx264": {"fast": ["-preset", "veryfast", "-crf", "23"],
                "balanced": ["-preset", "medium", "-crf", "20"],
                "high": ["-preset", "slow", "-crf", "17"]},
    "libx265": {"fast": ["-preset", "veryfast", "-crf", "26"],
                "balanced": ["-preset", "medium", "-crf", "23"],
                "high": ["-preset", "slow", "-crf", "20"]},
    "h264_nvenc": {"fast": ["-preset", "p3", "-rc", "vbr", "-cq", "25", "-b:v", "0"],
                   "balanced": ["-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"],
                   "high": ["-preset", "p7", "-rc", "vbr", "-cq", "18", "-b:v", "0"]},
    "hevc_nvenc": {"fast": ["-preset", "p3", "-rc", "vbr", "-cq", "28", "-b:v", "0"],
                   "balanced": ["-preset", "p5", "-rc", "vbr", "-cq", "24", "-b:v", "0"],
                   "high": ["-preset", "p7", "-rc", "vbr", "-cq", "21", "-b:v", "0"]},
    "h264_qsv": {"fast": ["-preset", "faster", "-global_quality", "25"],
                 "balanced": ["-preset", "medium", "-global_quality", "22"],
                 "high": ["-preset", "slow", "-global_quality", "19"]},
    "hevc_qsv": {"fast": ["-preset", "faster", "-global_quality", "27"],
                 "balanced": ["-preset", "medium", "-global_quality", "24"],
                 "high": ["-preset", "slow", "-global_quality", "21"]},
    "h264_amf": {"fast": ["-quality", "speed", "-rc", "cqp", "-qp_i", "24", "-qp_p", "26"],
                 "balanced": ["-quality", "balanced", "-rc", "cqp", "-qp_i", "21", "-qp_p", "23"],
                 "high": ["-quality", "quality", "-rc", "cqp", "-qp_i", "18", "-qp_p", "20"]},
    "hevc_amf": {"fast": ["-quality", "speed", "-rc", "cqp", "-qp_i", "26", "-qp_p", "28"],
                 "balanced": ["-quality", "balanced", "-rc", "cqp", "-qp_i", "23", "-qp_p", "25"],
                 "high": ["-quality", "quality", "-rc", "cqp", "-qp_i", "20", "-qp_p", "22"]},
}


def timescale(fps: Fraction) -> int:
    return fps.numerator if fps.denominator > 1 else fps.numerator * 1000


def encoder_args(encoder: str, quality: str, fps: Fraction) -> list[str]:
    args = ["-c:v", encoder] + _QUALITY.get(encoder, {}).get(quality, [])
    args += ["-pix_fmt", "yuv420p", "-g", str(max(1, round(float(fps) * 2))),
             "-video_track_timescale", str(timescale(fps))]
    if "265" in encoder or "hevc" in encoder:
        args += ["-tag:v", "hvc1"]
    return args
