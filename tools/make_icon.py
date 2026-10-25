"""Draw the app icon and save it as assets/icon.png and assets/icon.ico."""
import os
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPolygonF


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 64.0
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(2 * s, 2 * s, 60 * s, 60 * s), 12 * s, 12 * s)
    p.setClipPath(clip)
    p.translate(32 * s, 32 * s)
    p.rotate(-15)
    p.fillRect(QRectF(-60 * s, -60 * s, 120 * s, 62 * s), QColor("#3b7dd8"))
    p.fillRect(QRectF(-60 * s, 2 * s, 120 * s, 60 * s), QColor("#8a5a2b"))
    p.setPen(QPen(QColor("#ffffff"), 2.2 * s))
    p.drawLine(QPointF(-60 * s, 2 * s), QPointF(60 * s, 2 * s))
    p.resetTransform()
    p.setClipping(False)
    # aircraft symbol
    pen = QPen(QColor("#ffd166"), 5 * s)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(QPolygonF([QPointF(12 * s, 32 * s), QPointF(24 * s, 32 * s), QPointF(32 * s, 40 * s),
                              QPointF(40 * s, 32 * s), QPointF(52 * s, 32 * s)]))
    p.setPen(QPen(QColor(255, 255, 255, 200), 1.5 * s))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(2 * s, 2 * s, 60 * s, 60 * s), 12 * s, 12 * s)
    p.end()
    return img


def main() -> int:
    _app = QGuiApplication(sys.argv[:1])
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, "flightoverlay", "assets")
    os.makedirs(out, exist_ok=True)
    draw(256).save(os.path.join(out, "icon.png"))
    ok = draw(256).save(os.path.join(out, "icon.ico"))
    print("icon written" if ok else "ICO export not supported; PNG only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
