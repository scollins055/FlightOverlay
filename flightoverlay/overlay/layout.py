"""Overlay layout: which widgets are shown, where, and how they look."""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field


@dataclass
class WidgetConfig:
    id: str
    type: str
    enabled: bool = True
    x: float = 0.5          # widget centre, fraction of frame width
    y: float = 0.5          # widget centre, fraction of frame height
    scale: float = 1.0
    channel: str | None = None
    color: str = ""         # empty = style default
    opacity: float = 1.0
    options: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WidgetConfig":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: copy.deepcopy(v) for k, v in d.items() if k in known})


@dataclass
class OverlayStyle:
    font_family: str = "Segoe UI"
    scale: float = 1.0                 # global size multiplier
    text_color: str = "#ffffff"
    label_color: str = "#9fd8ff"
    hud_color: str = "#7dffa0"
    outline: bool = True
    panels: bool = True                # dark translucent box behind widgets
    panel_opacity: float = 0.35

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "OverlayStyle":
        d = d or {}
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class OverlayLayout:
    widgets: list[WidgetConfig] = field(default_factory=list)
    style: OverlayStyle = field(default_factory=OverlayStyle)
    preset: str = ""

    def get(self, wid: str) -> WidgetConfig | None:
        for w in self.widgets:
            if w.id == wid:
                return w
        return None

    def enabled(self) -> list[WidgetConfig]:
        return [w for w in self.widgets if w.enabled]

    def to_dict(self) -> dict:
        return {"preset": self.preset, "style": self.style.to_dict(),
                "widgets": [w.to_dict() for w in self.widgets]}

    @classmethod
    def from_dict(cls, d: dict | None) -> "OverlayLayout":
        if not d:
            from .presets import make_preset
            return make_preset("Dashboard")
        lay = cls(widgets=[WidgetConfig.from_dict(w) for w in d.get("widgets", [])],
                  style=OverlayStyle.from_dict(d.get("style")), preset=d.get("preset", ""))
        from .presets import ensure_all_widgets
        ensure_all_widgets(lay)
        return lay

    def clone(self) -> "OverlayLayout":
        return OverlayLayout.from_dict(copy.deepcopy(self.to_dict()))
