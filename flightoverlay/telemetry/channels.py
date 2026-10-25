"""Friendly data channels and where to find them in .bin and .tlog files."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .loader import RawLog
from .series import Series, Telemetry

RAD = 180.0 / math.pi


@dataclass(frozen=True)
class Src:
    msg: str
    field: str
    scale: float = 1.0
    offset: float = 0.0
    valid: tuple[float, float] | None = None     # allowed raw value range
    where: tuple[str, str, float] | None = None  # e.g. ("Status", ">=", 3)


@dataclass(frozen=True)
class ChannelDef:
    key: str
    label: str        # long name shown in the UI
    short: str        # short label drawn on the video
    quantity: str     # see units.py
    kind: str         # 'linear', 'hold', 'angle360', 'angle180'
    group: str
    bin: tuple[Src, ...] = ()
    tlog: tuple[Src, ...] = ()
    derived: bool = False


GPS_OK_BIN = ("Status", ">=", 3)
GPS_OK_TLOG = ("fix_type", ">=", 3)

CHANNELS: list[ChannelDef] = [
    # Flight
    ChannelDef("airspeed", "Airspeed", "AS", "speed", "linear", "Flight",
               bin=(Src("ARSP", "Airspeed"), Src("CTUN", "As")),
               tlog=(Src("VFR_HUD", "airspeed"),)),
    ChannelDef("groundspeed", "Ground speed", "GS", "speed", "linear", "Flight",
               bin=(Src("GPS", "Spd", where=GPS_OK_BIN),),
               tlog=(Src("VFR_HUD", "groundspeed"), Src("GPS_RAW_INT", "vel", 0.01, where=GPS_OK_TLOG))),
    ChannelDef("alt_rel", "Altitude (above home)", "ALT", "altitude", "linear", "Flight",
               bin=(Src("POS", "RelHomeAlt"),),
               tlog=(Src("GLOBAL_POSITION_INT", "relative_alt", 0.001),)),
    ChannelDef("alt_msl", "Altitude (sea level)", "MSL", "altitude", "linear", "Flight",
               bin=(Src("POS", "Alt"), Src("GPS", "Alt", where=GPS_OK_BIN)),
               tlog=(Src("GLOBAL_POSITION_INT", "alt", 0.001, valid=(-1e9, 1e9)),
                     Src("GPS_RAW_INT", "alt", 0.001, where=GPS_OK_TLOG))),
    ChannelDef("vspeed", "Vertical speed", "VS", "vspeed", "linear", "Flight",
               bin=(Src("TECS", "dh"), Src("GPS", "VZ", -1.0, where=GPS_OK_BIN), Src("BARO", "CRt")),
               tlog=(Src("VFR_HUD", "climb"), Src("GLOBAL_POSITION_INT", "vz", -0.01))),
    ChannelDef("throttle", "Throttle", "THR", "percent", "linear", "Flight",
               bin=(Src("CTUN", "ThO"), Src("CTUN", "ThrOut", 100.0)),
               tlog=(Src("VFR_HUD", "throttle"),)),
    ChannelDef("mode", "Flight mode", "MODE", "mode", "hold", "Flight",
               bin=(Src("MODE", "ModeNum"), Src("MODE", "Mode")),
               tlog=(Src("HEARTBEAT", "custom_mode"),)),
    # Attitude
    ChannelDef("roll", "Roll", "ROLL", "angle", "angle180", "Attitude",
               bin=(Src("ATT", "Roll"), Src("AHR2", "Roll")),
               tlog=(Src("ATTITUDE", "roll", RAD),)),
    ChannelDef("pitch", "Pitch", "PITCH", "angle", "angle180", "Attitude",
               bin=(Src("ATT", "Pitch"), Src("AHR2", "Pitch")),
               tlog=(Src("ATTITUDE", "pitch", RAD),)),
    ChannelDef("yaw", "Heading", "HDG", "heading", "angle360", "Attitude",
               bin=(Src("ATT", "Yaw"), Src("AHR2", "Yaw")),
               tlog=(Src("ATTITUDE", "yaw", RAD), Src("VFR_HUD", "heading"))),
    # Navigation
    ChannelDef("lat", "Latitude", "LAT", "coord", "linear", "Navigation",
               bin=(Src("GPS", "Lat", where=GPS_OK_BIN),),
               tlog=(Src("GLOBAL_POSITION_INT", "lat", 1e-7, valid=(-9e8, 9e8), where=("lat", "!=", 0)),
                     Src("GPS_RAW_INT", "lat", 1e-7, where=GPS_OK_TLOG))),
    ChannelDef("lon", "Longitude", "LON", "coord", "linear", "Navigation",
               bin=(Src("GPS", "Lng", where=GPS_OK_BIN),),
               tlog=(Src("GLOBAL_POSITION_INT", "lon", 1e-7, valid=(-1.8e9, 1.8e9), where=("lat", "!=", 0)),
                     Src("GPS_RAW_INT", "lon", 1e-7, where=GPS_OK_TLOG))),
    ChannelDef("course", "Course over ground", "CRS", "heading", "angle360", "Navigation",
               bin=(Src("GPS", "GCrs", where=GPS_OK_BIN),),
               tlog=(Src("GPS_RAW_INT", "cog", 0.01, valid=(0, 36000)),)),
    ChannelDef("gps_sats", "GPS satellites", "SAT", "count", "hold", "Navigation",
               bin=(Src("GPS", "NSats"),),
               tlog=(Src("GPS_RAW_INT", "satellites_visible", valid=(0, 254)),)),
    ChannelDef("gps_hdop", "GPS HDOP", "HDOP", "ratio", "linear", "Navigation",
               bin=(Src("GPS", "HDop"),),
               tlog=(Src("GPS_RAW_INT", "eph", 0.01, valid=(0, 65534)),)),
    ChannelDef("gps_fix", "GPS fix type", "FIX", "count", "hold", "Navigation",
               bin=(Src("GPS", "Status"),),
               tlog=(Src("GPS_RAW_INT", "fix_type"),)),
    ChannelDef("distance_home", "Distance to home", "HOME", "distance", "linear", "Navigation", derived=True),
    ChannelDef("bearing_home", "Bearing to home", "BRG", "heading", "angle360", "Navigation", derived=True),
    ChannelDef("distance_flown", "Distance flown", "TRIP", "distance", "linear", "Navigation", derived=True),
    # Power
    ChannelDef("batt_voltage", "Battery voltage", "BAT", "voltage", "linear", "Power",
               bin=(Src("BAT", "Volt"), Src("CURR", "Volt")),
               tlog=(Src("SYS_STATUS", "voltage_battery", 0.001, valid=(1, 65534)),)),
    ChannelDef("batt_current", "Current", "CUR", "current", "linear", "Power",
               bin=(Src("BAT", "Curr"), Src("CURR", "Curr")),
               tlog=(Src("SYS_STATUS", "current_battery", 0.01, valid=(0, 1e7)),
                     Src("BATTERY_STATUS", "current_battery", 0.01, valid=(0, 1e7)))),
    ChannelDef("batt_used", "Capacity used", "USED", "capacity", "linear", "Power",
               bin=(Src("BAT", "CurrTot"), Src("CURR", "CurrTot")),
               tlog=(Src("BATTERY_STATUS", "current_consumed", valid=(0, 1e9)),)),
    ChannelDef("batt_remaining", "Battery remaining", "BAT%", "percent", "linear", "Power",
               bin=(Src("BAT", "RemPct", valid=(0, 100)),),
               tlog=(Src("SYS_STATUS", "battery_remaining", valid=(0, 100)),
                     Src("BATTERY_STATUS", "battery_remaining", valid=(0, 100)))),
    ChannelDef("batt_power", "Power", "PWR", "power", "linear", "Power", derived=True),
    # Environment
    ChannelDef("wind_speed", "Wind speed", "WIND", "speed", "linear", "Environment",
               tlog=(Src("WIND", "speed"),), derived=True),
    ChannelDef("wind_dir", "Wind direction (from)", "WDIR", "heading", "angle360", "Environment",
               tlog=(Src("WIND", "direction"),), derived=True),
    # Link
    ChannelDef("rssi", "Receiver RSSI", "RSSI", "percent", "linear", "Link",
               bin=(Src("RSSI", "RXRSSI", 100.0, valid=(0, 1.0001)),),
               tlog=(Src("RC_CHANNELS", "rssi", 100.0 / 254.0, valid=(0, 254)),
                     Src("RC_CHANNELS_RAW", "rssi", 100.0 / 254.0, valid=(0, 254)))),
    ChannelDef("link_quality", "Link quality", "LQ", "percent", "linear", "Link",
               bin=(Src("RSSI", "RXLQ", valid=(0, 100)),)),
    # Time
    ChannelDef("armed", "Armed", "ARM", "bool", "hold", "Time", derived=True),
    ChannelDef("flight_time", "Flight timer (since arming)", "TIME", "duration", "linear", "Time", derived=True),
]

CHANNEL_BY_KEY: dict[str, ChannelDef] = {c.key: c for c in CHANNELS}


def _apply_where(d: dict[str, np.ndarray], where) -> np.ndarray:
    fld, op, val = where
    if fld not in d:
        return np.ones(d["_t"].shape, dtype=bool)
    x = d[fld]
    return {">=": x >= val, ">": x > val, "<=": x <= val, "<": x < val, "==": x == val, "!=": x != val}[op]


def series_from(raw: RawLog, src: Src, kind: str) -> Series | None:
    d = raw.msgs.get(src.msg)
    if d is None or src.field not in d:
        return None
    t = d["_t"]
    v = d[src.field]
    keep = np.isfinite(v)
    if src.valid is not None:
        keep &= (v >= src.valid[0]) & (v <= src.valid[1])
    if src.where is not None:
        keep &= _apply_where(d, src.where)
    if not np.any(keep):
        return None
    vals = v[keep] * src.scale + src.offset
    return Series(t[keep], vals, kind)


def build_telemetry(raw: RawLog) -> Telemetry:
    from pymavlink import mavutil
    from . import derived, events

    tel = Telemetry(source_path=raw.path, source_type=raw.source_type, vehicle=raw.vehicle,
                    duration=raw.duration, utc_offset=raw.utc_offset, messages=list(raw.texts))
    for cdef in CHANNELS:
        srcs = cdef.bin if raw.source_type == "bin" else cdef.tlog
        for src in srcs:
            s = series_from(raw, src, cdef.kind)
            if s is not None and len(s) > 0:
                tel.channels[cdef.key] = s
                tel.sources[cdef.key] = f"{src.msg}.{src.field}"
                break

    mapping = mavutil.mode_mapping_bynumber(raw.mav_type) or mavutil.mode_mapping_apm
    tel.mode_names = {int(k): str(v) for k, v in mapping.items()}

    derived.apply(tel, raw)
    tel.events = events.detect(tel)
    return tel
