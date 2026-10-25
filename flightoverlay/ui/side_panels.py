"""Small side panels: live data values and the list of imported files."""
from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..core.project import TRACK_NAMES
from ..telemetry.channels import CHANNELS
from ..telemetry.units import format_value
from .timeline_widget import fmt_time


class ValuesPanel(QWidget):
    """All data values at the playhead; handy for checking sync."""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.head = QLabel()
        lay.addWidget(self.head)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Data", "Value", "From log field"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table, 1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self.refresh)
        state.playheadChanged.connect(lambda _t: self._timer.start() if not self._timer.isActive() else None)
        state.telemetryChanged.connect(self.rebuild)
        state.overlayChanged.connect(self.refresh)
        self.rebuild()

    def rebuild(self) -> None:
        tel = self.state.telemetry
        rows = [c for c in CHANNELS if tel is not None and tel.has(c.key)]
        self._rows = rows
        self.table.setRowCount(len(rows))
        for i, c in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(c.label))
            self.table.setItem(i, 1, QTableWidgetItem(""))
            self.table.setItem(i, 2, QTableWidgetItem(tel.sources.get(c.key, "")))
        self.refresh()

    def refresh(self) -> None:
        if not self.isVisible():
            return
        tel = self.state.telemetry
        t = self.state.playhead
        if tel is None:
            self.head.setText("No flight log loaded")
            return
        self.head.setText(f"Log time {fmt_time(t, 0.1)}")
        u = self.state.project.units
        for i, c in enumerate(self._rows):
            if c.key == "mode":
                txt = tel.mode_name(t) or "--"
            else:
                v, unit = format_value(c.quantity, tel.value(c.key, t), u)
                txt = f"{v} {unit}".strip()
            item = self.table.item(i, 1)
            if item.text() != txt:
                item.setText(txt)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh()


class MediaPanel(QWidget):
    """Imported files: the flight log and every video."""

    def __init__(self, state, main_window, parent=None):
        super().__init__(parent)
        self.state = state
        self.mw = main_window
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        b = QPushButton("Import files…")
        b.clicked.connect(self.mw.import_dialog)
        row.addWidget(b)
        lay.addLayout(row)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.list.setUniformItemSizes(False)
        self.list.itemDoubleClicked.connect(self._activate)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.list, 1)
        tip = QLabel("Tip: you can also drag files from Explorer onto the window.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #a0a4ad;")
        lay.addWidget(tip)
        state.clipsChanged.connect(self.refresh)
        state.telemetryChanged.connect(self.refresh)
        state.projectReset.connect(self.refresh)
        state.selectionChanged.connect(self._select)
        self.refresh()

    def refresh(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        st = self.state
        tel = st.telemetry
        if st.project.log_path:
            name = os.path.basename(st.project.log_path)
            if tel is not None:
                detail = f"{tel.source_type.upper()} log, {fmt_time(tel.duration)} long, {tel.vehicle}"
                if tel.utc_offset is None:
                    detail += ", no GPS time"
            else:
                detail = "not loaded"
            it = QListWidgetItem(f"✈  {name}\n     {detail}")
            it.setData(Qt.ItemDataRole.UserRole, ("log", st.project.log_path))
            it.setToolTip(st.project.log_path)
            it.setSizeHint(self._row_size())
            self.list.addItem(it)
        for c in sorted(st.project.clips, key=lambda c: (c.track, c.start)):
            i = c.info
            detail = f"{TRACK_NAMES[c.track]}, starts {fmt_time(c.start, 0.1)}"
            if i:
                detail += f", {i.width}×{i.height} {i.fps:.0f} fps"
            if not os.path.exists(c.path):
                detail += "  — FILE MISSING"
            it = QListWidgetItem(f"🎞  {c.name}\n     {detail}")
            it.setData(Qt.ItemDataRole.UserRole, ("clip", c.id))
            it.setToolTip(c.path)
            it.setSizeHint(self._row_size())
            self.list.addItem(it)
        self.list.blockSignals(False)
        self._select(st.selected_clip_id)

    def _row_size(self) -> QSize:
        return QSize(0, self.list.fontMetrics().lineSpacing() * 2 + 10)

    def _select(self, cid: str) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            kind, val = it.data(Qt.ItemDataRole.UserRole)
            if kind == "clip" and val == cid:
                self.list.setCurrentItem(it)
                return

    def _activate(self, it: QListWidgetItem) -> None:
        kind, val = it.data(Qt.ItemDataRole.UserRole)
        if kind == "clip":
            c = self.state.project.clip(val)
            if c:
                self.state.select_clip(c.id)
                self.state.set_playhead(c.start)

    def _menu(self, pos) -> None:
        it = self.list.itemAt(pos)
        if it is None:
            return
        kind, val = it.data(Qt.ItemDataRole.UserRole)
        m = QMenu(self)
        a_show = m.addAction("Show in Explorer")
        a_rm = m.addAction("Remove from project") if kind == "clip" else None
        act = m.exec(self.list.mapToGlobal(pos))
        if act is None:
            return
        path = val if kind == "log" else (self.state.project.clip(val).path if self.state.project.clip(val) else "")
        if act is a_show and path:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif act is a_rm:
            self.mw.remove_clip(self.state.project.clip(val))
