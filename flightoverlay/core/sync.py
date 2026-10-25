"""Placing videos on the log timeline: from file timestamps, and from matched moments."""
from __future__ import annotations

from ..telemetry.series import Telemetry
from .project import Clip, Project

# How far outside the log a timestamp-based placement may land before we distrust it.
_SLACK = 600.0


def video_start_utc(clip: Clip) -> float | None:
    """Best guess of the UTC time of the clip's first frame, with the user's corrections applied."""
    if clip.info is None or clip.info.creation_utc is None:
        return None
    t = clip.info.creation_utc + clip.tz_shift_h * 3600.0
    if clip.stamp_is_end:
        t -= clip.duration
    return t


def offset_from_timestamp(clip: Clip, tel: Telemetry | None) -> float | None:
    """Log time at which the clip starts, according to its timestamp."""
    if tel is None or tel.utc_offset is None:
        return None
    start = video_start_utc(clip)
    if start is None:
        return None
    return start - tel.utc_offset


def lands_in_log(offset: float, clip: Clip, tel: Telemetry) -> bool:
    return offset + clip.duration > -_SLACK and offset < tel.duration + _SLACK


def suggest_tz_shift(clip: Clip, tel: Telemetry) -> float | None:
    """If the timestamp is off by whole hours (camera set to local time), find the shift that fits."""
    if tel.utc_offset is None or clip.info is None or clip.info.creation_utc is None:
        return None
    best = None
    for half_hours in range(-28, 29):
        shift = half_hours / 2.0
        t = clip.info.creation_utc + shift * 3600.0 - (clip.duration if clip.stamp_is_end else 0.0)
        off = t - tel.utc_offset
        if -60 < off and off + clip.duration < tel.duration + 60:
            if best is None or abs(shift) < abs(best):
                best = shift
    return best


def align(clip: Clip, video_time: float, log_time: float) -> float:
    """Offset that makes video_time (seconds into the clip) play at log_time."""
    return log_time - video_time


def free_track(project: Project, clip: Clip, start: float, end: float) -> int | None:
    for track in (clip.track, 1 - clip.track):
        if not project.overlaps(track, start, end, ignore=clip.id):
            return track
    return None


def auto_place(project: Project, clip: Clip, tel: Telemetry | None, fallback_time: float = 0.0) -> str:
    """Place a newly imported clip. Returns a short description of what was done."""
    note = ""
    off = offset_from_timestamp(clip, tel)
    if off is not None and tel is not None and not lands_in_log(off, clip, tel):
        shift = suggest_tz_shift(clip, tel)
        if shift is not None:
            clip.tz_shift_h = shift
            off = offset_from_timestamp(clip, tel)
            note = f"timestamp shifted {shift:+g} h (camera clock in local time?)"
        else:
            off = None
            note = "timestamp doesn't match the log"
    if off is not None:
        clip.offset = off
        clip.auto_placed = True
        note = note or "placed from file timestamp"
    else:
        # After the last clip, or at the fallback time.
        ends = [c.end for c in project.clips if c is not clip]
        clip.offset = max(ends) if ends else fallback_time
        clip.auto_placed = False
        note = note or "no usable timestamp; placed at the end"
    track = free_track(project, clip, clip.start, clip.end)
    if track is None:
        ends = [c.end for c in project.clips if c is not clip and c.track == 0]
        clip.offset = (max(ends) if ends else clip.offset) - clip.trim_in
        clip.track = 0
        note += "; moved to avoid overlapping clips"
    else:
        clip.track = track
    return note
