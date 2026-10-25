"""Channels computed from other channels: home, distances, flight timer, wind, etc."""
from __future__ import annotations

import math

import numpy as np

from .loader import RawLog
from .series import Series, Telemetry

EARTH_R = 6371008.8


def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_R * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def bearing(lat1, lon1, lat2, lon2):
    """Initial bearing in degrees from point 1 to point 2."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dl = np.radians(np.asarray(lon2) - np.asarray(lon1))
    y = np.sin(dl) * np.cos(p2)
    x = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl)
    return (np.degrees(np.arctan2(y, x)) + 360.0) % 360.0


def first_arm_time(tel: Telemetry) -> float | None:
    s = tel.channels.get("armed")
    if s is None or len(s) == 0:
        return None
    idx = np.nonzero(s.v > 0.5)[0]
    return float(s.t[idx[0]]) if idx.size else None


def _armed(tel: Telemetry, raw: RawLog) -> None:
    t = v = None
    src = ""
    if raw.source_type == "bin":
        arm = raw.msgs.get("ARM")
        ev = raw.msgs.get("EV")
        if arm is not None and "ArmState" in arm and arm["_t"].size:
            t, v, src = arm["_t"], (arm["ArmState"] > 0).astype(float), "ARM.ArmState"
        elif ev is not None and "Id" in ev:
            sel = (ev["Id"] == 10) | (ev["Id"] == 11)
            if np.any(sel):
                t, v, src = ev["_t"][sel], (ev["Id"][sel] == 10).astype(float), "EV.Id"
    else:
        hb = raw.msgs.get("HEARTBEAT")
        if hb is not None and "base_mode" in hb and hb["_t"].size:
            t = hb["_t"]
            v = ((hb["base_mode"].astype(np.int64) & 128) > 0).astype(float)
            src = "HEARTBEAT.base_mode"
    if t is None or t.size == 0:
        return
    if v[0] > 0.5 and t[0] > 0:
        t = np.concatenate([[0.0], t])
        v = np.concatenate([[0.0], v])
    tel.channels["armed"] = Series(t, v, "hold")
    tel.sources["armed"] = src


def _home(tel: Telemetry, raw: RawLog) -> None:
    arm_t = first_arm_time(tel)
    cand = []
    if raw.source_type == "bin":
        o = raw.msgs.get("ORGN")
        if o is not None and "Lat" in o:
            sel = o["Type"] == 1 if "Type" in o else np.ones(o["_t"].shape, bool)
            sel &= (o["Lat"] != 0) | (o["Lng"] != 0)
            for i in np.nonzero(sel)[0]:
                cand.append((float(o["_t"][i]), float(o["Lat"][i]), float(o["Lng"][i]), float(o["Alt"][i])))
    else:
        h = raw.msgs.get("HOME_POSITION")
        if h is not None and "latitude" in h:
            for i in range(h["_t"].size):
                if h["latitude"][i] != 0:
                    cand.append((float(h["_t"][i]), h["latitude"][i] * 1e-7, h["longitude"][i] * 1e-7,
                                 h["altitude"][i] * 1e-3))
    if cand:
        if arm_t is not None:
            before = [c for c in cand if c[0] <= arm_t + 10.0]
            pick = before[-1] if before else cand[0]
        else:
            pick = cand[0]
        tel.home = (float(pick[1]), float(pick[2]), float(pick[3]))
        return
    if tel.has("lat") and tel.has("lon"):
        lat_s, lon_s = tel.channels["lat"], tel.channels["lon"]
        t = arm_t if arm_t is not None and arm_t >= lat_s.start else lat_s.start
        alt = tel.value("alt_msl", t)
        tel.home = (float(lat_s.at(t)), float(lon_s.at(t)), float(alt) if not math.isnan(alt) else 0.0)


def _alt_fallback(tel: Telemetry, raw: RawLog) -> None:
    if tel.has("alt_rel"):
        return
    if tel.has("alt_msl") and tel.home is not None:
        s = tel.channels["alt_msl"]
        tel.channels["alt_rel"] = Series(s.t, s.v - tel.home[2], "linear")
        tel.sources["alt_rel"] = tel.sources.get("alt_msl", "") + " minus home"
        return
    baro = raw.get("BARO", "Alt")
    if baro is not None:
        tel.channels["alt_rel"] = Series(baro[0], baro[1], "linear")
        tel.sources["alt_rel"] = "BARO.Alt"


def _nav(tel: Telemetry) -> None:
    if not (tel.has("lat") and tel.has("lon")):
        return
    lat_s, lon_s = tel.channels["lat"], tel.channels["lon"]
    t = lat_s.t
    lat = lat_s.v
    lon = lon_s.v if lon_s.t.size == t.size else lon_s.at_many(t)
    if tel.home is not None:
        hlat, hlon = tel.home[0], tel.home[1]
        tel.channels["distance_home"] = Series(t, haversine(hlat, hlon, lat, lon), "linear")
        tel.channels["bearing_home"] = Series(t, bearing(lat, lon, hlat, hlon), "angle360")
        tel.sources["distance_home"] = tel.sources["bearing_home"] = "GPS + home"
    if t.size > 1:
        step = haversine(lat[:-1], lon[:-1], lat[1:], lon[1:])
        dt = np.maximum(np.diff(t), 1e-3)
        moving = np.ones(step.shape, dtype=bool)
        if tel.has("groundspeed"):
            gs = tel.channels["groundspeed"].at_many((t[:-1] + t[1:]) / 2)
            moving = np.nan_to_num(gs, nan=0.0) > 1.0
        good = moving & (step / dt < 150.0)  # drop GPS glitches
        flown = np.concatenate([[0.0], np.cumsum(np.where(good, step, 0.0))])
        tel.channels["distance_flown"] = Series(t, flown, "linear")
        tel.sources["distance_flown"] = "GPS track"


def _flight_time(tel: Telemetry) -> None:
    s = tel.channels.get("armed")
    if s is None or len(s) == 0:
        return
    pts_t, pts_v = [0.0], [0.0]
    acc = 0.0
    armed_since = None
    for t, v in zip(s.t, s.v):
        if v > 0.5 and armed_since is None:
            armed_since = float(t)
            pts_t.append(armed_since)
            pts_v.append(acc)
        elif v <= 0.5 and armed_since is not None:
            acc += float(t) - armed_since
            pts_t.append(float(t))
            pts_v.append(acc)
            armed_since = None
    end = max(tel.duration, pts_t[-1])
    if armed_since is not None:
        acc += end - armed_since
    pts_t.append(end + 1e-6)
    pts_v.append(acc)
    tel.channels["flight_time"] = Series(pts_t, pts_v, "linear")
    tel.sources["flight_time"] = "armed time"


def _vspeed_fallback(tel: Telemetry) -> None:
    if tel.has("vspeed") or not tel.has("alt_rel"):
        return
    s = tel.channels["alt_rel"]
    if len(s) < 4:
        return
    tt = np.arange(s.start, s.end, 0.2)
    if tt.size < 4:
        return
    alt = s.at_many(tt)
    vs = np.gradient(alt, tt)
    k = np.ones(5) / 5
    vs = np.convolve(vs, k, mode="same")
    tel.channels["vspeed"] = Series(tt, vs, "linear")
    tel.sources["vspeed"] = "altitude rate"


def _wind(tel: Telemetry, raw: RawLog) -> None:
    if tel.has("wind_speed"):
        return
    for m in ("XKF2", "NKF2"):
        d = raw.msgs.get(m)
        if d is not None and "VWN" in d and "VWE" in d and d["_t"].size:
            vn, ve = d["VWN"], d["VWE"]
            tel.channels["wind_speed"] = Series(d["_t"], np.hypot(vn, ve), "linear")
            tel.channels["wind_dir"] = Series(d["_t"], (np.degrees(np.arctan2(-ve, -vn)) + 360) % 360, "angle360")
            tel.sources["wind_speed"] = tel.sources["wind_dir"] = f"{m}.VWN/VWE"
            return


def _power(tel: Telemetry) -> None:
    if tel.has("batt_voltage") and tel.has("batt_current"):
        c = tel.channels["batt_current"]
        v = tel.channels["batt_voltage"].at_many(c.t)
        tel.channels["batt_power"] = Series(c.t, v * c.v, "linear")
        tel.sources["batt_power"] = "voltage x current"


def apply(tel: Telemetry, raw: RawLog) -> None:
    _armed(tel, raw)
    _home(tel, raw)
    _alt_fallback(tel, raw)
    _nav(tel)
    _flight_time(tel)
    _vspeed_fallback(tel)
    _wind(tel, raw)
    _power(tel)
