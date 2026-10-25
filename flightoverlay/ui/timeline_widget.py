"""Timeline: log graph with flight modes, and camera tracks where clips are dragged into sync."""
from __future__ import annotations

import math
import os

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QMenu, QScrollBar, QToolButton,
                               QVBoxLayout, QWidget)

from ..core.project import TRACK_NAMES
from ..overlay.widgets import mode_color
from ..telemetry.channels import CHANNEL_BY_KEY, CHANNELS
from ..telemetry.units import fmt_duration

HEADER_W = 78
RULER_H = 24
LOG_H = 78
TRACK_H = 50
GAP = 3
SNAP_PX = 8
EDGE_PX = 6
GRAPH_COLORS = [QColor("#ffb74d"), QColor("#4fc3f7"), QColor("#c5e1a5")]
TRACK_COLORS = [QColor("#3d6fb6"), QColor("#3a9a6b")]


def fmt_time(t: float, step: float = 1.0) -> str:
    sign = "-" if t < 0 else ""
    t = abs(t)
    if step < 1.0:
        m, s = divmod(t, 60)
        return f"{sign}{int(m)}:{s:04.1f}"
    s = int(round(t))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{sign}{h}:{m:02d}:{s:02d}" if h else f"{sign}{m}:{s:02d}"


class TimelineWidget(QWidget):
    viewChanged = Signal()

    def __init__(self, state, playback, parent=None):
        super().__init__(parent)
        self.state = state
        self.playback = playback
        self.setMinimumHeight(RULER_H + LOG_H + 2 * TRACK_H + 4 * GAP + 6)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.t0 = 0.0
        self.pps = 4.0
        self.snap = True
        self.drag: dict | None = None
        self._hover_clip = None
        state.clipsChanged.connect(self.update)
        state.playheadChanged.connect(self._on_playhead)
        state.telemetryChanged.connect(self.fit_all)
        state.projectReset.connect(self.fit_all)
        state.rangeChanged.connect(self.update)
        state.syncMarksChanged.connect(self.update)
        state.selectionChanged.connect(lambda _c: self.update())

    # ---------------------------------------------------------------- geometry

    def row_rect(self, row) -> QRectF:
        w = self.width()
        if row == "ruler":
            return QRectF(0, 0, w, RULER_H)
        if row == "log":
            return QRectF(0, RULER_H + GAP, w, LOG_H)
        y = RULER_H + GAP + LOG_H + GAP + row * (TRACK_H + GAP)
        return QRectF(0, y, w, TRACK_H)

    def row_at(self, y: float):
        for row in ("ruler", "log", 0, 1):
            r = self.row_rect(row)
            if r.top() - GAP / 2 <= y < r.bottom() + GAP / 2:
                return row
        return None

    def x_of(self, t: float) -> float:
        return HEADER_W + (t - self.t0) * self.pps

    def t_of(self, x: float) -> float:
        return self.t0 + (x - HEADER_W) / self.pps

    @property
    def content_w(self) -> float:
        return max(10.0, self.width() - HEADER_W)

    def visible_range(self) -> tuple[float, float]:
        return self.t0, self.t0 + self.content_w / self.pps

    def fit_all(self) -> None:
        lo, hi = self.state.extent()
        span = max(1.0, hi - lo)
        self.pps = self.content_w / (span * 1.04)
        self.t0 = lo - span * 0.02
        self.viewChanged.emit()
        self.update()

    def zoom(self, factor: float, anchor_x: float | None = None) -> None:
        ax = anchor_x if anchor_x is not None else HEADER_W + self.content_w / 2
        t_anchor = self.t_of(ax)
        self.pps = max(0.02, min(2000.0, self.pps * factor))
        self.t0 = t_anchor - (ax - HEADER_W) / self.pps
        self.viewChanged.emit()
        self.update()

    def set_t0(self, t0: float) -> None:
        self.t0 = t0
        self.viewChanged.emit()
        self.update()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.viewChanged.emit()

    def _on_playhead(self, t: float) -> None:
        if self.playback.playing:
            a, b = self.visible_range()
            if t > b - (b - a) * 0.05 or t < a:
                self.set_t0(t - (b - a) * 0.1)
        self.update()

    # ---------------------------------------------------------------- painting

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(30, 31, 36))
        small = QFont(self.font())
        small.setPointSizeF(max(7.5, self.font().pointSizeF() - 1))
        p.setFont(small)
        self._paint_ruler(p)
        self._paint_log(p)
        for tr in (0, 1):
            self._paint_track(p, tr)
        self._paint_range(p)
        self._paint_marks(p)
        self._paint_playhead(p)
        # header labels on top
        p.fillRect(QRectF(0, 0, HEADER_W - 2, self.height()), QColor(36, 37, 43))
        p.setPen(QColor(200, 200, 210))
        for row, label in (("log", "Flight log"), (0, TRACK_NAMES[0]), (1, TRACK_NAMES[1])):
            r = self.row_rect(row)
            p.drawText(QRectF(8, r.top(), HEADER_W - 12, r.height()), Qt.AlignmentFlag.AlignVCenter, label)
        p.setPen(QColor(150, 150, 160))
        p.drawText(QRectF(8, 0, HEADER_W - 12, RULER_H), Qt.AlignmentFlag.AlignVCenter,
                   fmt_time(self.state.playhead, 0.1))
        p.end()

    def _tick_step(self) -> float:
        for s in (0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600):
            if s * self.pps >= 70:
                return float(s)
        return 7200.0

    def _paint_ruler(self, p: QPainter) -> None:
        r = self.row_rect("ruler")
        p.fillRect(r, QColor(40, 41, 48))
        step = self._tick_step()
        minor = step / 5 if step >= 1 else step / 2
        a, b = self.visible_range()
        p.setPen(QColor(120, 120, 130))
        k = math.floor(a / minor)
        while k * minor <= b:
            t = k * minor
            x = self.x_of(t)
            major = abs(t / step - round(t / step)) < 1e-6
            p.drawLine(QPointF(x, r.bottom()), QPointF(x, r.bottom() - (9 if major else 4)))
            if major:
                p.setPen(QColor(190, 190, 200))
                p.drawText(QPointF(x + 3, r.top() + 12), fmt_time(t, step))
                p.setPen(QColor(120, 120, 130))
            k += 1

    def _paint_log(self, p: QPainter) -> None:
        r = self.row_rect("log")
        p.fillRect(r, QColor(26, 27, 32))
        tel = self.state.telemetry
        if tel is None:
            p.setPen(QColor(130, 130, 140))
            p.drawText(r.adjusted(HEADER_W + 10, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter,
                       "No flight log loaded. Import a .bin or .tlog file.")
            return
        # log extent
        x0, x1 = self.x_of(0), self.x_of(tel.duration)
        p.fillRect(QRectF(x0, r.top(), x1 - x0, r.height()), QColor(34, 36, 44))
        band = QRectF(r.left(), r.top(), r.width(), 15)
        for t0, t1, name in tel.mode_spans():
            xa, xb = max(self.x_of(t0), HEADER_W), self.x_of(t1)
            if xb < HEADER_W or xa > self.width():
                continue
            br = QRectF(xa, band.top() + 1, max(1.0, xb - xa - 1), band.height() - 2)
            p.fillRect(br, QColor(mode_color(name)))
            if br.width() > 40:
                p.setPen(QColor(255, 255, 255))
                p.drawText(br.adjusted(4, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter, name)
        area = QRectF(r.left(), band.bottom() + 3, r.width(), r.bottom() - band.bottom() - 6)
        a, b = self.visible_range()
        n = int(self.content_w // 2)
        if n > 2:
            ts = np.linspace(max(a, 0.0), min(b, tel.duration), n)
            legend_x = self.width() - 8
            for i, key in enumerate(self.state.project.graph_channels[:3]):
                s = tel.channels.get(key)
                if s is None or len(s) == 0:
                    continue
                lo, hi = s.min_max()
                if hi - lo < 1e-9:
                    hi = lo + 1
                vs = s.at_many(ts)
                xs = HEADER_W + (ts - self.t0) * self.pps
                ys = area.bottom() - (vs - lo) / (hi - lo) * area.height()
                pen = QPen(GRAPH_COLORS[i % 3], 1.4)
                p.setPen(pen)
                path = QPainterPath()
                started = False
                for x, y in zip(xs, ys):
                    if math.isnan(y):
                        started = False
                        continue
                    if not started:
                        path.moveTo(x, y)
                        started = True
                    else:
                        path.lineTo(x, y)
                p.drawPath(path)
                label = CHANNEL_BY_KEY[key].label if key in CHANNEL_BY_KEY else key
                fm = p.fontMetrics()
                w = fm.horizontalAdvance(label)
                legend_x -= w + 18
                p.drawLine(QPointF(legend_x, band.bottom() + 9), QPointF(legend_x + 10, band.bottom() + 9))
                p.setPen(QColor(200, 200, 205))
                p.drawText(QPointF(legend_x + 13, band.bottom() + 13), label)
        # events
        for ev in tel.events:
            if ev.kind not in ("arm", "disarm", "takeoff", "landing", "throttle"):
                continue
            x = self.x_of(ev.t)
            if x < HEADER_W or x > self.width():
                continue
            col = {"arm": QColor("#ff5252"), "disarm": QColor("#9e9e9e"), "takeoff": QColor("#69f0ae"),
                   "landing": QColor("#ffd740"), "throttle": QColor("#ffab40")}[ev.kind]
            p.setPen(QPen(col, 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(x, band.bottom()), QPointF(x, r.bottom()))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawPolygon(QPolygonF([QPointF(x - 4, r.bottom()), QPointF(x + 4, r.bottom()),
                                     QPointF(x, r.bottom() - 6)]))
            p.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_track(self, p: QPainter, track: int) -> None:
        r = self.row_rect(track)
        p.fillRect(r, QColor(26, 27, 32))
        st = self.state
        clips = st.project.clips_on(track)
        if not clips and track == 1 and len(st.project.clips) > 0:
            p.setPen(QColor(110, 110, 120))
            p.drawText(r.adjusted(HEADER_W + 10, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter,
                       "Drag a clip here to show it picture-in-picture")
        for c in clips:
            xa, xb = self.x_of(c.start), self.x_of(c.end)
            if xb < HEADER_W or xa > self.width():
                continue
            cr = QRectF(xa, r.top() + 3, max(3.0, xb - xa), r.height() - 6)
            sel = c.id == st.selected_clip_id
            base = QColor(TRACK_COLORS[track])
            missing = not os.path.exists(c.path)
            if missing:
                base = QColor("#8e3b3b")
            if sel:
                base = base.lighter(135)
            if sel:
                # ghost of trimmed-away parts
                ga, gb = self.x_of(c.offset), self.x_of(c.offset + c.duration)
                p.setPen(QPen(QColor(255, 255, 255, 70), 1, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(ga, cr.top(), gb - ga, cr.height()), 4, 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(base)
            p.drawRoundedRect(cr, 4, 4)
            overlap = st.project.overlaps(c.track, c.start, c.end, ignore=c.id)
            if sel or overlap:
                p.setPen(QPen(QColor("#ff5252") if overlap else QColor(255, 255, 255), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(cr, 4, 4)
            p.setPen(QColor(255, 255, 255))
            tag = ""
            if missing:
                tag = "  (file missing)"
            elif c.auto_placed and not c.verified:
                tag = "  ~auto"
            text_r = cr.adjusted(6, 2, -4, -2)
            p.save()
            p.setClipRect(cr)
            p.drawText(text_r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, c.name + tag)
            p.setPen(QColor(220, 225, 235))
            p.drawText(text_r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                       f"{fmt_duration(c.src_out - c.trim_in)}  starts at {fmt_time(c.start, 0.1)}")
            p.restore()

    def _paint_range(self, p: QPainter) -> None:
        proj = self.state.project
        if proj.range_in is None and proj.range_out is None:
            return
        a, b = proj.output_range()
        shade = QColor(0, 0, 0, 110)
        top = RULER_H
        h = self.height() - top
        xa, xb = self.x_of(a), self.x_of(b)
        if xa > HEADER_W:
            p.fillRect(QRectF(HEADER_W, top, xa - HEADER_W, h), shade)
        if xb < self.width():
            p.fillRect(QRectF(xb, top, self.width() - xb, h), shade)
        p.setPen(QPen(QColor("#66bb6a"), 2))
        for x, lab in ((xa, "IN"), (xb, "OUT")):
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            p.drawText(QPointF(x + 3, RULER_H - 3), lab)

    def _paint_marks(self, p: QPainter) -> None:
        st = self.state
        if st.video_mark:
            c = st.project.clip(st.video_mark[0])
            if c is not None:
                x = self.x_of(c.offset + st.video_mark[1])
                r = self.row_rect(c.track)
                p.setPen(QPen(QColor("#ff9800"), 2))
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
                p.drawText(QPointF(x + 3, r.top() + 12), "video mark")
        if st.log_mark is not None:
            x = self.x_of(st.log_mark)
            r = self.row_rect("log")
            p.setPen(QPen(QColor("#ffeb3b"), 2))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.drawText(QPointF(x + 3, r.bottom() - 4), "log mark")

    def _paint_playhead(self, p: QPainter) -> None:
        x = self.x_of(self.state.playhead)
        if x < HEADER_W or x > self.width():
            return
        p.setPen(QPen(QColor("#ff4d4d"), 1.5))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.setBrush(QColor("#ff4d4d"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 9)]))

    # ---------------------------------------------------------------- interaction

    def _clip_at(self, track: int, x: float):
        t = self.t_of(x)
        tol = 3 / self.pps
        for c in self.state.project.clips_on(track):
            if c.start - tol <= t <= c.end + tol:
                return c
        return None

    def _edge(self, c, x: float) -> str:
        if abs(x - self.x_of(c.start)) <= EDGE_PX and (self.x_of(c.end) - self.x_of(c.start)) > 3 * EDGE_PX:
            return "trim_in"
        if abs(x - self.x_of(c.end)) <= EDGE_PX and (self.x_of(c.end) - self.x_of(c.start)) > 3 * EDGE_PX:
            return "trim_out"
        return "move"

    def mousePressEvent(self, e) -> None:
        pos = e.position()
        x, y = pos.x(), pos.y()
        if x < HEADER_W:
            return
        row = self.row_at(y)
        st = self.state
        if e.button() == Qt.MouseButton.RightButton:
            return
        if e.button() == Qt.MouseButton.MiddleButton:
            self.drag = {"kind": "pan", "x0": x, "t00": self.t0}
            return
        if row == "log" and st.pick_log_mark and st.telemetry is not None:
            t = self._snap_to_event(self.t_of(x))
            st.pick_log_mark = False
            st.set_sync_marks(log=t)
            st.status.emit(f"Log mark set at {fmt_time(t, 0.1)}. Now click Align in the Clip & Sync panel.")
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if row in (0, 1):
            c = self._clip_at(row, x)
            if c is not None:
                st.select_clip(c.id)
                self.drag = {"kind": self._edge(c, x), "clip": c, "x0": x, "before": st.clip_fields(c),
                             "offset0": c.offset, "trim_in0": c.trim_in, "src_out0": c.src_out, "moved": False}
                return
            st.select_clip(None)
        self.drag = {"kind": "scrub"}
        st.set_playhead(self.t_of(x))

    def _snap_to_event(self, t: float) -> float:
        tel = self.state.telemetry
        best, best_d = t, SNAP_PX / self.pps
        for ev in tel.events if tel else []:
            if abs(ev.t - t) < best_d:
                best, best_d = ev.t, abs(ev.t - t)
        return best

    def _snap_candidates(self, clip) -> list[float]:
        st = self.state
        cands = [st.playhead]
        for c in st.project.clips:
            if c is not clip:
                cands += [c.start, c.end]
        if st.project.range_in is not None:
            cands.append(st.project.range_in)
        if st.project.range_out is not None:
            cands.append(st.project.range_out)
        return cands

    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        x, y = pos.x(), pos.y()
        d = self.drag
        st = self.state
        if d is None:
            self._update_cursor(x, y)
            return
        if d["kind"] == "scrub":
            st.set_playhead(self.t_of(x))
            return
        if d["kind"] == "pan":
            self.set_t0(d["t00"] - (x - d["x0"]) / self.pps)
            return
        c = d["clip"]
        dt = (x - d["x0"]) / self.pps
        if abs(x - d["x0"]) > 2:
            d["moved"] = True
        if not d["moved"]:
            return
        no_snap = (not self.snap) or bool(e.modifiers() & Qt.KeyboardModifier.AltModifier)
        if d["kind"] == "move":
            new_off = d["offset0"] + dt
            if not no_snap:
                best = None
                for cand in self._snap_candidates(c):
                    for edge_t, base in ((new_off + c.trim_in, c.trim_in), (new_off + c.src_out, c.src_out)):
                        dpx = abs(self.x_of(edge_t) - self.x_of(cand))
                        if dpx < SNAP_PX and (best is None or dpx < best[0]):
                            best = (dpx, cand - base)
                if best:
                    new_off = best[1]
            row = self.row_at(y)
            if row in (0, 1):
                c.track = row
            c.offset = new_off
        elif d["kind"] == "trim_in":
            c.trim_in = max(0.0, min(d["src_out0"] - 0.2, d["trim_in0"] + dt))
        elif d["kind"] == "trim_out":
            new_out = max(c.trim_in + 0.2, min(c.duration, d["src_out0"] + dt))
            c.trim_out = None if new_out >= c.duration - 1e-3 else new_out
        st.clipsChanged.emit()

    def mouseReleaseEvent(self, e) -> None:
        d = self.drag
        self.drag = None
        if not d or d["kind"] in ("scrub", "pan") or not d["moved"]:
            return
        c = d["clip"]
        st = self.state
        if st.project.overlaps(c.track, c.start, c.end, ignore=c.id):
            st.apply_clip_fields(c.id, d["before"])
            st.status.emit("Clips on the same camera track can't overlap. "
                           "Drop it on the other track to show it picture-in-picture.")
            return
        text = {"move": f"Move {c.name}", "trim_in": f"Trim {c.name}", "trim_out": f"Trim {c.name}"}[d["kind"]]
        if d["kind"] == "move":
            c.verified = True
        st.commit_clip_change(c, d["before"], text)

    def mouseDoubleClickEvent(self, e) -> None:
        row = self.row_at(e.position().y())
        if row in (0, 1):
            c = self._clip_at(row, e.position().x())
            if c is not None:
                self.state.set_playhead(c.start)

    def _update_cursor(self, x: float, y: float) -> None:
        row = self.row_at(y)
        shape = Qt.CursorShape.ArrowCursor
        tip = ""
        if x >= HEADER_W:
            if row == "log" and self.state.pick_log_mark:
                shape = Qt.CursorShape.CrossCursor
                tip = "Click the moment that matches your video mark"
            elif row in (0, 1):
                c = self._clip_at(row, x)
                if c is not None:
                    edge = self._edge(c, x)
                    shape = Qt.CursorShape.SizeHorCursor if edge != "move" else Qt.CursorShape.OpenHandCursor
                    tip = (f"{c.name}\nDrag to move in time, drag edges to trim.\n"
                           f"Hold Alt to turn off snapping. Right-click for more.")
            elif row == "log" and self.state.telemetry is not None:
                t = self.t_of(x)
                parts = [fmt_time(t, 0.1)]
                for key in self.state.project.graph_channels[:3]:
                    v = self.state.telemetry.value(key, t)
                    if not math.isnan(v) and key in CHANNEL_BY_KEY:
                        parts.append(f"{CHANNEL_BY_KEY[key].label}: {v:.1f}")
                m = self.state.telemetry.mode_name(t)
                if m:
                    parts.append(f"Mode: {m}")
                for ev in self.state.telemetry.events:
                    if abs(self.x_of(ev.t) - x) < 5:
                        parts.append(ev.label)
                tip = "\n".join(parts)
        self.setCursor(shape)
        self.setToolTip(tip)

    def wheelEvent(self, e) -> None:
        dy = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.set_t0(self.t0 - dy / 120.0 * 80 / self.pps)
        else:
            self.zoom(1.2 ** (dy / 120.0), e.position().x())
        e.accept()

    def contextMenuEvent(self, e) -> None:
        x, y = e.pos().x(), e.pos().y()
        row = self.row_at(y)
        if row not in (0, 1):
            return
        c = self._clip_at(row, x)
        if c is None:
            return
        st = self.state
        st.select_clip(c.id)
        m = QMenu(self)
        other = 1 - c.track
        a_move = m.addAction(f"Move to {TRACK_NAMES[other]}")
        a_place = m.addAction("Place from file timestamp")
        a_here = m.addAction("Start clip at playhead")
        a_untrim = m.addAction("Remove trimming")
        m.addSeparator()
        a_remove = m.addAction("Remove from timeline")
        act = m.exec(e.globalPos())
        if act is None:
            return
        if act is a_move:
            if st.project.overlaps(other, c.start, c.end, ignore=c.id):
                st.status.emit(f"{TRACK_NAMES[other]} already has a clip at that time.")
            else:
                st.edit_clip(c, f"Move {c.name} to {TRACK_NAMES[other]}", track=other)
        elif act is a_place:
            self.window().place_from_timestamp(c)
        elif act is a_here:
            st.edit_clip(c, f"Move {c.name}", offset=st.playhead - c.trim_in, verified=True)
        elif act is a_untrim:
            st.edit_clip(c, f"Untrim {c.name}", trim_in=0.0, trim_out=None)
        elif act is a_remove:
            self.window().remove_clip(c)


class TimelinePanel(QWidget):
    """Timeline plus its small toolbar and scrollbar."""

    def __init__(self, state, playback, parent=None):
        super().__init__(parent)
        self.state = state
        self.tl = TimelineWidget(state, playback, self)
        self.scroll = QScrollBar(Qt.Orientation.Horizontal, self)
        bar = QHBoxLayout()
        bar.setContentsMargins(4, 2, 4, 2)
        bar.addWidget(QLabel("Timeline"))
        for text, tip, fn in (("Fit", "Show the whole timeline", self.tl.fit_all),
                              ("+", "Zoom in (mouse wheel)", lambda: self.tl.zoom(1.5)),
                              ("−", "Zoom out (mouse wheel)", lambda: self.tl.zoom(1 / 1.5))):
            b = QToolButton(self)
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
        snap = QCheckBox("Snap", self)
        snap.setChecked(True)
        snap.setToolTip("Snap clip edges to other clips and the playhead (hold Alt while dragging to override)")
        snap.toggled.connect(lambda v: setattr(self.tl, "snap", v))
        bar.addWidget(snap)
        bar.addStretch(1)
        bar.addWidget(QLabel("Graph:"))
        self.graph_boxes = []
        numeric = [c for c in CHANNELS if c.quantity not in ("mode", "bool", "coord")]
        for i in range(2):
            cb = QComboBox(self)
            cb.addItem("(none)", "")
            for c in numeric:
                cb.addItem(c.label, c.key)
            cb.currentIndexChanged.connect(self._graph_changed)
            self.graph_boxes.append(cb)
            bar.addWidget(cb)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(bar)
        lay.addWidget(self.tl, 1)
        lay.addWidget(self.scroll)
        self.tl.viewChanged.connect(self._sync_scroll)
        self.scroll.valueChanged.connect(self._scrolled)
        state.projectReset.connect(self._load_graph_choice)
        state.clipsChanged.connect(self._sync_scroll)
        self._load_graph_choice()

    def _load_graph_choice(self) -> None:
        keys = list(self.state.project.graph_channels) + ["", ""]
        for cb, key in zip(self.graph_boxes, keys):
            cb.blockSignals(True)
            idx = cb.findData(key)
            cb.setCurrentIndex(max(0, idx))
            cb.blockSignals(False)
        self.tl.update()

    def _graph_changed(self) -> None:
        self.state.project.graph_channels = [cb.currentData() for cb in self.graph_boxes if cb.currentData()]
        self.state.mark_dirty()
        self.tl.update()

    def _sync_scroll(self) -> None:
        lo, hi = self.state.extent()
        a, b = self.tl.visible_range()
        lo, hi = min(lo, a), max(hi, b)
        self.scroll.blockSignals(True)
        self.scroll.setRange(int(lo * 100), int(max(lo, hi - (b - a)) * 100))
        self.scroll.setPageStep(int((b - a) * 100))
        self.scroll.setSingleStep(max(1, int((b - a) * 5)))
        self.scroll.setValue(int(a * 100))
        self.scroll.blockSignals(False)

    def _scrolled(self, v: int) -> None:
        self.tl.t0 = v / 100.0
        self.tl.update()
