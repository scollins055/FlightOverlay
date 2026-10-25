"""Overlay widgets drawn with QPainter. Sizes are in units of 1% of frame height."""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen,
                           QPolygonF)

from ..telemetry.channels import CHANNEL_BY_KEY
from ..telemetry.series import Telemetry
from ..telemetry.units import UnitPrefs, convert, fmt_duration, format_value
from .layout import OverlayStyle, WidgetConfig

AL = Qt.AlignmentFlag
LEFT, RIGHT, HCENTER = AL.AlignLeft, AL.AlignRight, AL.AlignHCenter

NAN = float("nan")


# ------------------------------------------------------------------ drawing context

class Ctx:
    """Everything a widget needs to draw one frame."""

    def __init__(self, tel: Telemetry | None, t: float, units: UnitPrefs, style: OverlayStyle,
                 frame_w: int, frame_h: int, cache: dict):
        self.tel = tel
        self.t = t
        self.units = units
        self.style = style
        self.W = frame_w
        self.H = frame_h
        self.cache = cache          # renderer-lifetime cache (tracks, etc.)
        self._vals: dict[str, float] = {}

    def v(self, key: str) -> float:
        if key not in self._vals:
            self._vals[key] = self.tel.value(key, self.t) if self.tel is not None else NAN
        return self._vals[key]

    def has(self, key: str) -> bool:
        return self.tel is not None and self.tel.has(key)

    def fmt(self, key: str) -> tuple[str, str]:
        c = CHANNEL_BY_KEY.get(key)
        q = c.quantity if c else ""
        return format_value(q, self.v(key), self.units)


_font_cache: dict[tuple, tuple[QFont, QFontMetricsF]] = {}


def get_font(family: str, px: float, bold: bool = True) -> tuple[QFont, QFontMetricsF]:
    key = (family, max(1, int(round(px))), bold, threading.get_ident())
    f = _font_cache.get(key)
    if f is None:
        font = QFont(family)
        font.setPixelSize(key[1])
        font.setBold(bold)
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        f = (font, QFontMetricsF(font))
        if len(_font_cache) > 400:
            _font_cache.clear()
        _font_cache[key] = f
    return f


def qcolor(s: str, alpha: float = 1.0) -> QColor:
    c = QColor(s)
    if not c.isValid():
        c = QColor("#ffffff")
    c.setAlphaF(max(0.0, min(1.0, c.alphaF() * alpha)))
    return c


def text_width(ctx: Ctx, s: str, px: float, bold: bool = True) -> float:
    return get_font(ctx.style.font_family, px, bold)[1].horizontalAdvance(s)


_sprite_cache: OrderedDict = OrderedDict()
_SPRITE_MAX = 4000
_sprite_lock = threading.Lock()


def _text_sprite(family: str, px: float, bold: bool, s: str, color: QColor, outline: bool):
    """Outlined text rendered once into a small image and reused (much faster than stroking paths)."""
    key = (family, max(1, int(round(px))), bold, s, color.rgba(), outline)
    with _sprite_lock:
        hit = _sprite_cache.get(key)
        if hit is not None:
            _sprite_cache.move_to_end(key)
            return hit
    font, fm = get_font(family, px, bold)
    ow = max(1.0, px * 0.14) if outline else 0.0
    pad = int(math.ceil(ow)) + 2
    tw = fm.horizontalAdvance(s)
    w = int(math.ceil(tw)) + 2 * pad
    h = int(math.ceil(fm.ascent() + fm.descent())) + 2 * pad
    img = QImage(max(1, w), max(1, h), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    path = QPainterPath()
    path.addText(QPointF(pad, pad + fm.ascent()), font, s)
    ip = QPainter(img)
    ip.setRenderHint(QPainter.RenderHint.Antialiasing)
    if outline:
        pen = QPen(QColor(0, 0, 0, int(210 * color.alphaF())), ow)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        ip.strokePath(path, pen)
    ip.fillPath(path, color)
    ip.end()
    hit = (img, pad, tw, fm.ascent(), fm.descent())
    with _sprite_lock:
        _sprite_cache[key] = hit
        if len(_sprite_cache) > _SPRITE_MAX:
            _sprite_cache.popitem(last=False)
    return hit


def draw_text(p: QPainter, ctx: Ctx, rect: QRectF, s: str, px: float, color: QColor,
              align=LEFT, bold: bool = True) -> float:
    """Draw outlined text vertically centred in rect. Returns the text width."""
    if not s:
        return 0.0
    img, pad, tw, asc, desc = _text_sprite(ctx.style.font_family, px, bold, s, color, ctx.style.outline)
    if align & RIGHT:
        x = rect.right() - tw
    elif align & HCENTER:
        x = rect.center().x() - tw / 2
    else:
        x = rect.left()
    top = rect.center().y() - (asc + desc) / 2
    tr = p.transform()
    if tr.isTranslating() and not tr.isRotating() and not tr.isScaling():
        # pixel-align for crisp text
        dx, dy = tr.dx(), tr.dy()
        p.drawImage(QPointF(round(x - pad + dx) - dx, round(top - pad + dy) - dy), img)
    elif tr.isIdentity():
        p.drawImage(QPointF(round(x - pad), round(top - pad)), img)
    else:
        p.drawImage(QPointF(x - pad, top - pad), img)
    return tw


def draw_panel(p: QPainter, ctx: Ctx, r: QRectF, u: float, opacity: float = 1.0) -> None:
    if not ctx.style.panels:
        return
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, int(255 * ctx.style.panel_opacity * opacity)))
    p.drawRoundedRect(r, u * 1.0, u * 1.0)
    p.restore()


def outlined_line_pen(color: QColor, width: float) -> tuple[QPen, QPen]:
    back = QPen(QColor(0, 0, 0, int(170 * color.alphaF())), width * 2.2)
    back.setCapStyle(Qt.PenCapStyle.RoundCap)
    front = QPen(color, width)
    front.setCapStyle(Qt.PenCapStyle.RoundCap)
    return back, front


def draw_lines_outlined(p: QPainter, lines: list[tuple[QPointF, QPointF]], color: QColor, width: float):
    back, front = outlined_line_pen(color, width)
    p.setPen(back)
    for a, b in lines:
        p.drawLine(a, b)
    p.setPen(front)
    for a, b in lines:
        p.drawLine(a, b)


def arrow_polygon(cx: float, cy: float, size: float, angle_deg: float) -> QPolygonF:
    """Arrow pointing 'up' rotated clockwise by angle_deg."""
    pts = [(0, -1.0), (0.62, 0.75), (0, 0.38), (-0.62, 0.75)]
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    return QPolygonF([QPointF(cx + (x * ca - y * sa) * size, cy + (x * sa + y * ca) * size) for x, y in pts])


MODE_COLORS = {
    "MANUAL": "#e67e22", "STABILIZE": "#e67e22", "ACRO": "#e67e22", "TRAINING": "#e67e22",
    "FBWA": "#27ae60", "FBWB": "#27ae60", "CRUISE": "#16a085", "AUTOTUNE": "#8e44ad",
    "AUTO": "#2980b9", "GUIDED": "#2980b9", "LOITER": "#2c82c9", "CIRCLE": "#2c82c9",
    "TAKEOFF": "#2980b9", "THERMAL": "#2c82c9",
    "RTL": "#c0392b", "AUTOLAND": "#c0392b", "LAND": "#c0392b", "QRTL": "#c0392b", "QLAND": "#c0392b",
    "SMART_RTL": "#c0392b",
}


def mode_color(name: str | None) -> str:
    if not name:
        return "#555555"
    if name in MODE_COLORS:
        return MODE_COLORS[name]
    if name.startswith("Q"):
        return "#8e44ad"
    return "#34495e"


# ------------------------------------------------------------------ widget base

@dataclass
class Widget:
    type: str
    title: str
    group: str
    size: tuple[float, float]
    requires: tuple[str, ...] = ()
    needs_all: bool = False

    def available(self, tel: Telemetry | None, cfg: WidgetConfig) -> bool:
        if tel is None or not self.requires:
            return True
        hits = [tel.has(k) for k in self.requires]
        return all(hits) if self.needs_all else any(hits)

    def missing_reason(self, tel: Telemetry | None, cfg: WidgetConfig) -> str:
        if self.available(tel, cfg):
            return ""
        names = [CHANNEL_BY_KEY[k].label if k in CHANNEL_BY_KEY else k for k in self.requires]
        return "This log has no " + (" / ".join(names)) + " data"

    def box_size(self, cfg: WidgetConfig) -> tuple[float, float]:
        return self.size

    def paint(self, p: QPainter, r: QRectF, u: float, ctx: Ctx, cfg: WidgetConfig) -> None:
        raise NotImplementedError

    def color(self, ctx: Ctx, cfg: WidgetConfig, default: str | None = None) -> QColor:
        return qcolor(cfg.color or default or ctx.style.text_color, cfg.opacity)


# ------------------------------------------------------------------ readouts

class Readout(Widget):
    def available(self, tel, cfg):
        return tel is None or (cfg.channel is not None and tel.has(cfg.channel))

    def missing_reason(self, tel, cfg):
        if self.available(tel, cfg):
            return ""
        c = CHANNEL_BY_KEY.get(cfg.channel or "")
        return f"This log has no {c.label if c else cfg.channel} data"

    def paint(self, p, r, u, ctx, cfg):
        key = cfg.channel or ""
        c = CHANNEL_BY_KEY.get(key)
        label = cfg.options.get("label") or (c.short if c else key)
        if key == "mode":
            val, unit = (ctx.tel.mode_name(ctx.t) if ctx.tel else None) or "--", ""
        else:
            val, unit = ctx.fmt(key)
        draw_panel(p, ctx, r, u, cfg.opacity)
        pad = u * 1.0
        inner = r.adjusted(pad, 0, -pad, 0)
        lab_px, val_px, unit_px = u * 2.3, u * 4.4, u * 2.3
        lab_c = qcolor(ctx.style.label_color, cfg.opacity)
        val_c = self.color(ctx, cfg)
        warn = cfg.options.get("warn_below")
        if warn is not None and val != "--":
            v = ctx.v(key)
            if not math.isnan(v) and v < float(warn):
                val_c = qcolor("#ff5b4f", cfg.opacity)
        lw = text_width(ctx, label, lab_px)
        vw = text_width(ctx, val, val_px)
        uw = text_width(ctx, unit, unit_px) if unit else 0.0
        gap = u * 0.9
        total = lw + gap + vw + (gap * 0.5 + uw if unit else 0)
        align = cfg.options.get("align", "left")
        if align == "right":
            x = inner.right() - total
        elif align == "center":
            x = inner.center().x() - total / 2
        else:
            x = inner.left()
        # Align baselines: draw each part in a rect of the same height.
        fm_v = get_font(ctx.style.font_family, val_px)[1]
        base = r.center().y() + (fm_v.ascent() - fm_v.descent()) / 2
        for txt, px, col, w in ((label, lab_px, lab_c, lw), (val, val_px, val_c, vw), (unit, unit_px, lab_c, uw)):
            if not txt:
                continue
            fm = get_font(ctx.style.font_family, px)[1]
            hh = fm.ascent() - fm.descent()
            rr = QRectF(x, base - hh, w + 2, hh)
            draw_text(p, ctx, rr, txt, px, col)
            x += w + (gap if px == lab_px and txt == label else gap * 0.5)


class ModeBadge(Widget):
    def paint(self, p, r, u, ctx, cfg):
        name = ctx.tel.mode_name(ctx.t) if ctx.tel else None
        armed = ctx.v("armed")
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(mode_color(name), 0.82 * cfg.opacity))
        p.drawRoundedRect(r, u * 1.2, u * 1.2)
        dot_r = u * 1.0
        cx = r.left() + u * 2.4
        if not math.isnan(armed):
            if armed > 0.5:
                p.setBrush(qcolor("#ff3b30", cfg.opacity))
                p.setPen(QPen(qcolor("#ffffff", cfg.opacity), u * 0.25))
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(qcolor("#dddddd", cfg.opacity), u * 0.3))
            p.drawEllipse(QPointF(cx, r.center().y()), dot_r, dot_r)
        p.restore()
        txt_r = r.adjusted(u * 4.0, 0, -u * 1.0, 0)
        draw_text(p, ctx, txt_r, name or "MODE", u * 3.4, self.color(ctx, cfg), HCENTER)


class Timer(Widget):
    def paint(self, p, r, u, ctx, cfg):
        draw_panel(p, ctx, r, u, cfg.opacity)
        col = self.color(ctx, cfg)
        cx, cy, rad = r.left() + u * 2.6, r.center().y(), u * 1.4
        p.save()
        back, front = outlined_line_pen(col, u * 0.35)
        for pen in (back, front):
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), rad, rad)
            p.drawLine(QPointF(cx, cy), QPointF(cx, cy - rad * 0.7))
            p.drawLine(QPointF(cx, cy), QPointF(cx + rad * 0.5, cy))
        p.restore()
        v = ctx.v("flight_time")
        txt = fmt_duration(v) if not math.isnan(v) else "--:--"
        draw_text(p, ctx, r.adjusted(u * 4.8, 0, 0, 0), txt, u * 3.6, col)


class Clock(Widget):
    def paint(self, p, r, u, ctx, cfg):
        draw_panel(p, ctx, r, u, cfg.opacity)
        utc = ctx.tel.utc_at(ctx.t) if ctx.tel else None
        tz = cfg.options.get("tz", "local")
        if utc is None:
            txt, suffix = "--:--:--", ""
        elif tz == "utc":
            txt, suffix = time.strftime("%H:%M:%S", time.gmtime(utc)), "UTC"
        else:
            txt, suffix = time.strftime("%H:%M:%S", time.localtime(utc)), ""
        if cfg.options.get("show_date") and utc is not None:
            st = time.gmtime(utc) if tz == "utc" else time.localtime(utc)
            txt = time.strftime("%Y-%m-%d ", st) + txt
        inner = r.adjusted(u, 0, -u, 0)
        sw = text_width(ctx, suffix, u * 2.2) if suffix else 0
        draw_text(p, ctx, inner.adjusted(0, 0, -(sw + u * 0.8 if suffix else 0), 0), txt, u * 3.4,
                  self.color(ctx, cfg), RIGHT)
        if suffix:
            draw_text(p, ctx, inner, suffix, u * 2.2, qcolor(ctx.style.label_color, cfg.opacity), RIGHT)


class TitleText(Widget):
    def paint(self, p, r, u, ctx, cfg):
        txt = cfg.options.get("text", "My flight")
        if cfg.options.get("panel", True):
            draw_panel(p, ctx, r, u, cfg.opacity)
        draw_text(p, ctx, r, txt, u * 3.6, self.color(ctx, cfg), HCENTER)


class GpsStatus(Widget):
    def paint(self, p, r, u, ctx, cfg):
        draw_panel(p, ctx, r, u, cfg.opacity)
        sats = ctx.v("gps_sats")
        fix = ctx.v("gps_fix")
        hd = ctx.v("gps_hdop")
        col = self.color(ctx, cfg)
        if not math.isnan(fix) and fix < 3:
            col = qcolor("#ff5b4f", cfg.opacity)
        lab = qcolor(ctx.style.label_color, cfg.opacity)
        x = r.left() + u
        x += draw_text(p, ctx, QRectF(x, r.top(), r.width(), r.height()), "SAT", u * 2.3, lab) + u * 0.7
        x += draw_text(p, ctx, QRectF(x, r.top(), r.width(), r.height()),
                       "--" if math.isnan(sats) else f"{int(sats)}", u * 3.4, col) + u * 1.4
        if not math.isnan(hd):
            x += draw_text(p, ctx, QRectF(x, r.top(), r.width(), r.height()), "HDOP", u * 2.0, lab) + u * 0.5
            draw_text(p, ctx, QRectF(x, r.top(), r.width(), r.height()), f"{hd:.1f}", u * 2.6, col)


class Coords(Widget):
    def paint(self, p, r, u, ctx, cfg):
        draw_panel(p, ctx, r, u, cfg.opacity)
        la, lo = ctx.v("lat"), ctx.v("lon")
        txt = "--" if math.isnan(la) else f"{la:.6f}, {lo:.6f}"
        draw_text(p, ctx, r, txt, u * 2.7, self.color(ctx, cfg), HCENTER, bold=False)


class Messages(Widget):
    def paint(self, p, r, u, ctx, cfg):
        if ctx.tel is None:
            return
        hold = float(cfg.options.get("hold", 6.0))
        msg = None
        for t, s in reversed(ctx.tel.messages):
            if t <= ctx.t:
                if ctx.t - t <= hold:
                    msg = s
                break
        if not msg:
            return
        tw = text_width(ctx, msg, u * 2.8)
        bg = QRectF(r.center().x() - tw / 2 - u, r.top(), tw + 2 * u, r.height())
        draw_panel(p, ctx, bg, u, cfg.opacity)
        draw_text(p, ctx, bg, msg, u * 2.8, qcolor(cfg.color or "#ffe066", cfg.opacity), HCENTER)


# ------------------------------------------------------------------ attitude

class HeadingTape(Widget):
    def paint(self, p, r, u, ctx, cfg):
        hdg = ctx.v("yaw")
        draw_panel(p, ctx, r, u, cfg.opacity)
        col = qcolor(cfg.color or ctx.style.text_color, cfg.opacity)
        span = float(cfg.options.get("span", 90.0))
        ppd = r.width() / span
        cx = r.center().x()
        base_y = r.bottom() - u * 0.6
        p.save()
        p.setClipRect(r)
        lines = []
        if not math.isnan(hdg):
            d0 = int(math.floor((hdg - span / 2) / 5.0) * 5)
            d1 = int(math.ceil((hdg + span / 2) / 5.0) * 5)
            for d in range(d0, d1 + 1, 5):
                x = cx + (d - hdg) * ppd
                long = d % 10 == 0
                lines.append((QPointF(x, base_y), QPointF(x, base_y - (u * 1.6 if long else u * 0.9))))
                if d % 30 == 0 and abs(x - cx) > u * 6.5:
                    dd = d % 360
                    lab = {0: "N", 90: "E", 180: "S", 270: "W"}.get(dd, f"{dd // 10:02d}")
                    big = dd % 90 == 0
                    lc = qcolor("#ffd166", cfg.opacity) if big else col
                    draw_text(p, ctx, QRectF(x - u * 4, r.top() + u * 0.6, u * 8, u * 3.2), lab,
                              u * (3.0 if big else 2.5), lc, HCENTER)
            draw_lines_outlined(p, lines, col, u * 0.28)
            # home direction marker
            bh = ctx.v("bearing_home")
            if not math.isnan(bh) and cfg.options.get("home_marker", True):
                rel = (bh - hdg + 540) % 360 - 180
                x = cx + max(-span / 2 + 2, min(span / 2 - 2, rel)) * ppd
                hr = QRectF(x - u * 1.4, base_y - u * 4.6, u * 2.8, u * 2.6)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(qcolor("#ff7b72", 0.9 * cfg.opacity))
                p.drawRoundedRect(hr, u * 0.5, u * 0.5)
                draw_text(p, ctx, hr, "H", u * 2.2, qcolor("#ffffff", cfg.opacity), HCENTER)
        p.restore()
        # centre box with numeric heading
        txt = "---" if math.isnan(hdg) else f"{int(round(hdg)) % 360:03d}"
        bw = u * 8.5
        box = QRectF(cx - bw / 2, r.top() - u * 0.2, bw, r.height() * 0.62)
        p.save()
        p.setPen(QPen(col, u * 0.25))
        p.setBrush(QColor(0, 0, 0, 200))
        p.drawRoundedRect(box, u * 0.6, u * 0.6)
        tri = QPolygonF([QPointF(cx - u * 0.9, box.bottom()), QPointF(cx + u * 0.9, box.bottom()),
                         QPointF(cx, box.bottom() + u * 1.1)])
        p.setBrush(col)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(tri)
        p.restore()
        draw_text(p, ctx, box, txt + "°", u * 3.3, col, HCENTER)


class HudHorizon(Widget):
    def paint(self, p, r, u, ctx, cfg):
        roll, pitch = ctx.v("roll"), ctx.v("pitch")
        col = qcolor(cfg.color or ctx.style.hud_color, cfg.opacity)
        cx, cy = r.center().x(), r.center().y()
        w, h = r.width(), r.height()
        ppd = h / float(cfg.options.get("fov", 60.0))
        lw = u * 0.32
        p.save()
        p.setClipRect(r)
        if not math.isnan(roll) and not math.isnan(pitch):
            p.translate(cx, cy)
            p.rotate(-roll)
            p.translate(0, pitch * ppd)
            g = w * 0.07
            lines = [(QPointF(-w * 0.46, 0), QPointF(-g, 0)), (QPointF(g, 0), QPointF(w * 0.46, 0))]
            draw_lines_outlined(p, lines, col, lw)
            ladder = []
            labels = []
            for deg in range(-60, 61, 10):
                if deg == 0:
                    continue
                y = -deg * ppd
                if abs(y + pitch * ppd) > h * 0.48:
                    continue
                L, gap = w * 0.11, w * 0.05
                tick = u * 1.0 * (1 if deg > 0 else -1)
                if deg > 0:
                    ladder += [(QPointF(-gap - L, y), QPointF(-gap, y)), (QPointF(gap, y), QPointF(gap + L, y)),
                               (QPointF(-gap - L, y), QPointF(-gap - L, y + tick)),
                               (QPointF(gap + L, y), QPointF(gap + L, y + tick))]
                else:
                    seg = L / 5
                    for k in range(0, 5, 2):
                        ladder.append((QPointF(-gap - L + k * seg, y), QPointF(-gap - L + (k + 1) * seg, y)))
                        ladder.append((QPointF(gap + k * seg, y), QPointF(gap + (k + 1) * seg, y)))
                    ladder += [(QPointF(-gap - L, y), QPointF(-gap - L, y + tick)),
                               (QPointF(gap + L, y), QPointF(gap + L, y + tick))]
                labels.append((y, str(abs(deg))))
            draw_lines_outlined(p, ladder, col, lw * 0.8)
            for y, s in labels:
                draw_text(p, ctx, QRectF(-w * 0.05 - w * 0.11 - u * 5, y - u * 1.5, u * 4.5, u * 3), s, u * 2.2,
                          col, RIGHT)
                draw_text(p, ctx, QRectF(w * 0.05 + w * 0.11 + u * 0.6, y - u * 1.5, u * 4.5, u * 3), s, u * 2.2,
                          col, LEFT)
            p.resetTransform()
        p.restore()
        # fixed aircraft symbol
        s = u * 2.2
        sym = [(QPointF(cx - s * 3.2, cy), QPointF(cx - s * 1.2, cy)),
               (QPointF(cx - s * 1.2, cy), QPointF(cx - s * 0.6, cy + s * 0.7)),
               (QPointF(cx - s * 0.6, cy + s * 0.7), QPointF(cx, cy)),
               (QPointF(cx, cy), QPointF(cx + s * 0.6, cy + s * 0.7)),
               (QPointF(cx + s * 0.6, cy + s * 0.7), QPointF(cx + s * 1.2, cy)),
               (QPointF(cx + s * 1.2, cy), QPointF(cx + s * 3.2, cy))]
        draw_lines_outlined(p, sym, qcolor("#ffd166", cfg.opacity), lw * 1.2)
        # roll scale
        if cfg.options.get("roll_scale", True):
            R = h * 0.42
            ticks = []
            for a in (-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60):
                ang = math.radians(a - 90)
                L = u * (1.8 if a % 30 == 0 else 1.0)
                ticks.append((QPointF(cx + R * math.cos(ang), cy + R * math.sin(ang)),
                              QPointF(cx + (R + L) * math.cos(ang), cy + (R + L) * math.sin(ang))))
            draw_lines_outlined(p, ticks, col, lw * 0.8)
            if not math.isnan(roll):
                ang = math.radians(-roll - 90)
                tip = QPointF(cx + (R - u * 0.3) * math.cos(ang), cy + (R - u * 0.3) * math.sin(ang))
                pa = math.radians(-roll)
                ca, sa = math.cos(pa), math.sin(pa)

                def rot(x, y):
                    return QPointF(tip.x() + x * ca - y * sa, tip.y() + x * sa + y * ca)
                tri = QPolygonF([tip, rot(-u * 1.0, u * 1.8), rot(u * 1.0, u * 1.8)])
                p.save()
                p.setPen(QPen(QColor(0, 0, 0, 170), u * 0.3))
                p.setBrush(col)
                p.drawPolygon(tri)
                p.restore()


class AttitudeIndicator(Widget):
    def paint(self, p, r, u, ctx, cfg):
        roll, pitch = ctx.v("roll"), ctx.v("pitch")
        R = min(r.width(), r.height()) / 2 - u * 0.6
        c = r.center()
        ppd = R / 32.0
        op = cfg.opacity
        p.save()
        clip = QPainterPath()
        clip.addEllipse(c, R, R)
        p.setClipPath(clip)
        p.translate(c)
        rr = 0.0 if math.isnan(roll) else roll
        pp = 0.0 if math.isnan(pitch) else pitch
        p.rotate(-rr)
        p.translate(0, pp * ppd)
        big = R * 4
        p.fillRect(QRectF(-big, -big, 2 * big, big), qcolor("#3b7dd8", op))
        p.fillRect(QRectF(-big, 0, 2 * big, big), qcolor("#8a5a2b", op))
        white = qcolor("#ffffff", op)
        p.setPen(QPen(white, u * 0.35))
        p.drawLine(QPointF(-big, 0), QPointF(big, 0))
        p.setPen(QPen(white, u * 0.22))
        for deg in range(-30, 31, 5):
            if deg == 0:
                continue
            y = -deg * ppd
            L = R * (0.32 if deg % 10 == 0 else 0.15)
            p.drawLine(QPointF(-L, y), QPointF(L, y))
            if deg % 10 == 0:
                draw_text(p, ctx, QRectF(L + u * 0.5, y - u * 1.3, u * 4, u * 2.6), str(abs(deg)), u * 1.8, white)
        p.resetTransform()
        p.restore()
        # bezel + roll scale
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcolor("#202020", op), u * 1.1))
        p.drawEllipse(c, R, R)
        p.setPen(QPen(qcolor("#ffffff", op), u * 0.25))
        for a in (-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60):
            ang = math.radians(a - 90)
            L = u * (1.6 if a % 30 == 0 else 0.9)
            p.drawLine(QPointF(c.x() + (R - L) * math.cos(ang), c.y() + (R - L) * math.sin(ang)),
                       QPointF(c.x() + R * math.cos(ang), c.y() + R * math.sin(ang)))
        # roll pointer
        ang = math.radians(-rr - 90)
        tip = QPointF(c.x() + (R - u * 1.8) * math.cos(ang), c.y() + (R - u * 1.8) * math.sin(ang))
        p.setBrush(qcolor("#ffd166", op))
        p.setPen(Qt.PenStyle.NoPen)
        pa = math.radians(-rr)
        ca, sa = math.cos(pa), math.sin(pa)
        p.drawPolygon(QPolygonF([tip, QPointF(tip.x() + (-u) * ca - (u * 1.6) * sa, tip.y() + (-u) * sa + (u * 1.6) * ca),
                                 QPointF(tip.x() + u * ca - (u * 1.6) * sa, tip.y() + u * sa + (u * 1.6) * ca)]))
        # aircraft symbol
        sym_pen = QPen(qcolor("#ffd166", op), u * 0.6)
        sym_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(sym_pen)
        p.drawLine(QPointF(c.x() - R * 0.55, c.y()), QPointF(c.x() - R * 0.18, c.y()))
        p.drawLine(QPointF(c.x() + R * 0.18, c.y()), QPointF(c.x() + R * 0.55, c.y()))
        p.drawLine(QPointF(c.x() - R * 0.18, c.y()), QPointF(c.x(), c.y() + R * 0.12))
        p.drawLine(QPointF(c.x() + R * 0.18, c.y()), QPointF(c.x(), c.y() + R * 0.12))
        p.setBrush(qcolor("#ffd166", op))
        p.drawEllipse(c, u * 0.5, u * 0.5)
        p.restore()


# ------------------------------------------------------------------ tapes

class Tape(Widget):
    """Vertical HUD tape for speed (left side) or altitude (right side)."""

    def available(self, tel, cfg):
        if tel is None:
            return True
        return any(tel.has(k) for k in self._channels(cfg))

    def _channels(self, cfg):
        ch = cfg.channel
        if ch:
            return [ch] if self.type != "speedtape" else [ch, "groundspeed"]
        return ["airspeed", "groundspeed"] if self.type == "speedtape" else ["alt_rel"]

    def paint(self, p, r, u, ctx, cfg):
        key = next((k for k in self._channels(cfg) if ctx.has(k)), self._channels(cfg)[0])
        c = CHANNEL_BY_KEY[key]
        v_si = ctx.v(key)
        val, unit = convert(c.quantity, 0.0 if math.isnan(v_si) else v_si, ctx.units)
        if c.quantity == "distance":
            unit = "m"
        col = qcolor(cfg.color or ctx.style.hud_color, cfg.opacity)
        right = self.type == "alttape"
        spans = {"km/h": (60, 5, 10), "m/s": (20, 1, 5), "mph": (40, 5, 10), "kn": (40, 5, 10),
                 "m": (100, 5, 20), "ft": (300, 10, 50)}
        span, minor, major = spans.get(unit, (60, 5, 10))
        ppv = r.height() / span
        cy = r.center().y()
        edge_x = r.left() if right else r.right()
        sgn = 1 if right else -1  # tick direction
        p.save()
        p.setClipRect(r.adjusted(-u, -u, u, u))
        lines = []
        if not math.isnan(v_si):
            v0 = int(math.floor((val - span / 2) / minor) * minor)
            v1 = int(math.ceil((val + span / 2) / minor) * minor)
            for vv in range(v0, v1 + 1, minor):
                if c.quantity == "speed" and vv < 0:
                    continue
                y = cy - (vv - val) * ppv
                if y < r.top() or y > r.bottom():
                    continue
                L = u * (1.6 if vv % major == 0 else 0.8)
                lines.append((QPointF(edge_x, y), QPointF(edge_x + sgn * L, y)))
                if vv % major == 0 and abs(y - cy) > u * 3:
                    tr = QRectF(edge_x + sgn * u * 2.2 - (0 if right else u * 8), y - u * 1.4, u * 8, u * 2.8)
                    draw_text(p, ctx, tr, str(vv), u * 2.3, col, LEFT if right else RIGHT)
        lines.append((QPointF(edge_x, r.top()), QPointF(edge_x, r.bottom())))
        draw_lines_outlined(p, lines, col, u * 0.25)
        p.restore()
        # value box
        txt = "--" if math.isnan(v_si) else (f"{val:.0f}" if unit != "m/s" else f"{val:.1f}")
        bw, bh = r.width() * 0.95, u * 4.6
        bx = r.left() + u * 1.2 if right else r.right() - u * 1.2 - bw
        box = QRectF(bx, cy - bh / 2, bw, bh)
        p.save()
        p.setPen(QPen(col, u * 0.3))
        p.setBrush(QColor(0, 0, 0, 200))
        p.drawRoundedRect(box, u * 0.6, u * 0.6)
        p.restore()
        draw_text(p, ctx, box, txt, u * 3.5, qcolor(ctx.style.text_color, cfg.opacity), HCENTER)
        lab = (c.short + " " + unit).strip()
        lr = QRectF(r.left() - u * 4, r.top() - u * 3.8, r.width() + u * 8, u * 3)
        draw_text(p, ctx, lr, lab, u * 2.2, qcolor(ctx.style.label_color, cfg.opacity), HCENTER)


# ------------------------------------------------------------------ navigation

class MiniMap(Widget):
    def _track(self, ctx: Ctx):
        key = ("track", id(ctx.tel))
        tr = ctx.cache.get(key)
        if tr is None:
            lat_s, lon_s = ctx.tel.channels["lat"], ctx.tel.channels["lon"]
            t = lat_s.t
            lat = lat_s.v
            lon = lon_s.v if lon_s.t.size == t.size else lon_s.at_many(t)
            step = max(1, t.size // 3000)
            t, lat, lon = t[::step], lat[::step], lon[::step]
            lat0 = float(np.mean(lat))
            lon0 = float(np.mean(lon))
            k = 6371008.8 * math.pi / 180
            x = (lon - lon0) * k * math.cos(math.radians(lat0))
            y = (lat - lat0) * k
            tr = {"t": t, "x": x, "y": y, "lat0": lat0, "lon0": lon0, "k": k,
                  "bbox": (float(x.min()), float(x.max()), float(y.min()), float(y.max()))}
            ctx.cache[key] = tr
        return tr

    def _background(self, p, r, u, ctx, cfg):
        p.save()
        p.setPen(QPen(qcolor("#ffffff", 0.35 * cfg.opacity), u * 0.2))
        p.setBrush(QColor(10, 20, 30, int(255 * max(ctx.style.panel_opacity, 0.45) * cfg.opacity)))
        p.drawRoundedRect(r, u * 1.2, u * 1.2)
        p.restore()
        draw_text(p, ctx, QRectF(r.right() - u * 3.2, r.top() + u * 0.4, u * 2.6, u * 2.6), "N", u * 2.0,
                  qcolor(ctx.style.label_color, cfg.opacity), HCENTER)

    def paint(self, p, r, u, ctx, cfg):
        if ctx.tel is None or not (ctx.has("lat") and ctx.has("lon")):
            self._background(p, r, u, ctx, cfg)
            draw_text(p, ctx, r, "MAP", u * 2.6, qcolor("#888888", cfg.opacity), HCENTER)
            return
        tr = self._track(ctx)
        k, lat0, lon0 = tr["k"], tr["lat0"], tr["lon0"]
        coslat = math.cos(math.radians(lat0))
        la, lo = ctx.v("lat"), ctx.v("lon")
        follow = cfg.options.get("mode", "fit") == "follow"
        # Work in coordinates local to the widget so the static layer can be cached.
        lr = QRectF(0, 0, r.width(), r.height())
        inner = lr.adjusted(u * 1.6, u * 1.6, -u * 1.6, -u * 1.6)
        if follow and not math.isnan(la):
            radius = float(cfg.options.get("radius_m", 400.0))
            cxm = (lo - lon0) * k * coslat
            cym = (la - lat0) * k
            scale = min(inner.width(), inner.height()) / (2 * radius)
        else:
            follow = False
            x0, x1, y0, y1 = tr["bbox"]
            if tel_home := ctx.tel.home:
                hx = (tel_home[1] - lon0) * k * coslat
                hy = (tel_home[0] - lat0) * k
                x0, x1, y0, y1 = min(x0, hx), max(x1, hx), min(y0, hy), max(y1, hy)
            cxm, cym = (x0 + x1) / 2, (y0 + y1) / 2
            ext = max(x1 - x0, y1 - y0, 50.0)
            scale = min(inner.width(), inner.height()) / ext
        mx, my = inner.center().x(), inner.center().y()

        def to_px(x, y):
            return mx + (x - cxm) * scale, my - (y - cym) * scale

        st = ctx.style
        skey = ("mm", id(ctx.tel), int(r.width()), int(r.height()), round(u, 3), cfg.opacity, st.panel_opacity,
                st.label_color, st.outline, st.font_family)
        cached = None if follow else ctx.cache.get(skey)
        if cached is None:
            px, py = to_px(tr["x"], tr["y"])
            pts = [QPointF(a, b) for a, b in zip(px.tolist(), py.tolist())]
            img = QImage(max(1, int(math.ceil(r.width()))), max(1, int(math.ceil(r.height()))),
                         QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(0)
            ip = QPainter(img)
            ip.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._background(ip, lr, u, ctx, cfg)
            ip.setClipRect(lr.adjusted(u * 0.3, u * 0.3, -u * 0.3, -u * 0.3))
            pen = QPen(qcolor("#ffffff", 0.28 * cfg.opacity), u * 0.3)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            ip.setPen(pen)
            ip.drawPolyline(QPolygonF(pts))
            ip.end()
            cached = (img, pts)
            if not follow:
                ctx.cache[skey] = cached
        img, pts = cached
        n_now = int(np.searchsorted(tr["t"], ctx.t))
        p.save()
        p.translate(r.topLeft())
        p.drawImage(QPointF(0, 0), img)
        p.setClipRect(lr.adjusted(u * 0.3, u * 0.3, -u * 0.3, -u * 0.3))
        if n_now > 1:
            pen = QPen(qcolor(cfg.color or ctx.style.hud_color, cfg.opacity), u * 0.45)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline(QPolygonF(pts[:n_now]))
        if ctx.tel.home:
            hx, hy = to_px((ctx.tel.home[1] - lon0) * k * coslat, (ctx.tel.home[0] - lat0) * k)
            hr = QRectF(hx - u * 1.2, hy - u * 1.2, u * 2.4, u * 2.4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor("#ff7b72", cfg.opacity))
            p.drawEllipse(hr)
            draw_text(p, ctx, hr, "H", u * 1.7, qcolor("#ffffff", cfg.opacity), HCENTER)
        if not math.isnan(la):
            ax, ay = to_px((lo - lon0) * k * coslat, (la - lat0) * k)
            hdg = ctx.v("yaw")
            poly = arrow_polygon(ax, ay, u * 1.9, 0.0 if math.isnan(hdg) else hdg)
            p.setPen(QPen(QColor(0, 0, 0, 200), u * 0.3))
            p.setBrush(qcolor("#ffd166", cfg.opacity))
            p.drawPolygon(poly)
        p.restore()


class HomeArrow(Widget):
    def paint(self, p, r, u, ctx, cfg):
        draw_panel(p, ctx, r, u, cfg.opacity)
        brg, hdg = ctx.v("bearing_home"), ctx.v("yaw")
        dist = ctx.v("distance_home")
        col = self.color(ctx, cfg)
        ar = QRectF(r.left(), r.top(), r.width(), r.height() * 0.62)
        if not (math.isnan(brg) or math.isnan(hdg)) and (math.isnan(dist) or dist > 3):
            poly = arrow_polygon(ar.center().x(), ar.center().y() + u * 0.3, u * 3.0, brg - hdg)
            p.save()
            p.setPen(QPen(QColor(0, 0, 0, 200), u * 0.35))
            p.setBrush(qcolor(cfg.color or "#ff7b72", cfg.opacity))
            p.drawPolygon(poly)
            p.restore()
        else:
            draw_text(p, ctx, ar, "H", u * 3.6, qcolor("#ff7b72", cfg.opacity), HCENTER)
        txt, unit = ctx.fmt("distance_home")
        draw_text(p, ctx, QRectF(r.left(), r.top() + r.height() * 0.6, r.width(), r.height() * 0.36),
                  f"{txt} {unit}".strip(), u * 2.8, col, HCENTER)


class WindArrow(Widget):
    def paint(self, p, r, u, ctx, cfg):
        draw_panel(p, ctx, r, u, cfg.opacity)
        wd, hdg = ctx.v("wind_dir"), ctx.v("yaw")
        col = self.color(ctx, cfg)
        ar = QRectF(r.left(), r.top(), r.width(), r.height() * 0.62)
        if not math.isnan(wd):
            rel = (wd + 180.0) - (0.0 if math.isnan(hdg) or not cfg.options.get("relative", True) else hdg)
            poly = arrow_polygon(ar.center().x(), ar.center().y() + u * 0.3, u * 2.8, rel)
            p.save()
            p.setPen(QPen(QColor(0, 0, 0, 200), u * 0.35))
            p.setBrush(qcolor(cfg.color or "#9fd8ff", cfg.opacity))
            p.drawPolygon(poly)
            p.restore()
        txt, unit = ctx.fmt("wind_speed")
        draw_text(p, ctx, QRectF(r.left(), r.top() + r.height() * 0.6, r.width(), r.height() * 0.36),
                  f"W {txt} {unit}".strip(), u * 2.5, col, HCENTER)


# ------------------------------------------------------------------ power

class Battery(Widget):
    def paint(self, p, r, u, ctx, cfg):
        draw_panel(p, ctx, r, u, cfg.opacity)
        col = self.color(ctx, cfg)
        lab = qcolor(ctx.style.label_color, cfg.opacity)
        pad = u * 1.0
        row1 = QRectF(r.left() + pad, r.top() + u * 0.4, r.width() - 2 * pad, r.height() * 0.48)
        v, _ = ctx.fmt("batt_voltage")
        x = row1.left()
        x += draw_text(p, ctx, row1, v, u * 4.4, col) + u * 0.4
        draw_text(p, ctx, QRectF(x, row1.top(), u * 3, row1.height()), "V", u * 2.4, lab)
        pct = ctx.v("batt_remaining")
        if not math.isnan(pct):
            pc = "#4cd964" if pct > 40 else ("#ffcc00" if pct > 20 else "#ff3b30")
            draw_text(p, ctx, row1, f"{pct:.0f}%", u * 3.2, qcolor(pc, cfg.opacity), RIGHT)
        row2 = QRectF(r.left() + pad, row1.bottom(), r.width() - 2 * pad, r.height() * 0.3)
        a, _ = ctx.fmt("batt_current")
        mah, _ = ctx.fmt("batt_used")
        draw_text(p, ctx, row2, f"{a} A", u * 2.6, col)
        draw_text(p, ctx, row2, f"{mah} mAh", u * 2.6, col, RIGHT)
        if not math.isnan(pct):
            bar = QRectF(r.left() + pad, r.bottom() - u * 1.9, r.width() - 2 * pad, u * 1.0)
            p.save()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, int(60 * cfg.opacity)))
            p.drawRoundedRect(bar, u * 0.4, u * 0.4)
            pc = "#4cd964" if pct > 40 else ("#ffcc00" if pct > 20 else "#ff3b30")
            p.setBrush(qcolor(pc, cfg.opacity))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * max(0.0, min(1.0, pct / 100)),
                                     bar.height()), u * 0.4, u * 0.4)
            p.restore()


class ThrottleBar(Widget):
    def paint(self, p, r, u, ctx, cfg):
        thr = ctx.v("throttle")
        col = qcolor(cfg.color or ctx.style.hud_color, cfg.opacity)
        lab = qcolor(ctx.style.label_color, cfg.opacity)
        top_r = QRectF(r.left() - u * 2, r.top(), r.width() + u * 4, u * 3)
        draw_text(p, ctx, top_r, "THR", u * 2.1, lab, HCENTER)
        bar = QRectF(r.center().x() - u * 1.4, r.top() + u * 3.4, u * 2.8, r.height() - u * 7.4)
        p.save()
        p.setPen(QPen(QColor(0, 0, 0, int(180 * cfg.opacity)), u * 0.5))
        p.setBrush(QColor(0, 0, 0, int(90 * cfg.opacity)))
        p.drawRect(bar)
        p.setPen(QPen(col, u * 0.25))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(bar)
        if not math.isnan(thr):
            f = max(0.0, min(1.0, thr / 100.0))
            fill = QRectF(bar.left(), bar.bottom() - bar.height() * f, bar.width(), bar.height() * f)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRect(fill)
        p.restore()
        txt = "--" if math.isnan(thr) else f"{thr:.0f}%"
        draw_text(p, ctx, QRectF(r.left() - u * 3, r.bottom() - u * 3.4, r.width() + u * 6, u * 3.4), txt,
                  u * 2.5, self.color(ctx, cfg), HCENTER)


class Graph(Widget):
    def available(self, tel, cfg):
        return tel is None or tel.has(cfg.channel or "alt_rel")

    def paint(self, p, r, u, ctx, cfg):
        key = cfg.channel or "alt_rel"
        c = CHANNEL_BY_KEY.get(key)
        draw_panel(p, ctx, r, u, cfg.opacity)
        col = qcolor(cfg.color or ctx.style.hud_color, cfg.opacity)
        lab = qcolor(ctx.style.label_color, cfg.opacity)
        txt, unit = ctx.fmt(key)
        head = QRectF(r.left() + u, r.top() + u * 0.3, r.width() - 2 * u, u * 3.2)
        draw_text(p, ctx, head, c.short if c else key, u * 2.2, lab)
        draw_text(p, ctx, head, f"{txt} {unit}".strip(), u * 2.8, qcolor(ctx.style.text_color, cfg.opacity), RIGHT)
        if ctx.tel is None or not ctx.has(key):
            return
        win = float(cfg.options.get("window", 60.0))
        s = ctx.tel.channels[key]
        tt, vv = s.window(ctx.t - win, ctx.t)
        if tt.size < 2:
            return
        if tt.size > 400:
            step = tt.size // 400
            tt, vv = tt[::step], vv[::step]
        lo, hi = s.min_max() if cfg.options.get("fixed_range", True) else (float(vv.min()), float(vv.max()))
        if hi - lo < 1e-6:
            hi = lo + 1.0
        area = QRectF(r.left() + u, head.bottom() + u * 0.3, r.width() - 2 * u, r.bottom() - head.bottom() - u * 1.0)
        xs = area.left() + (tt - (ctx.t - win)) / win * area.width()
        ys = area.bottom() - (vv - lo) / (hi - lo) * area.height()
        poly = QPolygonF([QPointF(a, b) for a, b in zip(xs, ys)])
        p.save()
        pen = QPen(col, u * 0.35)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPolyline(poly)
        p.setBrush(col)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(float(xs[-1]), float(ys[-1])), u * 0.6, u * 0.6)
        p.restore()


# ------------------------------------------------------------------ registry

READOUT = Readout("readout", "Value", "Flight", (24.0, 6.0))

WIDGETS: dict[str, Widget] = {w.type: w for w in [
    READOUT,
    ModeBadge("mode", "Flight mode badge", "Flight", (22.0, 6.0), ("mode",)),
    Tape("speedtape", "Speed tape (HUD)", "Flight", (11.0, 40.0), ("airspeed", "groundspeed")),
    Tape("alttape", "Altitude tape (HUD)", "Flight", (11.0, 40.0), ("alt_rel",)),
    ThrottleBar("throttle", "Throttle bar", "Flight", (6.0, 24.0), ("throttle",)),
    Graph("graph", "Altitude graph", "Flight", (30.0, 12.0), ("alt_rel",)),
    AttitudeIndicator("horizon", "Artificial horizon (instrument)", "Attitude", (26.0, 26.0), ("roll", "pitch"), True),
    HudHorizon("hud_horizon", "Artificial horizon (HUD lines)", "Attitude", (70.0, 56.0), ("roll", "pitch"), True),
    HeadingTape("heading", "Heading tape", "Attitude", (60.0, 7.5), ("yaw",)),
    MiniMap("minimap", "Mini-map (GPS track)", "Navigation", (28.0, 28.0), ("lat", "lon"), True),
    HomeArrow("home", "Home arrow + distance", "Navigation", (15.0, 13.0), ("bearing_home",)),
    GpsStatus("gps", "GPS satellites", "Navigation", (22.0, 5.5), ("gps_sats",)),
    Coords("coords", "GPS coordinates", "Navigation", (38.0, 5.0), ("lat",)),
    Battery("battery", "Battery panel", "Power", (27.0, 11.5), ("batt_voltage",)),
    WindArrow("wind", "Wind arrow", "Environment", (15.0, 13.0), ("wind_dir",)),
    Timer("timer", "Flight timer", "Time", (17.0, 5.5), ("flight_time",)),
    Clock("clock", "Clock (time of day)", "Time", (21.0, 5.5)),
    Messages("messages", "Autopilot messages", "Other", (60.0, 4.6)),
    TitleText("title", "Title text", "Other", (50.0, 6.5)),
]}
