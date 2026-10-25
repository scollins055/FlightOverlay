"""Exporting the final video: per-segment FFmpeg encodes with the overlay piped in, then audio, then join."""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from fractions import Fraction
from typing import Callable

from ..core import ffmpeg_tools
from ..core.project import Clip, Project
from ..core.timeline import Segment, plan_segments
from ..overlay.layout import OverlayLayout
from ..overlay.renderer import OverlayRenderer
from ..telemetry.series import Telemetry
from .filters import encoder_args, fps_str, segment_graph

ProgressFn = Callable[[float, str], None]


class ExportCancelled(Exception):
    pass


class ExportError(RuntimeError):
    pass


@dataclass
class ExportPlan:
    width: int
    height: int
    fps: Fraction
    segments: list[Segment]
    encoder: str
    total_frames: int

    @property
    def duration(self) -> float:
        return self.total_frames / float(self.fps)


def default_output_path(project: Project) -> str:
    if project.clips:
        first = sorted(project.clips, key=lambda c: c.start)[0]
        base, _ = os.path.splitext(first.path)
        return base + "_overlay.mp4"
    if project.path:
        return os.path.splitext(project.path)[0] + ".mp4"
    return os.path.join(os.path.expanduser("~"), "Videos", "flight_overlay.mp4")


class _StderrReader(threading.Thread):
    def __init__(self, stream):
        super().__init__(daemon=True)
        self.stream = stream
        self.lines: list[str] = []

    def run(self):
        for raw in iter(self.stream.readline, b""):
            self.lines.append(raw.decode("utf-8", "replace").rstrip())
            if len(self.lines) > 200:
                del self.lines[:100]

    def tail(self, n: int = 12) -> str:
        return "\n".join(self.lines[-n:])


class Exporter:
    def __init__(self, project: Project, telemetry: Telemetry | None, progress: ProgressFn | None = None):
        # Work on a snapshot so edits made while exporting don't affect the result.
        self.project = Project.from_dict(project.to_dict(), project.path)
        self.telemetry = telemetry
        self.progress = progress or (lambda f, m: None)
        self._cancel = threading.Event()
        self._proc: subprocess.Popen | None = None

    # ---------------------------------------------------------------- planning

    def plan(self) -> ExportPlan:
        p = self.project
        fps = p.output_fps()
        W, H = p.output_size()
        segs = plan_segments(p, fps)
        enc = ffmpeg_tools.pick_encoder(p.export.codec, p.export.encoder)
        return ExportPlan(W, H, fps, segs, enc, sum(s.frames for s in segs))

    def cancel(self) -> None:
        self._cancel.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    # ---------------------------------------------------------------- running

    def run(self, output_path: str | None = None) -> str:
        p = self.project
        plan = self.plan()
        if not plan.segments:
            raise ExportError("Nothing to export: there are no video clips in the export range.")
        out = os.path.abspath(output_path or p.export.output_path or default_output_path(p))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        work = os.path.join(os.path.dirname(out), "." + os.path.basename(out) + ".parts")
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)
        t_start = time.time()
        try:
            renderer = OverlayRenderer(OverlayLayout.from_dict(p.overlay.to_dict()), p.units, self.telemetry)
            seg_files = []
            done = 0
            for i, seg in enumerate(plan.segments):
                seg_path = os.path.join(work, f"seg_{i:04d}.mp4")
                self._encode_segment(seg, seg_path, plan, renderer, done, t_start)
                seg_files.append(seg_path)
                done += seg.frames
            self._check_cancel()
            audio_path = None
            if p.export.audio != "none":
                self.progress(0.96, "Mixing audio")
                audio_path = self._make_audio(plan, work)
            self._check_cancel()
            self.progress(0.98, "Joining")
            self._join(seg_files, audio_path, out, work)
            self.progress(1.0, f"Done in {time.time() - t_start:.0f} s")
            return out
        except ExportCancelled:
            try:
                if os.path.exists(out) and os.path.getmtime(out) >= t_start:
                    os.remove(out)
            except OSError:
                pass
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise ExportCancelled()

    def _clip_input(self, clip: Clip, seg: Segment) -> list[str]:
        src = max(0.0, seg.start - clip.offset)
        # Start half a frame early so FFmpeg's first frame is the one nearest to `src`
        # (the same frame the preview shows when paused).
        fps = clip.info.fps if clip.info and clip.info.fps > 0 else 30.0
        ss = max(0.0, (round(src * fps) - 0.5) / fps)
        return ["-ss", f"{ss:.6f}", "-t", f"{seg.duration + 1.0:.6f}", "-i", clip.path]

    def _encode_segment(self, seg: Segment, path: str, plan: ExportPlan, renderer: OverlayRenderer,
                        frames_before: int, t_start: float) -> None:
        p = self.project
        W, H, fps = plan.width, plan.height, plan.fps
        args = [ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"]
        main, second = seg.main, seg.second
        if main is not None:
            args += self._clip_input(main, seg)
            main_idx, main_info = 0, main.info
        else:
            args += ["-f", "lavfi", "-t", f"{seg.duration + 1:.6f}", "-i",
                     f"color=c=black:s={W}x{H}:r={fps_str(fps)}"]
            main_idx, main_info = 0, None
        second_idx = None
        if second is not None:
            args += self._clip_input(second, seg)
            second_idx = 1
        ov_idx = 2 if second is not None else 1
        args += ["-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{W}x{H}", "-framerate", fps_str(fps),
                 "-i", "pipe:0"]
        graph = segment_graph(main_info, second.info if second else None, W, H, fps, p.export,
                              main_idx, second_idx, ov_idx)
        args += ["-filter_complex", graph, "-map", "[vout]", "-an"]
        args += encoder_args(plan.encoder, p.export.quality, fps)
        args += ["-frames:v", str(seg.frames), "-r", fps_str(fps), path]

        proc = ffmpeg_tools.popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self._proc = proc
        err = _StderrReader(proc.stderr)
        err.start()
        img = None
        try:
            for i in range(seg.frames):
                if self._cancel.is_set():
                    raise ExportCancelled()
                img = renderer.render_image(W, H, seg.log_time(i), img)
                proc.stdin.write(img.constBits())
                if i % 10 == 0:
                    n = frames_before + i
                    frac = n / max(1, plan.total_frames)
                    el = time.time() - t_start
                    eta = el / frac * (1 - frac) if frac > 0.01 else 0
                    rate = n / el if el > 0 else 0
                    self.progress(0.95 * frac, f"Rendering frame {n + 1} of {plan.total_frames}"
                                  f"  ({rate:.0f} fps, about {_fmt_eta(eta)} left)")
            proc.stdin.close()
        except (BrokenPipeError, OSError) as e:
            if self._cancel.is_set():
                raise ExportCancelled() from e
            proc.wait()
            err.join(timeout=2)
            raise ExportError(f"FFmpeg stopped while encoding:\n{err.tail()}") from e
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        proc.wait()
        err.join(timeout=5)
        self._proc = None
        if self._cancel.is_set():
            raise ExportCancelled()
        if proc.returncode != 0:
            raise ExportError(f"FFmpeg failed (code {proc.returncode}):\n{err.tail()}")

    def _audio_clip(self, seg: Segment) -> Clip | None:
        pref = self.project.export.audio
        order = [seg.a, seg.b] if pref == "A" else [seg.b, seg.a]
        for c in order:
            if c is not None and c.info is not None and c.info.has_audio:
                return c
        return None

    def _make_audio(self, plan: ExportPlan, work: str) -> str | None:
        segs = plan.segments
        if not any(self._audio_clip(s) for s in segs):
            return None
        args = [ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"]
        chains = []
        for i, seg in enumerate(segs):
            c = self._audio_clip(seg)
            if c is not None:
                args += ["-ss", f"{max(0.0, seg.start - c.offset):.6f}", "-t", f"{seg.duration + 0.5:.6f}",
                         "-i", c.path]
            else:
                args += ["-f", "lavfi", "-t", f"{seg.duration + 0.5:.6f}", "-i",
                         "anullsrc=r=48000:cl=stereo"]
            chains.append(f"[{i}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                          f"apad,atrim=0:{seg.duration:.6f},asetpts=PTS-STARTPTS[a{i}]")
        graph = ";".join(chains) + ";" + "".join(f"[a{i}]" for i in range(len(segs))) + \
            f"concat=n={len(segs)}:v=0:a=1[aout]"
        out = os.path.join(work, "audio.m4a")
        args += ["-filter_complex", graph, "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", out]
        r = ffmpeg_tools.run(args)
        if r.returncode != 0:
            raise ExportError(f"Audio mixing failed:\n{r.stderr[-1500:]}")
        return out

    def _join(self, seg_files: list[str], audio: str | None, out: str, work: str) -> None:
        lst = os.path.join(work, "segments.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for s in seg_files:
                f.write("file '" + s.replace("\\", "/").replace("'", "'\\''") + "'\n")
        args = [ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", lst]
        if audio:
            args += ["-i", audio, "-map", "0:v:0", "-map", "1:a:0"]
        args += ["-c", "copy", "-movflags", "+faststart", out]
        r = ffmpeg_tools.run(args)
        if r.returncode != 0:
            raise ExportError(f"Joining the video failed:\n{r.stderr[-1500:]}")


def _fmt_eta(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec} s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m} min {s:02d} s"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"
