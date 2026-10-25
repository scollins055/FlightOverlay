"""Video preview with the overlay drawn on top. Overlay widgets and the PiP can be dragged."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget

from ..overlay.renderer import OverlayRenderer
from ..overlay.widgets import WIDGETS
from ..render.filters import pip_box


class PreviewWidget(QWidget):
    importRequested = Signal()

    def __init__(self, state, playback, parent=None):
        super().__init__(parent)
        self.state = state
        self.playback = playback
        self.setMinimumSize(320, 180)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.renderer = OverlayRenderer(state.project.overlay, state.project.units, state.telemetry)
        self.show_overlay = True
        self.hover: str | None = None       # widget id or 'pip'
        self.drag: dict | None = None
        state.overlayChanged.connect(self._sync_renderer)
        state.projectReset.connect(self._sync_renderer)
        state.telemetryChanged.connect(self._on_tel)
        state.playheadChanged.connect(lambda _t: self.update())
        state.clipsChanged.connect(self.update)
        state.exportSettingsChanged.connect(self.update)
        state.widgetSelected.connect(lambda _w: self.update())
        playback.frameReady.connect(self.update)

    def _sync_renderer(self) -> None:
        self.renderer.layout = self.state.project.overlay
        self.renderer.units = self.state.project.units
        if len(self.renderer.cache) > 64:
            self.renderer.cache.clear()
        self.update()

    def _on_tel(self) -> None:
        self.renderer.set_telemetry(self.state.telemetry)
        self._sync_renderer()

    # ---------------------------------------------------------------- geometry

    def frame_rect(self) -> QRectF:
        W, H = self.state.project.output_size()
        avail = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        k = min(avail.width() / W, avail.height() / H)
        w, h = W * k, H * k
        return QRectF(avail.center().x() - w / 2, avail.center().y() - h / 2, w, h)

    def _pip_rect(self, fr: QRectF, second) -> QRectF:
        W, H = self.state.project.output_size()
        x, y, pw, ph = pip_box(W, H, self.state.project.export, second.info if second else None)
        k = fr.width() / W
        return QRectF(fr.left() + x * k, fr.top() + y * k, pw * k, ph * k)

    def _clips_now(self):
        p = self.state.project
        t = self.state.playhead
        a, b = p.clip_at(0, t), p.clip_at(1, t)
        return (a or b), (b if a else None)

    # ---------------------------------------------------------------- painting

    @staticmethod
    def _draw_fit(p: QPainter, img: QImage | None, rect: QRectF) -> None:
        if img is None or img.isNull():
            return
        k = min(rect.width() / img.width(), rect.height() / img.height())
        w, h = img.width() * k, img.height() * k
        p.drawImage(QRectF(rect.center().x() - w / 2, rect.center().y() - h / 2, w, h), img)

    def _msg(self, p: QPainter, rect: QRectF, text: str, size: int = 13) -> None:
        f = QFont(self.font())
        f.setPointSize(size)
        p.setFont(f)
        p.setPen(QColor(200, 200, 205))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(22, 22, 26))
        fr = self.frame_rect()
        p.fillRect(fr, QColor(0, 0, 0))
        st = self.state
        proj = st.project
        main, second = self._clips_now()
        ex = proj.export
        if main is not None:
            img_main = self.playback.tracks[main.track].image()
            if second is not None and ex.layout == "side":
                lw = fr.width() / 2
                self._draw_fit(p, img_main, QRectF(fr.left(), fr.top(), lw, fr.height()))
                self._draw_fit(p, self.playback.tracks[second.track].image(),
                               QRectF(fr.left() + lw, fr.top(), fr.width() - lw, fr.height()))
            else:
                self._draw_fit(p, img_main, fr)
                if second is not None:
                    pr = self._pip_rect(fr, second)
                    p.fillRect(pr, QColor(0, 0, 0))
                    self._draw_fit(p, self.playback.tracks[second.track].image(), pr)
                    pen = QPen(QColor(255, 255, 255, 220), 2 if self.hover != "pip" else 3)
                    if self.hover == "pip":
                        pen.setColor(QColor(80, 180, 255))
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(pr.adjusted(-1, -1, 1, 1))
            tp = self.playback.tracks[main.track]
            if tp.error:
                self._msg(p, fr, f"{main.name}\n{tp.error}")
            elif img_main is None:
                self._msg(p, fr, "Loading video…")
        elif not proj.clips and st.telemetry is None:
            self._msg(p, fr, "Drop a flight log (.bin / .tlog) and your videos here,\n"
                             "or click Import files.", 14)
        elif not proj.clips:
            self._msg(p, fr, "Now import the video files from this flight.", 14)
        else:
            self._msg(p, QRectF(fr.left(), fr.top() + fr.height() * 0.62, fr.width(), fr.height() * 0.1),
                      "No video at this point of the timeline", 11)

        if self.show_overlay and (st.telemetry is not None or proj.clips):
            p.save()
            p.translate(fr.topLeft())
            p.setClipRect(QRectF(0, 0, fr.width(), fr.height()))
            self.renderer.paint(p, fr.width(), fr.height(), st.playhead)
            p.restore()
            # outline for hovered / selected widget
            for wid, col, dashed in ((st.selected_widget_id, QColor(255, 210, 80, 200), True),
                                     (self.hover, QColor(80, 180, 255, 230), False)):
                if not wid or wid == "pip":
                    continue
                cfg = proj.overlay.get(wid)
                if cfg is None or not cfg.enabled or not self.renderer.is_available(cfg):
                    continue
                r = self.renderer.widget_rect(cfg, fr.width(), fr.height()).translated(fr.topLeft())
                pen = QPen(col, 1.5)
                if dashed:
                    pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r.adjusted(-2, -2, 2, 2))
        p.end()

    # ---------------------------------------------------------------- interaction

    def _hit(self, pos: QPointF) -> str | None:
        fr = self.frame_rect()
        if not fr.contains(pos):
            return None
        if self.show_overlay:
            for cfg in reversed(self.renderer.visible_widgets()):
                r = self.renderer.widget_rect(cfg, fr.width(), fr.height()).translated(fr.topLeft())
                if r.contains(pos):
                    return cfg.id
        main, second = self._clips_now()
        if second is not None and self.state.project.export.layout != "side":
            if self._pip_rect(fr, second).contains(pos):
                return "pip"
        return None

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)
        pos = e.position()
        hit = self._hit(pos)
        if hit is None:
            return
        if hit == "pip":
            ex = self.state.project.export
            self.drag = {"kind": "pip", "start": pos, "orig": (ex.pip_x, ex.pip_y), "moved": False}
        else:
            cfg = self.state.project.overlay.get(hit)
            self.drag = {"kind": "widget", "id": hit, "start": pos, "orig": (cfg.x, cfg.y), "moved": False}
            self.state.select_widget(hit)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        if self.drag:
            fr = self.frame_rect()
            dx = (pos.x() - self.drag["start"].x()) / fr.width()
            dy = (pos.y() - self.drag["start"].y()) / fr.height()
            ox, oy = self.drag["orig"]
            nx, ny = min(1.0, max(0.0, ox + dx)), min(1.0, max(0.0, oy + dy))
            if self.drag["kind"] == "pip":
                ex = self.state.project.export
                ex.pip_x, ex.pip_y = nx, ny
            else:
                cfg = self.state.project.overlay.get(self.drag["id"])
                cfg.x, cfg.y = nx, ny
            self.drag["moved"] = True
            self.update()
            return
        hit = self._hit(pos)
        if hit != self.hover:
            self.hover = hit
            self.setCursor(Qt.CursorShape.OpenHandCursor if hit else Qt.CursorShape.ArrowCursor)
            if hit and hit != "pip":
                w = WIDGETS[self.state.project.overlay.get(hit).type]
                self.setToolTip(f"{w.title}: drag to move, mouse wheel to resize, right-click for options")
            elif hit == "pip":
                self.setToolTip("Second camera: drag to move. Size is set in Export settings.")
            else:
                self.setToolTip("")
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self.drag:
            moved = self.drag["moved"]
            kind = self.drag["kind"]
            self.drag = None
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.hover else Qt.CursorShape.ArrowCursor)
            if moved:
                if kind == "pip":
                    self.state.export_edited()
                else:
                    self.state.overlay_edited()

    def leaveEvent(self, _e) -> None:
        if self.hover and not self.drag:
            self.hover = None
            self.update()

    def wheelEvent(self, e) -> None:
        hit = self._hit(e.position())
        if hit and hit != "pip":
            cfg = self.state.project.overlay.get(hit)
            steps = e.angleDelta().y() / 120.0
            cfg.scale = max(0.3, min(4.0, cfg.scale * (1.08 ** steps)))
            self.state.overlay_edited()
            e.accept()
            return
        if hit == "pip":
            ex = self.state.project.export
            ex.pip_size = max(0.15, min(0.6, ex.pip_size * (1.06 ** (e.angleDelta().y() / 120.0))))
            self.state.export_edited()
            e.accept()
            return
        super().wheelEvent(e)

    def contextMenuEvent(self, e) -> None:
        hit = self._hit(QPointF(e.pos()))
        if not hit or hit == "pip":
            return
        cfg = self.state.project.overlay.get(hit)
        m = QMenu(self)
        a_hide = m.addAction("Hide")
        a_big = m.addAction("Bigger")
        a_small = m.addAction("Smaller")
        a_reset = m.addAction("Reset size")
        act = m.exec(e.globalPos())
        if act is None:
            return
        if act is a_hide:
            cfg.enabled = False
        elif act is a_big:
            cfg.scale = min(4.0, cfg.scale * 1.15)
        elif act is a_small:
            cfg.scale = max(0.3, cfg.scale / 1.15)
        elif act is a_reset:
            cfg.scale = 1.0
        self.state.overlay_edited()
