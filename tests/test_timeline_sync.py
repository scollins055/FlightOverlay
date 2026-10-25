from fractions import Fraction

from flightoverlay.core.media import MediaInfo, parse_timestamp, snap_fps
from flightoverlay.core.project import Clip, Project
from flightoverlay.core.sync import align, auto_place, offset_from_timestamp, suggest_tz_shift
from flightoverlay.core.timeline import plan_segments
from flightoverlay.telemetry.series import Telemetry


def clip(start, dur, track=0, name="c", created=None, fps=30):
    info = MediaInfo(path=f"{name}.mp4", duration=dur, width=1920, height=1080, fps_num=fps, fps_den=1,
                     creation_utc=created, creation_source="creation_time" if created else "")
    return Clip(path=f"{name}.mp4", info=info, track=track, offset=start)


def test_segments_split_at_overlaps():
    p = Project()
    p.clips = [clip(10, 20, 0, "a"), clip(20, 20, 1, "b")]
    segs = plan_segments(p, Fraction(30))
    assert [(s.a.name if s.a else None, s.b.name if s.b else None) for s in segs] == [
        ("a.mp4", None), ("a.mp4", "b.mp4"), (None, "b.mp4")]
    assert sum(s.frames for s in segs) == 30 * 30
    assert segs[0].start == 10 and segs[1].f0 == 300


def test_gaps_skipped_or_black():
    p = Project()
    p.clips = [clip(0, 5, 0, "a"), clip(10, 5, 0, "b")]
    assert sum(s.frames for s in plan_segments(p, Fraction(30))) == 300
    p.export.gaps = "black"
    segs = plan_segments(p, Fraction(30))
    assert sum(s.frames for s in segs) == 450
    assert segs[1].a is None and segs[1].b is None


def test_range_limits_output():
    p = Project()
    p.clips = [clip(0, 60, 0, "a")]
    p.range_in, p.range_out = 10.0, 12.5
    segs = plan_segments(p, Fraction(30000, 1001))
    assert len(segs) == 1
    assert segs[0].frames == round(2.5 * 30000 / 1001)


def test_frame_rounding_is_consistent():
    p = Project()
    p.clips = [clip(0.0, 10.013, 0, "a"), clip(10.013, 5, 0, "b")]
    segs = plan_segments(p, Fraction(30))
    assert segs[0].f1 == segs[1].f0  # no gap or overlap between back-to-back clips


def test_timestamp_placement_and_fixes():
    tel = Telemetry(source_path="x", source_type="bin", duration=600.0, utc_offset=1_000_000.0)
    c = clip(0, 60, created=1_000_000.0 + 100.0)
    assert offset_from_timestamp(c, tel) == 100.0
    c.stamp_is_end = True
    assert offset_from_timestamp(c, tel) == 40.0
    c.stamp_is_end = False
    # camera clock set to local time UTC-7: timestamp reads 7 h early
    c.info.creation_utc = 1_000_000.0 + 100.0 - 7 * 3600
    assert suggest_tz_shift(c, tel) == 7.0
    p = Project()
    note = auto_place(p, c, tel)
    assert "shifted" in note and abs(c.offset - 100.0) < 1e-6


def test_auto_place_moves_overlapping_clip_to_track_b():
    tel = Telemetry(source_path="x", source_type="bin", duration=600.0, utc_offset=0.0)
    p = Project()
    a = clip(0, 60, created=100.0, name="a")
    auto_place(p, a, tel)
    p.clips.append(a)
    b = clip(0, 60, created=130.0, name="b")
    auto_place(p, b, tel)
    assert a.track == 0 and b.track == 1


def test_align():
    c = clip(50, 30)
    assert align(c, 4.0, 70.0) == 66.0


def test_snap_fps_and_timestamps():
    assert snap_fps(Fraction(2997, 100)) == Fraction(30000, 1001)
    assert snap_fps(Fraction(30)) == Fraction(30)
    assert parse_timestamp("2024-05-03T14:22:10-0700") == parse_timestamp("2024-05-03T21:22:10Z")
    assert parse_timestamp("1904-01-01T00:00:00Z") is None


def test_project_roundtrip(tmp_path):
    p = Project()
    p.clips = [clip(12.5, 30, 1, "a")]
    p.clips[0].trim_in = 2.0
    p.overlay.get("minimap").x = 0.123
    p.export.layout = "side"
    path = tmp_path / "x.fovproj"
    p.save(str(path))
    q = Project.load(str(path))
    assert q.clips[0].offset == 12.5 and q.clips[0].track == 1 and q.clips[0].trim_in == 2.0
    assert q.clips[0].info.duration == 30
    assert q.overlay.get("minimap").x == 0.123
    assert q.export.layout == "side"
