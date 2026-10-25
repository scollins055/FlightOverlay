"""Time series with interpolation, and the Telemetry container."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# How long past the last sample a value is still shown before it becomes "no data".
STALE_AFTER = 3.0


class Series:
    """Samples of one channel. kind: 'linear', 'hold', 'angle360' or 'angle180'."""

    __slots__ = ("t", "v", "kind", "_unwrapped")

    def __init__(self, t, v, kind: str = "linear"):
        t = np.asarray(t, dtype=np.float64)
        v = np.asarray(v, dtype=np.float64)
        ok = np.isfinite(t) & np.isfinite(v)
        t, v = t[ok], v[ok]
        if t.size > 1 and np.any(np.diff(t) < 0):
            order = np.argsort(t, kind="stable")
            t, v = t[order], v[order]
        self.t = t
        self.v = v
        self.kind = kind
        self._unwrapped = None
        if kind in ("angle360", "angle180") and v.size:
            self._unwrapped = np.rad2deg(np.unwrap(np.deg2rad(v)))

    def __len__(self) -> int:
        return int(self.t.size)

    @property
    def start(self) -> float:
        return float(self.t[0]) if self.t.size else math.nan

    @property
    def end(self) -> float:
        return float(self.t[-1]) if self.t.size else math.nan

    def at(self, x: float) -> float:
        """Value at time x, or NaN when there is no data near x."""
        t = self.t
        n = t.size
        if n == 0 or x < t[0] or x > t[-1] + STALE_AFTER:
            return math.nan
        if self.kind == "hold" or n == 1 or x >= t[-1]:
            i = int(np.searchsorted(t, x, side="right")) - 1
            return float(self.v[max(i, 0)])
        if self._unwrapped is not None:
            a = float(np.interp(x, t, self._unwrapped))
            if self.kind == "angle360":
                return a % 360.0
            return (a + 180.0) % 360.0 - 180.0
        return float(np.interp(x, t, self.v))

    def at_many(self, xs) -> np.ndarray:
        xs = np.asarray(xs, dtype=np.float64)
        if self.t.size == 0:
            return np.full(xs.shape, np.nan)
        if self.kind == "hold":
            idx = np.searchsorted(self.t, xs, side="right") - 1
            out = self.v[np.clip(idx, 0, None)].astype(np.float64)
        elif self._unwrapped is not None:
            out = np.interp(xs, self.t, self._unwrapped)
            out = out % 360.0 if self.kind == "angle360" else (out + 180.0) % 360.0 - 180.0
        else:
            out = np.interp(xs, self.t, self.v)
        out[(xs < self.t[0]) | (xs > self.t[-1] + STALE_AFTER)] = np.nan
        return out

    def window(self, t0: float, t1: float) -> tuple[np.ndarray, np.ndarray]:
        i0 = int(np.searchsorted(self.t, t0, side="left"))
        i1 = int(np.searchsorted(self.t, t1, side="right"))
        return self.t[i0:i1], self.v[i0:i1]

    def min_max(self) -> tuple[float, float]:
        if self.v.size == 0:
            return (0.0, 1.0)
        return float(np.nanmin(self.v)), float(np.nanmax(self.v))


@dataclass
class Event:
    t: float
    label: str
    kind: str  # 'arm', 'disarm', 'mode', 'throttle', 'takeoff', 'landing'


@dataclass
class Telemetry:
    source_path: str
    source_type: str                       # 'bin' or 'tlog'
    vehicle: str = "plane"
    start_time: float = 0.0                # always 0 (log time origin)
    duration: float = 0.0
    utc_offset: float | None = None        # UTC unix seconds = log time + utc_offset
    channels: dict[str, Series] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)  # channel -> "MSG.Field" it came from
    mode_names: dict[int, str] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    home: tuple[float, float, float] | None = None   # lat, lon, alt (MSL m)
    messages: list[tuple[float, str]] = field(default_factory=list)  # (t, text) from MSG/STATUSTEXT

    def has(self, key: str) -> bool:
        s = self.channels.get(key)
        return s is not None and len(s) > 0

    def value(self, key: str, t: float) -> float:
        s = self.channels.get(key)
        return s.at(t) if s is not None else math.nan

    def mode_name(self, t: float) -> str | None:
        v = self.value("mode", t)
        if math.isnan(v):
            return None
        return self.mode_names.get(int(v), f"MODE {int(v)}")

    def mode_spans(self) -> list[tuple[float, float, str]]:
        s = self.channels.get("mode")
        if s is None or len(s) == 0:
            return []
        spans = []
        cur_v, cur_t = s.v[0], s.t[0]
        for t, v in zip(s.t[1:], s.v[1:]):
            if v != cur_v:
                spans.append((float(cur_t), float(t), self.mode_names.get(int(cur_v), str(int(cur_v)))))
                cur_v, cur_t = v, t
        spans.append((float(cur_t), float(self.duration), self.mode_names.get(int(cur_v), str(int(cur_v)))))
        return spans

    def utc_at(self, t: float) -> float | None:
        return None if self.utc_offset is None else t + self.utc_offset
