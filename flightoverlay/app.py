"""Application entry point."""
from __future__ import annotations

import os
import sys


def _dark_palette(app) -> None:
    from PySide6.QtGui import QColor, QPalette
    app.setStyle("Fusion")
    pal = QPalette()
    base = QColor(37, 38, 43)
    pal.setColor(QPalette.ColorRole.Window, base)
    pal.setColor(QPalette.ColorRole.WindowText, QColor(225, 226, 230))
    pal.setColor(QPalette.ColorRole.Base, QColor(28, 29, 33))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(44, 45, 51))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(50, 52, 60))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(235, 235, 240))
    pal.setColor(QPalette.ColorRole.Text, QColor(225, 226, 230))
    pal.setColor(QPalette.ColorRole.Button, QColor(50, 52, 59))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(225, 226, 230))
    pal.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(61, 111, 182))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.ColorRole.Link, QColor(110, 170, 255))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(115, 116, 124))
    app.setPalette(pal)


def asset_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "flightoverlay", "assets", name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", name)


def main() -> int:
    os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
    argv = sys.argv[1:]
    if argv[:1] == ["--self-test"]:
        from .selftest import run
        return run(argv[1] if len(argv) > 1 else "selftest_report.txt")
    if argv[:1] == ["--cli"]:
        from .cli import main as cli_main
        return cli_main(argv[1:])
    if sys.platform == "win32":
        try:  # own taskbar icon/group instead of python.exe
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FlightOverlay.App")
        except Exception:
            pass
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import APP_NAME
    from .core import ffmpeg_tools
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    icon = asset_path("icon.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    _dark_palette(app)
    win = MainWindow()
    win.show()
    try:
        ffmpeg_tools.ffmpeg()
        ffmpeg_tools.ffprobe()
    except FileNotFoundError as e:
        QMessageBox.critical(win, APP_NAME, f"{e}\n\nVideo import and export won't work until FFmpeg is installed.")
    # open files passed on the command line (e.g. a project double-clicked in Explorer)
    args = [a for a in sys.argv[1:] if os.path.exists(a)]
    if args:
        win.import_files(args)
    shot = os.environ.get("FLIGHTOVERLAY_SCREENSHOT")
    if shot:  # test hook: save a screenshot of the window after a few seconds, then quit
        from PySide6.QtCore import QTimer

        def grab_and_quit():
            win.grab().save(shot)
            win.state.set_dirty(False)
            win.close()
        QTimer.singleShot(int(os.environ.get("FLIGHTOVERLAY_SCREENSHOT_MS", "5000")), grab_and_quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
