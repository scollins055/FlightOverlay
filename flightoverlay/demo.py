"""Synthetic ArduPlane flight: writes a .bin log, a .tlog and camera-like test videos.

Used for automated tests and for the "Create demo project" menu item.
"""
from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass

import numpy as np

UTC_BOOT = 1792850400.0          # 2026-10-24 14:00:00 UTC, time of log t=0
HOME = (47.2540, -122.4410, 120.0)
GPS_FIX_T = 15.0
ARM_T, THR_T, CRUISE_T, RTL_T, LAND_T, DISARM_T, END_T = 60.0, 70.0, 95.0, 300.0, 372.0, 392.0, 420.0
MODES = [(0.0, 0), (62.0, 5), (CRUISE_T, 7), (RTL_T, 11), (350.0, 5)]  # MANUAL, FBWA, CRUISE, RTL, FBWA
WIND_N, WIND_E = 0.0, 3.0        # wind blowing toward east (from the west)


@dataclass
class Sim:
    t: np.ndarray
    roll: np.ndarray
    pitch: np.ndarray
    yaw: np.ndarray
    airspeed: np.ndarray
    groundspeed: np.ndarray
    course: np.ndarray
    alt: np.ndarray       # above home
    vz: np.ndarray        # climb rate m/s (up positive)
    north: np.ndarray
    east: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    throttle: np.ndarray
    volt: np.ndarray
    curr: np.ndarray
    used: np.ndarray
    armed: np.ndarray
    mode: np.ndarray


def _mode_at(t: float) -> int:
    m = 0
    for t0, mm in MODES:
        if t >= t0:
            m = mm
    return m


def simulate(rate: float = 25.0) -> Sim:
    n = int(END_T * rate) + 1
    t = np.arange(n) / rate
    dt = 1.0 / rate
    roll = np.zeros(n); pitch = np.zeros(n); yaw = np.zeros(n)
    tas = np.zeros(n); alt = np.zeros(n); vz = np.zeros(n)
    north = np.zeros(n); east = np.zeros(n); thr = np.zeros(n)
    gs = np.zeros(n); crs = np.zeros(n)
    hdg = math.radians(270.0)  # launch into the wind
    a = 0.0; spd = 0.0; pn = 0.0; pe = 0.0
    for i, ti in enumerate(t):
        # throttle and speed
        if ti < THR_T or ti >= LAND_T - 8:
            th = 0.0 if ti < THR_T else max(0.0, 30.0 - (ti - (LAND_T - 8)) * 6)
        elif ti < THR_T + 15:
            th = 100.0
        else:
            th = 55.0 + 10.0 * math.sin(ti / 13.0)
        if THR_T <= ti < LAND_T:
            target = 18.0 + 2.0 * math.sin(ti / 17.0)
        else:
            target = 0.0
        spd += (target - spd) * min(1.0, dt * (0.9 if target > spd else 0.35))
        # bank angle profile: straight legs and turns
        r = 0.0
        if THR_T + 12 < ti < RTL_T:
            phase = (ti - THR_T - 12) % 40.0
            if phase < 12:
                r = 0.0
            elif phase < 22:
                r = 35.0 * math.sin(math.pi * (phase - 12) / 10.0)
            elif phase < 28:
                r = 0.0
            else:
                r = -25.0 * math.sin(math.pi * (phase - 28) / 12.0)
        elif RTL_T <= ti < LAND_T - 20:
            want = math.atan2(-pe, -pn)
            err = (want - hdg + math.pi) % (2 * math.pi) - math.pi
            r = max(-30.0, min(30.0, math.degrees(err) * 1.2))
        # altitude
        if ti < THR_T + 2:
            target_alt = 0.0
        elif ti < RTL_T:
            target_alt = 80.0 + 15.0 * math.sin(ti / 23.0)
        elif ti < LAND_T - 20:
            target_alt = 60.0
        else:
            target_alt = 0.0
        if ti < LAND_T - 20:
            climb = max(-4.0, min(5.0, (target_alt - a) * 0.4))
        else:
            climb = -max(0.0, a) / max(1.0, LAND_T - ti) if ti < LAND_T else -a * 2.0
        if spd < 8.0 and a <= 0.5:
            climb = max(0.0, climb) if spd > 6 else 0.0
        a = max(0.0, a + climb * dt)
        if spd > 2.0:
            hdg += 9.81 * math.tan(math.radians(r)) / max(spd, 5.0) * dt
        airborne = a > 1.0 and spd > 6.0
        vn = spd * math.cos(hdg) + (WIND_N if airborne else 0.0)
        ve = spd * math.sin(hdg) + (WIND_E if airborne else 0.0)
        pn += vn * dt
        pe += ve * dt
        roll[i] = r + 1.5 * math.sin(ti * 2.1) * airborne
        pitch[i] = math.degrees(math.atan2(climb, max(spd, 1.0))) + 2.0 + 0.8 * math.sin(ti * 1.3) * airborne
        yaw[i] = math.degrees(hdg) % 360.0
        tas[i] = spd; alt[i] = a; vz[i] = climb; north[i] = pn; east[i] = pe; thr[i] = th
        gs[i] = math.hypot(vn, ve); crs[i] = (math.degrees(math.atan2(ve, vn)) + 360) % 360
    lat = HOME[0] + np.degrees(north / 6371008.8)
    lon = HOME[1] + np.degrees(east / (6371008.8 * math.cos(math.radians(HOME[0]))))
    curr = 0.8 + 28.0 * (thr / 100.0) ** 1.5
    used = np.cumsum(curr) * dt / 3.6  # mAh
    volt = 16.7 - 1.7 * (used / 2200.0) - 0.025 * curr
    armed = ((t >= ARM_T) & (t < DISARM_T)).astype(float)
    mode = np.array([_mode_at(x) for x in t], dtype=float)
    return Sim(t, roll, pitch, yaw, tas, gs, crs, alt, vz, north, east, lat, lon, thr, volt, curr, used,
               armed, mode)


def _rssi(sim: Sim, i: int) -> float:
    return max(0.25, 0.95 - 0.0002 * math.hypot(sim.north[i], sim.east[i]))


# ------------------------------------------------------------------ DataFlash writer

_STRUCT = {"b": "b", "B": "B", "h": "h", "H": "H", "i": "i", "I": "I", "f": "f", "d": "d",
           "n": "4s", "N": "16s", "Z": "64s", "c": "h", "C": "H", "e": "i", "E": "I", "L": "i",
           "M": "b", "q": "q", "Q": "Q"}


class DFWriter:
    def __init__(self, f):
        self.f = f
        self.types: dict[str, tuple[int, struct.Struct]] = {}
        self.next_id = 1
        self._write_fmt(128, 89, "FMT", "BBnNZ", "Type,Length,Name,Format,Columns")

    def _write_fmt(self, mid, length, name, fmt, cols):
        self.f.write(b"\xa3\x95\x80" + struct.pack("<BB4s16s64s", mid, length, name.encode(),
                                                  fmt.encode(), cols.encode()))

    def define(self, name: str, fmt: str, cols: str) -> None:
        st = struct.Struct("<" + "".join(_STRUCT[c] for c in fmt))
        mid = self.next_id
        self.next_id += 1
        self._write_fmt(mid, st.size + 3, name, fmt, cols)
        self.types[name] = (mid, st)

    def write(self, name: str, *vals) -> None:
        mid, st = self.types[name]
        self.f.write(bytes([0xA3, 0x95, mid]) + st.pack(*vals))


def write_bin(path: str, sim: Sim | None = None) -> None:
    sim = sim or simulate()
    t = sim.t
    with open(path, "wb") as f:
        w = DFWriter(f)
        w.define("MSG", "QZ", "TimeUS,Message")
        w.define("ATT", "QffffffB", "TimeUS,DesRoll,Roll,DesPitch,Pitch,DesYaw,Yaw,AEKF")
        w.define("GPS", "QBBIHBcLLeffffB", "TimeUS,I,Status,GMS,GWk,NSats,HDop,Lat,Lng,Alt,Spd,GCrs,VZ,Yaw,U")
        w.define("POS", "QLLfff", "TimeUS,Lat,Lng,Alt,RelHomeAlt,RelOriginAlt")
        w.define("BARO", "QBffcf", "TimeUS,I,Alt,Press,Temp,CRt")
        w.define("CTUN", "Qffffff", "TimeUS,NavRoll,Roll,NavPitch,Pitch,ThO,As")
        w.define("ARSP", "QBffc", "TimeUS,I,Airspeed,DiffPress,Temp")
        w.define("BAT", "QBfffffcfB", "TimeUS,Inst,Volt,VoltR,Curr,CurrTot,EnrgTot,Temp,Res,RemPct")
        w.define("MODE", "QMBB", "TimeUS,Mode,ModeNum,Rsn")
        w.define("ARM", "QBIBB", "TimeUS,ArmState,ArmChecks,Forced,Method")
        w.define("EV", "QB", "TimeUS,Id")
        w.define("XKF2", "QBff", "TimeUS,C,VWN,VWE")
        w.define("RSSI", "Qff", "TimeUS,RXRSSI,RXLQ")
        w.define("TECS", "Qfff", "TimeUS,h,dh,hdem")
        w.define("ORGN", "QBLLe", "TimeUS,Type,Lat,Lng,Alt")

        def us(x):
            return int(round(x * 1e6)) + 1_000_000  # log starts 1 s after boot

        w.write("MSG", us(0.0), b"ArduPlane V4.5.7 (demo)")
        mode_prev = None
        armed_prev = 0.0
        for i, ti in enumerate(t):
            k = i % 25
            T = us(ti)
            w.write("ATT", T, sim.roll[i], sim.roll[i], sim.pitch[i], sim.pitch[i], sim.yaw[i], sim.yaw[i], 3)
            if sim.mode[i] != mode_prev:
                w.write("MODE", T, int(sim.mode[i]), int(sim.mode[i]), 1)
                mode_prev = sim.mode[i]
            if sim.armed[i] != armed_prev:
                w.write("ARM", T, int(sim.armed[i]), 0, 0, 1)
                w.write("EV", T, 10 if sim.armed[i] else 11)
                if sim.armed[i]:
                    w.write("ORGN", T, 1, int(round(HOME[0] * 1e7)), int(round(HOME[1] * 1e7)), int(HOME[2] * 100))
                    w.write("MSG", T, b"Throttle armed")
                armed_prev = sim.armed[i]
            if k % 5 == 0:  # 5 Hz
                fix = ti >= GPS_FIX_T
                utc = UTC_BOOT + ti + 18.0
                gweek = int((utc - 315964800) // 604800)
                gms = int(round(((utc - 315964800) - gweek * 604800) * 1000))
                for inst in (0, 1):
                    jitter = 0.0 if inst == 0 else 0.00002
                    w.write("GPS", T, inst, 3 if fix else 1, gms if fix else 0, gweek if fix else 0,
                            14 if fix else 3, 80 if fix else 990,
                            int(round((sim.lat[i] + jitter) * 1e7)) if fix else 0,
                            int(round(sim.lon[i] * 1e7)) if fix else 0,
                            int(round((HOME[2] + sim.alt[i]) * 100)) if fix else 0,
                            sim.groundspeed[i], sim.course[i], -sim.vz[i], 0.0, 1)
            if k % 2 == 0:  # ~12 Hz
                w.write("POS", T, int(round(sim.lat[i] * 1e7)), int(round(sim.lon[i] * 1e7)),
                        HOME[2] + sim.alt[i], sim.alt[i], sim.alt[i])
                w.write("CTUN", T, sim.roll[i], sim.roll[i], sim.pitch[i], sim.pitch[i], sim.throttle[i],
                        sim.airspeed[i])
                w.write("ARSP", T, 0, sim.airspeed[i], 0.5 * 1.2 * sim.airspeed[i] ** 2, 2000)
                w.write("TECS", T, sim.alt[i], sim.vz[i], sim.alt[i])
                w.write("BARO", T, 0, sim.alt[i] + 0.3, 101325.0 - sim.alt[i] * 12, 2100, sim.vz[i])
            if k % 5 == 0:
                rem = int(max(0, 100 - sim.used[i] / 22.0))
                w.write("BAT", T, 0, sim.volt[i], sim.volt[i] + 0.1, sim.curr[i], sim.used[i],
                        sim.used[i] * 0.015, 2500, 0.012, rem)
                w.write("BAT", T, 1, 5.1, 5.1, 0.4, 0.0, 0.0, 2500, 0.0, 0)
                w.write("XKF2", T, 0, WIND_N, WIND_E)
                w.write("RSSI", T, _rssi(sim, i), 100.0)


def write_tlog(path: str, sim: Sim | None = None) -> None:
    from pymavlink.dialects.v20 import ardupilotmega as mavlink

    sim = sim or simulate()

    class _Buf:
        def __init__(self):
            self.data = b""

        def write(self, b):
            self.data += b

    veh = mavlink.MAVLink(_Buf(), srcSystem=1, srcComponent=1)
    gcs = mavlink.MAVLink(_Buf(), srcSystem=255, srcComponent=190)
    start = 5.0
    with open(path, "wb") as f:
        def out(ts, m, link=veh):
            buf = m.pack(link)
            f.write(struct.pack(">Q", int(round((UTC_BOOT + ts) * 1e6))) + buf)

        home_sent = False
        for i, ti in enumerate(sim.t):
            if ti < start:
                continue
            k = i % 25
            armed = sim.armed[i] > 0.5
            if k == 0:
                base = 1 | (128 if armed else 0)
                out(ti, mavlink.MAVLink_heartbeat_message(1, 3, base, int(sim.mode[i]), 4, 3))
                out(ti + 0.001, mavlink.MAVLink_heartbeat_message(6, 8, 0, 0, 0, 3), gcs)
                out(ti, mavlink.MAVLink_sys_status_message(0, 0, 0, 500, int(sim.volt[i] * 1000),
                                                           int(sim.curr[i] * 100),
                                                           int(max(0, 100 - sim.used[i] / 22.0)), 0, 0, 0, 0, 0, 0))
                out(ti, mavlink.MAVLink_battery_status_message(0, 0, 0, 2500, [int(sim.volt[i] * 1000)] + [65535] * 9,
                                                               int(sim.curr[i] * 100), int(sim.used[i]), -1,
                                                               int(max(0, 100 - sim.used[i] / 22.0))))
                wspd = math.hypot(WIND_N, WIND_E)
                out(ti, mavlink.MAVLink_wind_message((math.degrees(math.atan2(-WIND_E, -WIND_N)) + 360) % 360, wspd, 0))
                out(ti, mavlink.MAVLink_rc_channels_message(int(ti * 1000), 8, *([1500] * 18),
                                                            int(254 * _rssi(sim, i))))
                if armed and not home_sent:
                    out(ti, mavlink.MAVLink_home_position_message(int(HOME[0] * 1e7), int(HOME[1] * 1e7),
                                                                  int(HOME[2] * 1000), 0, 0, 0, [1, 0, 0, 0], 0, 0, 0))
                    home_sent = True
            if k % 3 == 0:  # ~8 Hz
                out(ti, mavlink.MAVLink_attitude_message(int(ti * 1000), math.radians(sim.roll[i]),
                                                         math.radians(sim.pitch[i]),
                                                         math.radians(((sim.yaw[i] + 180) % 360) - 180), 0, 0, 0))
            if k % 6 == 0:  # ~4 Hz
                out(ti, mavlink.MAVLink_vfr_hud_message(sim.airspeed[i], sim.groundspeed[i], int(sim.yaw[i]),
                                                        int(sim.throttle[i]), HOME[2] + sim.alt[i], sim.vz[i]))
                out(ti, mavlink.MAVLink_global_position_int_message(
                    int(ti * 1000), int(round(sim.lat[i] * 1e7)), int(round(sim.lon[i] * 1e7)),
                    int((HOME[2] + sim.alt[i]) * 1000), int(sim.alt[i] * 1000), 0, 0, int(-sim.vz[i] * 100),
                    int(sim.yaw[i] * 100)))
            if k % 12 == 0:
                fix = ti >= GPS_FIX_T
                out(ti, mavlink.MAVLink_gps_raw_int_message(
                    int(ti * 1e6), 3 if fix else 1, int(round(sim.lat[i] * 1e7)) if fix else 0,
                    int(round(sim.lon[i] * 1e7)) if fix else 0, int((HOME[2] + sim.alt[i]) * 1000),
                    80, 65535, int(sim.groundspeed[i] * 100), int(sim.course[i] * 100), 14 if fix else 3))


# ------------------------------------------------------------------ synthetic camera video

def write_camera_video(path: str, sim: Sim, log_start: float, duration: float, size=(1280, 720), fps=30,
                       creation_utc: float | None = None, label: str = "ONBOARD CAM", progress=None) -> None:
    """Draw a simple 'camera view' whose horizon follows the simulated attitude, and encode it."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPolygonF

    from .core import ffmpeg_tools

    W, H = size
    n = int(round(duration * fps))
    args = [ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{W}x{H}", "-framerate", str(fps), "-i", "pipe:0",
            "-f", "lavfi", "-t", f"{duration:.3f}", "-i", "sine=frequency=330:sample_rate=48000",
            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-shortest"]
    if creation_utc is not None:
        from datetime import datetime, timezone
        ct = datetime.fromtimestamp(creation_utc, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        args += ["-metadata", f"creation_time={ct}"]
    args.append(path)
    proc = ffmpeg_tools.popen(args, stdin=__import__("subprocess").PIPE)
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    font = QFont("Consolas", int(H / 24))
    font.setBold(True)
    try:
        for fi in range(n):
            lt = log_start + fi / fps
            roll = float(np.interp(lt, sim.t, sim.roll))
            pitch = float(np.interp(lt, sim.t, sim.pitch))
            alt = float(np.interp(lt, sim.t, sim.alt))
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.fillRect(0, 0, W, H, QColor(70, 130, 200))
            p.translate(W / 2, H / 2 + pitch * H / 60.0)
            p.rotate(-roll)
            ground = QColor(90, 120, 60) if alt > 2 else QColor(110, 110, 100)
            p.fillRect(QRectF(-W * 2, 0, W * 4, H * 4), ground)
            p.setPen(QColor(255, 255, 255, 180))
            for k in range(-20, 21):
                x = k * 60.0
                p.drawLine(QPointF(x, 10), QPointF(x * 1.6, H))
            p.resetTransform()
            # propeller blur when throttle is up
            thr = float(np.interp(lt, sim.t, sim.throttle))
            if thr > 5:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(20, 20, 20, int(40 + thr)))
                p.drawEllipse(QPointF(W / 2, H * 0.92), W * 0.3, H * 0.05)
            p.setPen(QColor(255, 255, 255))
            p.setFont(font)
            p.drawText(QRectF(0, 10, W - 20, H / 10), Qt.AlignmentFlag.AlignRight, f"{label}  f{fi:05d}")
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QColor(255, 220, 0))
            poly = QPolygonF([QPointF(W / 2 - 40, H / 2), QPointF(W / 2, H / 2 + 15), QPointF(W / 2 + 40, H / 2)])
            p.drawPolyline(poly)
            p.end()
            proc.stdin.write(img.constBits())
            if progress and fi % 30 == 0:
                progress(fi / n)
        proc.stdin.close()
        proc.wait()
    except BaseException:
        proc.kill()
        raise
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed making {path}")


def make_demo_project(folder: str, progress=None) -> str:
    """Create the demo files plus a ready-to-open project file. Returns the project path."""
    from .core.media import probe
    from .core.project import Clip, Project
    from .core.sync import auto_place
    from .telemetry.loader import load_telemetry

    paths = make_demo(folder, progress)
    tel = load_telemetry(paths["bin"])
    proj = Project(log_path=paths["bin"])
    proj.path = os.path.join(folder, "demo.fovproj")
    for v in paths["videos"]:
        clip = Clip(path=v, info=probe(v))
        auto_place(proj, clip, tel)
        proj.clips.append(clip)
    proj.overlay.get("title").options["text"] = "Demo flight"
    proj.save()
    return proj.path


def make_demo(folder: str, progress=None) -> dict:
    """Create demo log + two videos in `folder`. Returns paths."""
    os.makedirs(folder, exist_ok=True)
    sim = simulate()
    bin_path = os.path.join(folder, "demo_flight.bin")
    tlog_path = os.path.join(folder, "demo_flight.tlog")
    write_bin(bin_path, sim)
    write_tlog(tlog_path, sim)
    cam1 = os.path.join(folder, "GX010001.MP4")
    cam2 = os.path.join(folder, "GX020001.MP4")
    # Camera clock is 2.4 s fast so auto-placement is close but needs a nudge with the sync tool.
    write_camera_video(cam1, sim, 64.0, 40.0, creation_utc=UTC_BOOT + 64.0 + 2.4, label="CAM A",
                       progress=(lambda f: progress(0.1 + 0.4 * f)) if progress else None)
    write_camera_video(cam2, sim, 104.0, 30.0, creation_utc=UTC_BOOT + 104.0 + 2.4, label="CAM A",
                       progress=(lambda f: progress(0.5 + 0.25 * f)) if progress else None)
    ground = os.path.join(folder, "phone_ground.mp4")
    write_camera_video(ground, sim, 80.0, 35.0, size=(1280, 720), creation_utc=UTC_BOOT + 80.0,
                       label="PHONE", progress=(lambda f: progress(0.75 + 0.25 * f)) if progress else None)
    return {"bin": bin_path, "tlog": tlog_path, "videos": [cam1, cam2, ground]}
