"""Reading ArduPilot DataFlash (.bin/.log) and telemetry (.tlog) files into numpy arrays."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

LOADER_VERSION = 3

# Message types read from DataFlash logs.
BIN_TYPES = [
    "ATT", "AHR2", "GPS", "POS", "BARO", "CTUN", "ARSP", "BAT", "CURR", "MODE", "ARM", "EV",
    "XKF2", "NKF2", "RSSI", "TECS", "ORGN", "RCOU", "RCIN", "MSG", "VER", "QTUN", "TERR",
]
# Message types read from telemetry logs.
TLOG_TYPES = [
    "HEARTBEAT", "ATTITUDE", "VFR_HUD", "GLOBAL_POSITION_INT", "GPS_RAW_INT", "SYS_STATUS",
    "BATTERY_STATUS", "WIND", "RC_CHANNELS", "RC_CHANNELS_RAW", "HOME_POSITION", "STATUSTEXT",
    "SERVO_OUTPUT_RAW", "TERRAIN_REPORT",
]

GPS_EPOCH_UNIX = 315964800      # 1980-01-06T00:00:00Z
GPS_LEAP_SECONDS = 18           # GPS - UTC, valid since 2017

ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]


class LoadCancelled(Exception):
    pass


@dataclass
class RawLog:
    path: str
    source_type: str                     # 'bin' or 'tlog'
    msgs: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)  # each has '_t' (log seconds)
    texts: list[tuple[float, str]] = field(default_factory=list)
    vehicle: str = "plane"
    mav_type: int = 1
    duration: float = 0.0
    utc_offset: float | None = None

    def get(self, msg: str, fld: str) -> tuple[np.ndarray, np.ndarray] | None:
        m = self.msgs.get(msg)
        if not m or fld not in m:
            return None
        return m["_t"], m[fld]


def gps_to_unix(week, ms):
    return GPS_EPOCH_UNIX + np.asarray(week, dtype=np.float64) * 604800.0 + np.asarray(ms, dtype=np.float64) / 1000.0 - GPS_LEAP_SECONDS


def _vehicle_from_mav_type(mav_type: int) -> str:
    # MAV_TYPE numbers: 1 fixed wing, 19-25 VTOL variants, 10 rover, 11 boat, 12 sub
    if mav_type in (1, 19, 20, 21, 22, 23, 24, 25):
        return "plane"
    if mav_type in (10, 11):
        return "rover"
    if mav_type == 12:
        return "sub"
    return "copter"


# ---------------------------------------------------------------- DataFlash (.bin)

_NP_TYPES = {
    "b": "i1", "B": "u1", "h": "<i2", "H": "<u2", "i": "<i4", "I": "<u4", "f": "<f4", "d": "<f8",
    "n": "S4", "N": "S16", "Z": "S64", "c": "<i2", "C": "<u2", "e": "<i4", "E": "<u4",
    "L": "<i4", "M": "i1", "q": "<i8", "Q": "<u8", "a": ("<i2", (32,)), "g": "<f2",
}
_CHUNK_ROWS = 200_000


def _bin_extract_fast(df, name: str) -> dict[str, np.ndarray] | None:
    """Vectorised extraction of every message of one type using pymavlink's offset index."""
    mtype = df.name_to_id.get(name)
    if mtype is None or mtype not in df.formats:
        return None
    fmt = df.formats[mtype]
    offs = np.asarray(df.offsets[mtype], dtype=np.int64)
    if offs.size == 0:
        return None
    body_len = fmt.len - 3
    offs = offs[offs + fmt.len <= df.data_len]
    cols = list(fmt.columns)
    chars = [c for c in fmt.format]
    if len(cols) != len(chars):
        raise ValueError("column/format mismatch")
    seen: dict[str, int] = {}
    np_fields = []
    for c, ch in zip(cols, chars):
        nm = c
        if nm in seen:
            seen[nm] += 1
            nm = f"{c}_{seen[c]}"
        else:
            seen[nm] = 0
        np_fields.append((nm, _NP_TYPES[ch]))
    dt = np.dtype(np_fields)
    if dt.itemsize != body_len:
        raise ValueError("format size mismatch")

    buf = np.frombuffer(df.data_map, dtype=np.uint8)
    cols_idx = np.arange(body_len, dtype=np.int64)[None, :] + 3
    parts = []
    for i in range(0, offs.size, _CHUNK_ROWS):
        idx = offs[i:i + _CHUNK_ROWS, None] + cols_idx
        parts.append(np.ascontiguousarray(buf[idx]).view(dt).reshape(-1))
    rec = np.concatenate(parts) if len(parts) > 1 else parts[0]

    out: dict[str, np.ndarray] = {}
    for (nm, _), ch in zip(np_fields, chars):
        if ch in "nNZa":
            if name == "MSG" and nm == "Message":
                out[nm] = rec[nm]
            continue
        arr = rec[nm].astype(np.float64)
        if ch in "cCeE":
            arr /= 100.0
        elif ch == "L":
            arr /= 1.0e7
        out[nm] = arr
    return out


def _bin_collect_slow(df, names: list[str], cancel: CancelFn | None) -> dict[str, dict[str, np.ndarray]]:
    """Message-by-message fallback in a single pass (text logs, odd formats)."""
    df.rewind()
    want = set(names)
    rows: dict[str, dict[str, list]] = {}
    fields: dict[str, list[str]] = {}
    n = 0
    while True:
        m = df.recv_match(type=list(want))
        if m is None:
            break
        n += 1
        if cancel and n % 20000 == 0 and cancel():
            raise LoadCancelled()
        name = m.get_type()
        if name not in want:
            continue
        flds = fields.get(name)
        if flds is None:
            flds = m.get_fieldnames()
            fields[name] = flds
        r = rows.setdefault(name, {})
        for f in flds:
            v = getattr(m, f, None)
            if name == "MSG" and f == "Message":
                r.setdefault(f, []).append(str(v).encode("utf-8", "replace"))
            elif isinstance(v, (int, float)):
                r.setdefault(f, []).append(float(v))
    out = {}
    for name, r in rows.items():
        if not r:
            continue
        cnt = min(len(v) for v in r.values())
        out[name] = {k: (np.array(v[:cnt]) if k == "Message" else np.asarray(v[:cnt], dtype=np.float64))
                     for k, v in r.items() if len(v) >= cnt}
    return out


def _instance_filter(name: str, d: dict[str, np.ndarray], instance_field: str | None) -> dict[str, np.ndarray]:
    cand = [instance_field] if instance_field else []
    cand += ["I", "Inst", "Instance"]
    if name.startswith(("XKF", "NKF")):
        cand.insert(0, "C")
    for f in cand:
        if f and f in d and d[f].size:
            inst = d[f]
            keep = inst == np.nanmin(inst)
            if not np.all(keep):
                d = {k: v[keep] for k, v in d.items()}
            break
    return d


def _load_bin(path: str, progress: ProgressFn | None, cancel: CancelFn | None) -> RawLog:
    from pymavlink import DFReader, mavutil

    is_text = path.lower().endswith(".log")

    def cb(pct):
        if progress:
            progress(min(float(pct), 100.0) * 0.8 / 100.0, "Indexing log")
        if cancel and cancel():
            raise LoadCancelled()

    if progress:
        progress(0.0, "Opening log")
    if is_text:
        df = DFReader.DFReader_text(path, zero_time_base=False, progress_callback=cb)
    else:
        df = DFReader.DFReader_binary(path, zero_time_base=False, progress_callback=cb)

    raw = RawLog(path=path, source_type="bin")
    try:
        mav_type = getattr(df, "mav_type", mavutil.mavlink.MAV_TYPE_FIXED_WING)
        raw.mav_type = int(mav_type)
        raw.vehicle = _vehicle_from_mav_type(raw.mav_type)
        extracted: dict[str, dict[str, np.ndarray]] = {}
        slow: list[str] = []
        for i, name in enumerate(BIN_TYPES):
            if cancel and cancel():
                raise LoadCancelled()
            if progress:
                progress(0.8 + 0.15 * i / len(BIN_TYPES), f"Reading {name}")
            if is_text or not hasattr(df, "offsets"):
                slow.append(name)
                continue
            if name not in df.name_to_id:
                continue
            try:
                d = _bin_extract_fast(df, name)
            except Exception:
                d = None
                slow.append(name)
            if d:
                extracted[name] = d
        if slow:
            if progress:
                progress(0.95, "Reading remaining messages")
            extracted.update(_bin_collect_slow(df, slow, cancel))

        for name, d in extracted.items():
            if "TimeUS" in d:
                tabs = d["TimeUS"] / 1e6
            elif "TimeMS" in d:
                tabs = d["TimeMS"] / 1e3
            else:
                continue
            d["_tabs"] = tabs
            fmt = None
            mtype = getattr(df, "name_to_id", {}).get(name)
            if mtype is not None and mtype in df.formats:
                fmt = df.formats[mtype]
            d = _instance_filter(name, d, getattr(fmt, "instance_field", None) if fmt else None)
            raw.msgs[name] = d
    finally:
        try:
            df.close()
        except Exception:
            pass

    # Log time origin: earliest timestamp, ignoring zero/garbage stamps.
    starts = [np.nanmin(m["_tabs"][m["_tabs"] > 0]) for m in raw.msgs.values() if np.any(m["_tabs"] > 0)]
    t0 = min(starts) if starts else 0.0
    t_end = 0.0
    for name, m in raw.msgs.items():
        m["_t"] = m.pop("_tabs") - t0
        if m["_t"].size:
            t_end = max(t_end, float(np.nanmax(m["_t"])))
    raw.duration = t_end

    # Vehicle type from firmware string if the reader didn't find it.
    msg = raw.msgs.get("MSG")
    if msg is not None and "Message" in msg:
        texts = []
        for t, b in zip(msg["_t"], msg["Message"]):
            s = b.split(b"\0", 1)[0].decode("utf-8", "replace") if isinstance(b, (bytes, np.bytes_)) else str(b)
            texts.append((float(t), s))
            if "ArduPlane" in s:
                raw.vehicle, raw.mav_type = "plane", 1
            elif "ArduCopter" in s:
                raw.vehicle, raw.mav_type = "copter", 2
            elif "ArduRover" in s:
                raw.vehicle, raw.mav_type = "rover", 10
        raw.texts = texts
        del msg["Message"]

    # UTC from GPS week / ms
    g = raw.msgs.get("GPS")
    if g is not None and "GWk" in g and "GMS" in g:
        ok = (g["GWk"] > 1000) & (g.get("Status", np.full(g["GWk"].shape, 3)) >= 3)
        if np.any(ok):
            utc = gps_to_unix(g["GWk"][ok], g["GMS"][ok])
            raw.utc_offset = float(np.median(utc - g["_t"][ok]))
    return raw


# ---------------------------------------------------------------- Telemetry (.tlog)

def _load_tlog(path: str, progress: ProgressFn | None, cancel: CancelFn | None) -> RawLog:
    from pymavlink import mavutil

    size = max(os.path.getsize(path), 1)
    mlog = mavutil.mavlink_connection(path, dialect="ardupilotmega", robust_parsing=True)
    want = set(TLOG_TYPES)
    data: dict[tuple[int, int, str], dict[str, list]] = {}
    fields_cache: dict[str, list[str]] = {}
    texts: dict[tuple[int, int], list[tuple[float, str]]] = {}
    heartbeats: dict[tuple[int, int], tuple[int, int]] = {}
    counts: dict[tuple[int, int], int] = {}
    n = 0
    try:
        while True:
            m = mlog.recv_match(type=TLOG_TYPES)
            if m is None:
                break
            n += 1
            if n % 5000 == 0:
                if progress:
                    progress(min(mlog.f.tell() / size, 1.0) * 0.95, "Reading telemetry")
                if cancel and cancel():
                    raise LoadCancelled()
            mt = m.get_type()
            if mt not in want:
                continue
            key = (m.get_srcSystem(), m.get_srcComponent())
            ts = float(getattr(m, "_timestamp", 0.0) or 0.0)
            counts[key] = counts.get(key, 0) + 1
            if mt == "HEARTBEAT":
                heartbeats[key] = (m.autopilot, m.type)
            if mt == "STATUSTEXT":
                texts.setdefault(key, []).append((ts, str(m.text)))
                continue
            if mt == "BATTERY_STATUS" and getattr(m, "id", 0) != 0:
                continue
            flds = fields_cache.get(mt)
            if flds is None:
                flds = [f for f in m.get_fieldnames() if isinstance(getattr(m, f, None), (int, float))]
                fields_cache[mt] = flds
            row = data.setdefault((key[0], key[1], mt), {"_tabs": []})
            row["_tabs"].append(ts)
            for f in flds:
                row.setdefault(f, []).append(float(getattr(m, f)))
    finally:
        try:
            mlog.close()
        except Exception:
            pass

    if progress:
        progress(0.97, "Organising data")

    # Pick the vehicle: an ArduPilot autopilot heartbeat that isn't a ground station.
    candidates = [k for k, (ap, typ) in heartbeats.items() if ap == 3 and typ not in (6, 18, 26, 27)]
    if not candidates:
        candidates = [k for k, (ap, typ) in heartbeats.items() if ap != 8 and typ not in (6, 18, 26, 27)]
    if not candidates:
        candidates = list(counts)
    if not candidates:
        raise RuntimeError("No vehicle data found in this telemetry log")
    veh = max(candidates, key=lambda k: counts.get(k, 0))

    raw = RawLog(path=path, source_type="tlog")
    if veh in heartbeats:
        raw.mav_type = int(heartbeats[veh][1])
        raw.vehicle = _vehicle_from_mav_type(raw.mav_type)
    for (sysid, compid, mt), row in data.items():
        if (sysid, compid) != veh:
            continue
        n_rows = len(row["_tabs"])
        raw.msgs[mt] = {k: np.asarray(v[:n_rows], dtype=np.float64) for k, v in row.items()}
    tvals = [m["_tabs"][0] for m in raw.msgs.values() if m["_tabs"].size]
    t0 = min(tvals) if tvals else 0.0
    t_end = 0.0
    for m in raw.msgs.values():
        m["_t"] = m.pop("_tabs") - t0
        if m["_t"].size:
            t_end = max(t_end, float(m["_t"][-1]))
    raw.duration = t_end
    raw.utc_offset = t0 if t0 > 946684800 else None
    raw.texts = [(t - t0, s) for t, s in texts.get(veh, [])]
    return raw


# ---------------------------------------------------------------- caching

def _cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "FlightOverlay", "cache")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_path(path: str) -> str:
    st = os.stat(path)
    key = f"{os.path.abspath(path).lower()}|{st.st_size}|{int(st.st_mtime)}|{LOADER_VERSION}"
    return os.path.join(_cache_dir(), hashlib.sha1(key.encode()).hexdigest() + ".npz")


def _save_cache(raw: RawLog, cpath: str) -> None:
    arrays = {}
    for mt, d in raw.msgs.items():
        for f, arr in d.items():
            arrays[f"{mt}|{f}"] = arr
    meta = {"path": raw.path, "source_type": raw.source_type, "vehicle": raw.vehicle,
            "mav_type": raw.mav_type, "duration": raw.duration, "utc_offset": raw.utc_offset,
            "texts": raw.texts}
    arrays["__meta__"] = np.frombuffer(json.dumps(meta).encode("utf-8"), dtype=np.uint8)
    tmp = cpath + ".tmp.npz"
    np.savez_compressed(tmp, **arrays)
    os.replace(tmp, cpath)


def _load_cache(cpath: str) -> RawLog | None:
    try:
        with np.load(cpath, allow_pickle=False) as z:
            meta = json.loads(bytes(z["__meta__"]).decode("utf-8"))
            raw = RawLog(path=meta["path"], source_type=meta["source_type"], vehicle=meta["vehicle"],
                         mav_type=meta["mav_type"], duration=meta["duration"], utc_offset=meta["utc_offset"],
                         texts=[tuple(x) for x in meta["texts"]])
            for k in z.files:
                if k == "__meta__":
                    continue
                mt, f = k.split("|", 1)
                raw.msgs.setdefault(mt, {})[f] = z[k]
        return raw
    except Exception:
        return None


def load_raw(path: str, progress: ProgressFn | None = None, cancel: CancelFn | None = None,
             use_cache: bool = True) -> RawLog:
    path = os.path.abspath(path)
    cpath = None
    if use_cache:
        try:
            cpath = _cache_path(path)
            if os.path.exists(cpath):
                raw = _load_cache(cpath)
                if raw is not None:
                    raw.path = path
                    if progress:
                        progress(1.0, "Loaded from cache")
                    return raw
        except OSError:
            cpath = None
    ext = os.path.splitext(path)[1].lower()
    if ext == ".tlog":
        raw = _load_tlog(path, progress, cancel)
    else:
        raw = _load_bin(path, progress, cancel)
    if not raw.msgs:
        raise RuntimeError("No usable flight data was found in this log")
    if cpath:
        try:
            _save_cache(raw, cpath)
        except OSError:
            pass
    if progress:
        progress(1.0, "Done")
    return raw


def load_telemetry(path: str, progress: ProgressFn | None = None, cancel: CancelFn | None = None,
                   use_cache: bool = True):
    from .channels import build_telemetry
    raw = load_raw(path, progress, cancel, use_cache)
    return build_telemetry(raw)
