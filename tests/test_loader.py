import math

import numpy as np

from flightoverlay import demo
from flightoverlay.telemetry.loader import gps_to_unix


def test_gps_time_conversion():
    # 2026-10-24 14:00:00 UTC
    utc = demo.UTC_BOOT
    gps = utc - 315964800 + 18
    week = int(gps // 604800)
    ms = (gps - week * 604800) * 1000
    assert abs(float(gps_to_unix(week, ms)) - utc) < 1e-6


def _check_against_sim(tel, sim, shift):
    """shift = boot time of log t=0 (bin starts 1 s after boot via TimeUS offset; tlog starts at 5 s)."""
    for t in (80.0, 150.0, 222.2, 310.0):
        st = t + shift
        assert abs(tel.value("roll", t) - np.interp(st, sim.t, sim.roll)) < 1.0
        assert abs(tel.value("pitch", t) - np.interp(st, sim.t, sim.pitch)) < 1.0
        assert abs(tel.value("alt_rel", t) - np.interp(st, sim.t, sim.alt)) < 1.5
        assert abs(tel.value("airspeed", t) - np.interp(st, sim.t, sim.airspeed)) < 0.5
        assert abs(tel.value("throttle", t) - np.interp(st, sim.t, sim.throttle)) < 2.0


def test_bin_channels(bin_tel, sim):
    tel = bin_tel
    assert tel.source_type == "bin"
    assert tel.vehicle == "plane"
    _check_against_sim(tel, sim, 0.0)
    assert tel.mode_name(100.0) == "CRUISE"
    assert tel.mode_name(310.0) == "RTL"
    # GPS instance 1 has a position offset; only instance 0 must be used
    assert abs(tel.value("lat", 150.0) - np.interp(150.0, sim.t, sim.lat)) < 2e-6
    # UTC: log t=0 is 1 s after boot in the demo .bin
    assert abs(tel.utc_offset - (demo.UTC_BOOT)) < 0.3
    assert tel.home is not None and abs(tel.home[0] - demo.HOME[0]) < 1e-6


def test_tlog_channels(tlog_tel, sim):
    tel = tlog_tel
    assert tel.source_type == "tlog"
    _check_against_sim(tel, sim, 5.0)          # telemetry starts 5 s after boot
    assert abs(tel.utc_offset - (demo.UTC_BOOT + 5.0)) < 0.01
    assert tel.mode_name(100.0) == "CRUISE"
    assert tel.has("wind_speed") and abs(tel.value("wind_speed", 100) - 3.0) < 0.01


def test_events(bin_tel):
    kinds = {e.kind: e.t for e in bin_tel.events}
    assert abs(kinds["arm"] - demo.ARM_T) < 0.1
    assert abs(kinds["disarm"] - demo.DISARM_T) < 0.1
    assert abs(kinds["throttle"] - demo.THR_T) < 0.2
    assert demo.THR_T <= kinds["takeoff"] < demo.THR_T + 3
    assert demo.LAND_T < kinds["landing"] < demo.DISARM_T


def test_derived(bin_tel, sim):
    t = 200.0
    d = bin_tel.value("distance_home", t)
    expect = math.hypot(np.interp(t, sim.t, sim.north), np.interp(t, sim.t, sim.east))
    assert abs(d - expect) < 3.0
    assert abs(bin_tel.value("flight_time", 100.0) - (100.0 - demo.ARM_T)) < 0.1
    assert abs(bin_tel.value("flight_time", 410.0) - (demo.DISARM_T - demo.ARM_T)) < 0.1
    assert abs(bin_tel.value("wind_dir", 100.0) - 270.0) < 0.5
