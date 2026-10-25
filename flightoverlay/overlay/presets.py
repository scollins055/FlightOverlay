"""Built-in overlay layouts and saving/loading user presets."""
from __future__ import annotations

import json
import os

from .layout import OverlayLayout, OverlayStyle, WidgetConfig

READOUT_CHANNELS = [
    "airspeed", "groundspeed", "alt_rel", "alt_msl", "vspeed", "throttle", "distance_home",
    "distance_flown", "batt_voltage", "batt_current", "batt_used", "batt_remaining", "batt_power",
    "rssi", "link_quality", "gps_sats", "gps_hdop", "roll", "pitch", "yaw", "course", "wind_speed",
]
GAUGES = ["mode", "heading", "horizon", "hud_horizon", "speedtape", "alttape", "throttle", "minimap",
          "home", "wind", "battery", "gps", "coords", "timer", "clock", "graph", "messages", "title"]

_GAUGE_DEFAULTS = {
    "mode": (0.08, 0.06), "heading": (0.5, 0.06), "horizon": (0.5, 0.84), "hud_horizon": (0.5, 0.5),
    "speedtape": (0.26, 0.5), "alttape": (0.74, 0.5), "throttle": (0.19, 0.5), "minimap": (0.905, 0.83),
    "home": (0.635, 0.885), "wind": (0.365, 0.885), "battery": (0.095, 0.9), "gps": (0.92, 0.115),
    "coords": (0.5, 0.965), "timer": (0.065, 0.125), "clock": (0.925, 0.055), "graph": (0.12, 0.7),
    "messages": (0.5, 0.16), "title": (0.5, 0.2),
}


def _readout_default_pos(i: int) -> tuple[float, float]:
    col, row = divmod(i, 8)
    return (0.3 + 0.2 * (col % 3), 0.25 + 0.07 * row)


def all_widgets() -> list[WidgetConfig]:
    out = []
    for i, ch in enumerate(READOUT_CHANNELS):
        x, y = _readout_default_pos(i)
        out.append(WidgetConfig(id=f"readout:{ch}", type="readout", channel=ch, enabled=False, x=x, y=y))
    for g in GAUGES:
        x, y = _GAUGE_DEFAULTS[g]
        opts = {"text": "My flight"} if g == "title" else {}
        out.append(WidgetConfig(id=g, type=g, enabled=False, x=x, y=y, options=opts))
    return out


# id -> overrides for enabled widgets
_PRESETS: dict[str, dict[str, dict]] = {
    "Dashboard": {
        "mode": {}, "timer": {}, "heading": {}, "clock": {}, "gps": {},
        "readout:airspeed": {"x": 0.085, "y": 0.30, "options": {"align": "left"}},
        "readout:groundspeed": {"x": 0.085, "y": 0.37, "options": {"align": "left"}},
        "readout:vspeed": {"x": 0.085, "y": 0.44, "options": {"align": "left"}},
        "readout:throttle": {"x": 0.085, "y": 0.51, "options": {"align": "left"}},
        "readout:alt_rel": {"x": 0.915, "y": 0.30, "options": {"align": "right"}},
        "readout:distance_home": {"x": 0.915, "y": 0.37, "options": {"align": "right"}},
        "readout:distance_flown": {"x": 0.915, "y": 0.44, "options": {"align": "right"}},
        "readout:rssi": {"x": 0.915, "y": 0.51, "options": {"align": "right"}},
        "horizon": {}, "battery": {}, "minimap": {}, "home": {}, "wind": {},
    },
    "Plane HUD": {
        "mode": {}, "timer": {}, "heading": {}, "clock": {}, "gps": {},
        "hud_horizon": {}, "speedtape": {}, "alttape": {}, "throttle": {},
        "readout:groundspeed": {"x": 0.26, "y": 0.77, "options": {"align": "center"}},
        "readout:vspeed": {"x": 0.74, "y": 0.77, "options": {"align": "center"}},
        "home": {"x": 0.5, "y": 0.86}, "battery": {}, "minimap": {},
    },
    "Minimal": {
        "mode": {}, "timer": {},
        "readout:alt_rel": {"x": 0.915, "y": 0.06, "options": {"align": "right"}},
        "readout:groundspeed": {"x": 0.915, "y": 0.125, "options": {"align": "right"}},
        "readout:batt_voltage": {"x": 0.085, "y": 0.94, "options": {"align": "left"}},
        "readout:distance_home": {"x": 0.915, "y": 0.94, "options": {"align": "right"}},
    },
}

PRESET_NAMES = list(_PRESETS)


def make_preset(name: str) -> OverlayLayout:
    spec = _PRESETS.get(name) or _PRESETS["Dashboard"]
    widgets = all_widgets()
    for w in widgets:
        if w.id in spec:
            w.enabled = True
            for k, v in spec[w.id].items():
                if k == "options":
                    w.options.update(v)
                else:
                    setattr(w, k, v)
    return OverlayLayout(widgets=widgets, style=OverlayStyle(), preset=name if name in _PRESETS else "Dashboard")


def ensure_all_widgets(layout: OverlayLayout) -> None:
    have = {w.id for w in layout.widgets}
    for w in all_widgets():
        if w.id not in have:
            layout.widgets.append(w)


# ------------------------------------------------------------------ user presets

def user_preset_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "FlightOverlay", "presets")
    os.makedirs(d, exist_ok=True)
    return d


def list_user_presets() -> list[str]:
    try:
        return sorted(os.path.splitext(f)[0] for f in os.listdir(user_preset_dir()) if f.endswith(".json"))
    except OSError:
        return []


def save_user_preset(name: str, layout: OverlayLayout) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in " -_").strip() or "My preset"
    path = os.path.join(user_preset_dir(), safe + ".json")
    d = layout.to_dict()
    d["preset"] = safe
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    return path


def load_user_preset(name: str) -> OverlayLayout:
    with open(os.path.join(user_preset_dir(), name + ".json"), encoding="utf-8") as f:
        return OverlayLayout.from_dict(json.load(f))
