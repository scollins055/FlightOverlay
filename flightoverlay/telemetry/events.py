"""Finding recognisable moments in a log (arming, takeoff, landing, mode changes) for syncing."""
from __future__ import annotations

import numpy as np

from .series import Event, Telemetry


def _arm_intervals(tel: Telemetry) -> list[tuple[float, float]]:
    s = tel.channels.get("armed")
    if s is None:
        return [(0.0, tel.duration)]
    out = []
    start = None
    for t, v in zip(s.t, s.v):
        if v > 0.5 and start is None:
            start = float(t)
        elif v <= 0.5 and start is not None:
            out.append((start, float(t)))
            start = None
    if start is not None:
        out.append((start, tel.duration))
    return out


def _first_sustained(t: np.ndarray, cond: np.ndarray, hold: float) -> int | None:
    """Index of the first sample where cond becomes true and stays true for `hold` seconds."""
    idx = np.nonzero(cond)[0]
    for i in idx:
        j = np.searchsorted(t, t[i] + hold)
        if j <= i:
            continue
        if np.all(cond[i:j]):
            return int(i)
    return None


def detect(tel: Telemetry) -> list[Event]:
    ev: list[Event] = []
    s = tel.channels.get("armed")
    if s is not None:
        prev = 0.0
        for t, v in zip(s.t, s.v):
            if v > 0.5 and prev <= 0.5:
                ev.append(Event(float(t), "Armed", "arm"))
            elif v <= 0.5 and prev > 0.5:
                ev.append(Event(float(t), "Disarmed", "disarm"))
            prev = v
    for t0, t1, name in tel.mode_spans():
        ev.append(Event(t0, f"Mode {name}", "mode"))

    for a0, a1 in _arm_intervals(tel):
        thr = tel.channels.get("throttle")
        if thr is not None:
            tt, vv = thr.window(a0, a1)
            i = np.nonzero(vv > 15.0)[0]
            if i.size:
                ev.append(Event(float(tt[i[0]]), "Throttle up", "throttle"))
        gs = tel.channels.get("groundspeed")
        if gs is not None:
            tt, vv = gs.window(a0, a1)
            if tt.size > 2:
                k = _first_sustained(tt, vv > 5.0, 2.0)
                if k is not None:
                    ev.append(Event(float(tt[k]), "Takeoff (speed > 5 m/s)", "takeoff"))
                    after = tt > tt[k] + 5.0
                    if np.any(after):
                        t2, v2 = tt[after], vv[after]
                        m = _first_sustained(t2, v2 < 2.0, 3.0)
                        if m is not None:
                            ev.append(Event(float(t2[m]), "Landed (speed < 2 m/s)", "landing"))
    ev.sort(key=lambda e: e.t)
    return ev
