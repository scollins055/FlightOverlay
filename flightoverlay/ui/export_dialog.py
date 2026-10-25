"""Export dialog: output settings, then progress with cancel."""
from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
                               QSlider, QVBoxLayout)

from ..core import ffmpeg_tools
from ..core.timeline import plan_segments
from ..render.exporter import Exporter, default_output_path
from ..telemetry.units import fmt_duration
from .workers import Worker


class ExportDialog(QDialog):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("Export video")
        self.setMinimumWidth(560)
        self.worker: Worker | None = None
        self.exporter: Exporter | None = None
        ex = state.project.export
        lay = QVBoxLayout(self)
        form = QFormLayout()

        row = QHBoxLayout()
        self.out = QLineEdit(ex.output_path or default_output_path(state.project))
        row.addWidget(self.out, 1)
        b = QPushButton("Browse…")
        b.clicked.connect(self._browse)
        row.addWidget(b)
        form.addRow("Save as", row)

        def combo(items, current):
            c = QComboBox()
            for text, val in items:
                c.addItem(text, val)
            c.setCurrentIndex(max(0, c.findData(current)))
            c.currentIndexChanged.connect(self._update_summary)
            return c

        self.res = combo([("Same as main video", "match"), ("720p", "720p"), ("1080p (Full HD)", "1080p"),
                          ("1440p", "1440p"), ("2160p (4K)", "2160p")], ex.resolution)
        form.addRow("Resolution", self.res)
        self.fps = combo([("Same as main video", "match"), ("24", "24"), ("25", "25"), ("30", "30"),
                          ("50", "50"), ("60", "60")], ex.fps)
        form.addRow("Frame rate", self.fps)
        self.codec = combo([("H.264 (plays everywhere)", "h264"), ("H.265 / HEVC (smaller files)", "hevc")],
                           ex.codec)
        form.addRow("Format", self.codec)
        self.quality = combo([("Fast export, larger file", "fast"), ("Balanced", "balanced"),
                              ("Best quality (slower)", "high")], ex.quality)
        form.addRow("Quality", self.quality)
        self.encoder = combo([("Automatic (use graphics card if possible)", "auto"),
                              ("Processor only (slower)", "cpu")], ex.encoder if ex.encoder != "gpu" else "auto")
        form.addRow("Encoder", self.encoder)
        self.layout_box = combo([("Picture-in-picture", "pip"), ("Side by side", "side")], ex.layout)
        form.addRow("Two cameras", self.layout_box)
        self.pip_size = QSlider(Qt.Orientation.Horizontal)
        self.pip_size.setRange(15, 60)
        self.pip_size.setValue(int(ex.pip_size * 100))
        form.addRow("PiP size", self.pip_size)
        self.audio = combo([("From Cam A", "A"), ("From Cam B", "B"), ("No sound", "none")], ex.audio)
        form.addRow("Sound", self.audio)
        self.gaps = combo([("Skip parts with no video", "skip"), ("Show the overlay on black", "black")], ex.gaps)
        form.addRow("Gaps", self.gaps)
        self.hdr = QCheckBox("Convert HDR phone video to normal colours")
        self.hdr.setChecked(ex.hdr_tonemap)
        form.addRow("", self.hdr)
        lay.addLayout(form)
        self.pip_size.valueChanged.connect(self._update_summary)
        self._inputs = [self.out, self.res, self.fps, self.codec, self.quality, self.encoder, self.layout_box,
                        self.pip_size, self.audio, self.gaps, self.hdr]

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color: #b8bcc6;")
        lay.addWidget(self.summary)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        lay.addWidget(self.bar)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        self.buttons = QDialogButtonBox()
        self.btn_start = self.buttons.addButton("Export", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_cancel = self.buttons.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_play = self.buttons.addButton("Play video", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_folder = self.buttons.addButton("Open folder", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_play.setVisible(False)
        self.btn_folder.setVisible(False)
        self.btn_start.clicked.connect(self.start)
        self.btn_cancel.clicked.connect(self._cancel_or_close)
        self.btn_play.clicked.connect(lambda: os.startfile(self.out.text()))
        self.btn_folder.clicked.connect(lambda: subprocess.Popen(["explorer", "/select,",
                                                                   os.path.normpath(self.out.text())]))
        lay.addWidget(self.buttons)
        self._apply_settings()
        self._update_summary()

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save video as", self.out.text(), "MP4 video (*.mp4)")
        if path:
            if not path.lower().endswith(".mp4"):
                path += ".mp4"
            self.out.setText(path)

    def _apply_settings(self) -> None:
        ex = self.state.project.export
        ex.output_path = self.out.text().strip()
        ex.resolution = self.res.currentData()
        ex.fps = self.fps.currentData()
        ex.codec = self.codec.currentData()
        ex.quality = self.quality.currentData()
        ex.encoder = self.encoder.currentData()
        ex.layout = self.layout_box.currentData()
        ex.pip_size = self.pip_size.value() / 100.0
        ex.audio = self.audio.currentData()
        ex.gaps = self.gaps.currentData()
        ex.hdr_tonemap = self.hdr.isChecked()

    def _update_summary(self) -> None:
        self._apply_settings()
        p = self.state.project
        W, H = p.output_size()
        fps = p.output_fps()
        segs = plan_segments(p, fps)
        dur = sum(s.duration for s in segs)
        a, b = p.output_range()
        two = any(s.second for s in segs)
        txt = (f"{W}×{H} at {float(fps):.2f} fps. The video will be {fmt_duration(dur)} long "
               f"(timeline {fmt_duration(max(0, a))} to {fmt_duration(max(0, b))}).")
        if p.range_in is not None or p.range_out is not None:
            txt += " Only the part between the IN and OUT markers is exported."
        if two:
            txt += " Overlapping clips are shown together."
        if self.state.telemetry is None:
            txt += "\nNo flight log is loaded, so the overlay will be empty."
        self.summary.setText(txt)

    # ---------------------------------------------------------------- running

    def start(self) -> None:
        self._apply_settings()
        self.state.export_edited()
        out = self.out.text().strip()
        if not out:
            return
        if os.path.exists(out):
            r = QMessageBox.question(self, "Replace file?", f"{os.path.basename(out)} already exists. Replace it?")
            if r != QMessageBox.StandardButton.Yes:
                return
        try:
            ffmpeg_tools.ffmpeg()
        except FileNotFoundError as e:
            QMessageBox.critical(self, "FFmpeg missing", str(e))
            return
        self.exporter = Exporter(self.state.project, self.state.telemetry)
        if not self.exporter.plan().segments:
            QMessageBox.information(self, "Nothing to export", "There are no video clips in the export range.")
            return

        def job(progress, cancel):
            self.exporter.progress = progress
            return self.exporter.run(out)

        self.worker = Worker(job, self)
        self.worker.cancel_hook = self.exporter.cancel
        self.worker.progress.connect(self._progress)
        self.worker.succeeded.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.cancelled.connect(self._cancelled)
        for w in self._inputs:
            w.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.btn_cancel.setText("Cancel")
        self.btn_play.setVisible(False)
        self.btn_folder.setVisible(False)
        self.bar.setVisible(True)
        self.bar.setValue(0)
        self.status.setText("Starting…")
        self.worker.start()

    def _progress(self, frac: float, msg: str) -> None:
        self.bar.setValue(int(frac * 1000))
        self.status.setText(msg)

    def _finish_ui(self) -> None:
        for w in self._inputs:
            w.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.btn_cancel.setText("Close")
        self.worker = None

    def _done(self, path: str) -> None:
        self._finish_ui()
        self.bar.setValue(1000)
        self.status.setText(f"Finished: {path}")
        self.btn_play.setVisible(True)
        self.btn_folder.setVisible(True)

    def _failed(self, msg: str, details: str) -> None:
        self._finish_ui()
        self.bar.setVisible(False)
        self.status.setText("Export failed.")
        box = QMessageBox(QMessageBox.Icon.Critical, "Export failed", msg, parent=self)
        box.setDetailedText(details)
        box.exec()

    def _cancelled(self) -> None:
        self._finish_ui()
        self.bar.setVisible(False)
        self.status.setText("Export cancelled.")

    def _cancel_or_close(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.status.setText("Cancelling…")
        else:
            self.reject()

    def closeEvent(self, e) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(5000)
        super().closeEvent(e)

    def reject(self) -> None:
        if self.worker is not None:
            self._cancel_or_close()
            return
        super().reject()
