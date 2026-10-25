"""Splitting the output range into segments where the set of visible clips doesn't change."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .project import Clip, Project


@dataclass
class Segment:
    f0: int                 # first output frame (relative to the range start)
    f1: int                 # one past the last frame
    start: float            # log time of frame f0
    fps: Fraction
    a: Clip | None = None   # clip on track A
    b: Clip | None = None   # clip on track B

    @property
    def frames(self) -> int:
        return self.f1 - self.f0

    @property
    def duration(self) -> float:
        return self.frames / float(self.fps)

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def main(self) -> Clip | None:
        return self.a or self.b

    @property
    def second(self) -> Clip | None:
        return self.b if self.a else None

    def log_time(self, i: int) -> float:
        """Log time of the i-th frame of this segment."""
        return self.start + i / float(self.fps)


def plan_segments(project: Project, fps: Fraction | None = None) -> list[Segment]:
    fps = fps or project.output_fps()
    r_in, r_out = project.output_range()
    if r_out <= r_in:
        return []
    fpsf = float(fps)

    def to_frame(t: float) -> int:
        return int(round((t - r_in) * fpsf))

    total = to_frame(r_out)
    cuts = {0, total}
    for c in project.clips:
        for t in (c.start, c.end):
            f = to_frame(t)
            if 0 < f < total:
                cuts.add(f)
    cuts_sorted = sorted(cuts)
    segs: list[Segment] = []
    for f0, f1 in zip(cuts_sorted, cuts_sorted[1:]):
        if f1 <= f0:
            continue
        mid = r_in + (f0 + f1) / 2 / fpsf
        a = project.clip_at(0, mid)
        b = project.clip_at(1, mid)
        if a is None and b is None and project.export.gaps == "skip":
            continue
        if segs and segs[-1].f1 == f0 and segs[-1].a is a and segs[-1].b is b:
            segs[-1].f1 = f1
            continue
        segs.append(Segment(f0, f1, r_in + f0 / fpsf, fps, a, b))
    return segs


def output_duration(segments: list[Segment]) -> float:
    return sum(s.duration for s in segments)
