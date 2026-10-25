"""Unit conversion and value formatting for display."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

M_TO_FT = 3.280839895
MS_TO = {"m/s": 1.0, "km/h": 3.6, "mph": 2.236936292, "kn": 1.943844492}
SPEED_UNITS = list(MS_TO)


@dataclass
class UnitPrefs:
    system: str = "metric"   # 'metric' or 'imperial'
    speed: str = "km/h"      # 'm/s', 'km/h', 'mph', 'kn'

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "UnitPrefs":
        d = d or {}
        return cls(system=d.get("system", "metric"), speed=d.get("speed", "km/h"))


def fmt_duration(sec: float) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def convert(quantity: str, v: float, u: UnitPrefs) -> tuple[float, str]:
    """Convert an SI value into display units. Returns (value, unit label)."""
    imp = u.system == "imperial"
    if quantity == "altitude":
        return (v * M_TO_FT, "ft") if imp else (v, "m")
    if quantity == "speed":
        return v * MS_TO.get(u.speed, 1.0), u.speed
    if quantity == "vspeed":
        return (v * M_TO_FT * 60.0, "ft/min") if imp else (v, "m/s")
    if quantity == "distance":
        if imp:
            ft = v * M_TO_FT
            return (ft, "ft") if ft < 1000 else (ft / 5280.0, "mi")
        return (v, "m") if v < 1000 else (v / 1000.0, "km")
    units = {"voltage": "V", "current": "A", "capacity": "mAh", "percent": "%", "power": "W",
             "angle": "°", "heading": "°"}
    return v, units.get(quantity, "")


def format_value(quantity: str, v: float, u: UnitPrefs) -> tuple[str, str]:
    """Return (number text, unit text). NaN gives '--'."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "--", convert(quantity, 0.0, u)[1] if quantity not in ("duration", "mode", "bool") else ""
    if quantity == "duration":
        return fmt_duration(v), ""
    if quantity == "bool":
        return ("ARMED" if v > 0.5 else "DISARMED"), ""
    if quantity == "coord":
        return f"{v:.6f}", "°"
    if quantity == "count":
        return f"{int(round(v))}", ""
    if quantity == "ratio":
        return f"{v:.1f}", ""
    val, unit = convert(quantity, v, u)
    if quantity == "heading":
        return f"{int(round(val)) % 360:03d}", unit
    if quantity == "distance":
        return (f"{val:.0f}" if unit in ("m", "ft") else f"{val:.2f}"), unit
    if quantity == "vspeed":
        return (f"{val:+.0f}" if unit == "ft/min" else f"{val:+.1f}"), unit
    if quantity == "speed":
        return (f"{val:.1f}" if unit == "m/s" else f"{val:.0f}"), unit
    if quantity in ("voltage",):
        return f"{val:.2f}", unit
    if quantity in ("current",):
        return f"{val:.1f}", unit
    if quantity == "power":
        return f"{val:.0f}", unit
    return f"{val:.0f}", unit
