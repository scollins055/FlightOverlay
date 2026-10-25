"""Draws a whole overlay layout for one moment in time. Used by both the preview and the export."""
from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter

from ..telemetry.series import Telemetry
from ..telemetry.units import UnitPrefs
from .layout import OverlayLayout, WidgetConfig
from .widgets import WIDGETS, Ctx


class OverlayRenderer:
    def __init__(self, layout: OverlayLayout, units: UnitPrefs | None = None, telemetry: Telemetry | None = None):
        self.layout = layout
        self.units = units or UnitPrefs()
        self.telemetry = telemetry
        self.cache: dict = {}

    def set_telemetry(self, tel: Telemetry | None) -> None:
        self.telemetry = tel
        self.cache.clear()

    def widget_size_px(self, cfg: WidgetConfig, H: float) -> tuple[float, float, float]:
        """Returns (width, height, unit) in pixels for a frame of height H."""
        w = WIDGETS.get(cfg.type)
        if w is None:
            return 0.0, 0.0, 0.0
        u = H / 100.0 * cfg.scale * self.layout.style.scale
        bw, bh = w.box_size(cfg)
        return bw * u, bh * u, u

    def widget_rect(self, cfg: WidgetConfig, W: float, H: float) -> QRectF:
        ww, hh, _ = self.widget_size_px(cfg, H)
        cx, cy = cfg.x * W, cfg.y * H
        x = min(max(cx - ww / 2, 0.0), max(0.0, W - ww))
        y = min(max(cy - hh / 2, 0.0), max(0.0, H - hh))
        return QRectF(x, y, ww, hh)

    def is_available(self, cfg: WidgetConfig) -> bool:
        w = WIDGETS.get(cfg.type)
        return w is not None and w.available(self.telemetry, cfg)

    def visible_widgets(self) -> list[WidgetConfig]:
        return [c for c in self.layout.widgets if c.enabled and self.is_available(c)]

    def paint(self, p: QPainter, W: float, H: float, t: float) -> None:
        """Paint all enabled widgets into the rectangle (0, 0, W, H) of the painter's coordinates."""
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        ctx = Ctx(self.telemetry, t, self.units, self.layout.style, int(W), int(H), self.cache)
        for cfg in self.visible_widgets():
            widget = WIDGETS[cfg.type]
            r = self.widget_rect(cfg, W, H)
            _, _, u = self.widget_size_px(cfg, H)
            p.save()
            try:
                widget.paint(p, r, u, ctx, cfg)
            finally:
                p.restore()

    def render_image(self, W: int, H: int, t: float, img: QImage | None = None) -> QImage:
        """Render to a transparent premultiplied ARGB image (BGRA byte order on little-endian)."""
        if img is None or img.width() != W or img.height() != H:
            img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        try:
            self.paint(p, W, H, t)
        finally:
            p.end()
        return img
