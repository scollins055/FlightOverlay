"""Clip & Sync panel: placement, trimming, timestamp fixes and the sync tool for the selected clip."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..core.project import TRACK_NAMES
from ..core.sync import align, video_start_utc
from .timeline_widget import fmt_time


def _spin(lo, hi, step, decimals, suffix="") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setKeyboardTracking(False)
    if suffix:
        s.setSuffix(suffix)
    return s


class ClipPanel(QScrollArea):
    def __init__(self, state, main_window, parent=None):
        super().__init__(parent)
        self.state = state
        self.mw = main_window
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.setWidget(body)
        lay = QVBoxLayout(body)

        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-weight: bold; font-size: 11pt;")
        lay.addWidget(self.title)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color: #a0a4ad;")
        lay.addWidget(self.info)

        # --- sync tool (most used, so it goes first)
        g = QGroupBox("Sync video to the flight log")
        gl = QVBoxLayout(g)
        how = QLabel("1. Pause the video on a moment you can recognise (launch, a sharp turn, touchdown).\n"
                     "2. Click Mark video.\n"
                     "3. Click the same moment on the log graph, or pick an event.\n"
                     "4. Click Align.")
        how.setWordWrap(True)
        how.setStyleSheet("color: #b8bcc6;")
        gl.addWidget(how)
        row = QHBoxLayout()
        self.btn_mark = QPushButton("Mark video  (M)")
        self.btn_mark.clicked.connect(self.mark_video)
        row.addWidget(self.btn_mark)
        self.lbl_vmark = QLabel("—")
        row.addWidget(self.lbl_vmark, 1)
        gl.addLayout(row)
        row = QHBoxLayout()
        self.btn_pick = QPushButton("Click on log graph")
        self.btn_pick.setCheckable(True)
        self.btn_pick.toggled.connect(self._pick_toggled)
        row.addWidget(self.btn_pick)
        self.events = QComboBox()
        self.events.setMinimumContentsLength(8)
        self.events.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.events.setToolTip("Moments found automatically in the log")
        self.events.activated.connect(self._event_chosen)
        row.addWidget(self.events, 1)
        gl.addLayout(row)
        self.lbl_lmark = QLabel("Log mark: —")
        gl.addWidget(self.lbl_lmark)
        self.btn_align = QPushButton("Align")
        self.btn_align.setStyleSheet("font-weight: bold;")
        self.btn_align.clicked.connect(self.do_align)
        gl.addWidget(self.btn_align)
        self.chk_chain = QCheckBox("Also shift later clips from this camera")
        self.chk_chain.setChecked(True)
        self.chk_chain.setToolTip("Useful for action cameras that split one recording into several files")
        gl.addWidget(self.chk_chain)
        self.lbl_result = QLabel("")
        self.lbl_result.setWordWrap(True)
        self.lbl_result.setStyleSheet("color: #8fd18f;")
        gl.addWidget(self.lbl_result)
        lay.addWidget(g)

        # --- placement
        g = QGroupBox("Placement")
        fl = QFormLayout(g)
        self.track = QComboBox()
        self.track.addItems([f"{TRACK_NAMES[0]} (main)", f"{TRACK_NAMES[1]} (picture-in-picture)"])
        self.track.activated.connect(self._track_changed)
        fl.addRow("Track", self.track)
        self.start = _spin(-1e6, 1e6, 0.1, 3, " s")
        self.start.setToolTip("Log time at which this clip starts playing")
        self.start.valueChanged.connect(self._start_changed)
        fl.addRow("Starts at", self.start)
        nud = QHBoxLayout()
        nud.setSpacing(2)
        for label, kind, amount in (("−1s", "s", -1), ("−1f", "f", -1), ("+1f", "f", 1), ("+1s", "s", 1)):
            b = QPushButton(label)
            b.setMinimumWidth(10)
            b.setToolTip("Move the clip by one frame (f) or one second (s).\n"
                         "Shortcuts: , and . for one frame; Shift+, and Shift+. for one second")
            b.clicked.connect(lambda _=False, k=kind, a=amount: self.mw.nudge_selected(a, k))
            nud.addWidget(b)
        fl.addRow("Nudge", nud)
        self.trim_in = _spin(0, 1e6, 0.1, 3, " s")
        self.trim_in.valueChanged.connect(self._trim_changed)
        fl.addRow("Trim start", self.trim_in)
        self.trim_out = _spin(0, 1e6, 0.1, 3, " s")
        self.trim_out.valueChanged.connect(self._trim_changed)
        fl.addRow("Trim end at", self.trim_out)
        lay.addWidget(g)

        # --- timestamp
        g = QGroupBox("File timestamp")
        fl = QFormLayout(g)
        self.lbl_stamp = QLabel("—")
        self.lbl_stamp.setWordWrap(True)
        fl.addRow("Recorded", self.lbl_stamp)
        self.tz = _spin(-14, 14, 0.5, 1, " h")
        self.tz.setToolTip("Many action cameras store local time as if it were UTC. Shift it here.")
        self.tz.valueChanged.connect(self._tz_changed)
        fl.addRow("Clock correction", self.tz)
        self.chk_end = QCheckBox("Timestamp is the end of the recording")
        self.chk_end.toggled.connect(self._end_toggled)
        fl.addRow("", self.chk_end)
        b = QPushButton("Place clip from timestamp")
        b.clicked.connect(lambda: self.mw.place_from_timestamp(self.state.selected_clip()))
        fl.addRow("", b)
        lay.addWidget(g)

        b = QPushButton("Remove clip from timeline")
        b.clicked.connect(lambda: self.mw.remove_clip(self.state.selected_clip()))
        lay.addWidget(b)
        lay.addStretch(1)
        self._controls = [self.track, self.start, self.trim_in, self.trim_out, self.tz, self.chk_end]

        state.selectionChanged.connect(lambda _c: self.refresh())
        state.clipsChanged.connect(self.refresh)
        state.syncMarksChanged.connect(self.refresh_marks)
        state.telemetryChanged.connect(self.refresh_events)
        state.projectReset.connect(self.refresh)
        self.refresh()
        self.refresh_events()

    # ---------------------------------------------------------------- refresh

    def refresh(self) -> None:
        c = self.state.selected_clip()
        for w in self._controls:
            w.blockSignals(True)
        try:
            enabled = c is not None
            for w in self._controls:
                w.setEnabled(enabled)
            if c is None:
                self.title.setText("No clip selected")
                self.info.setText("Click a clip on the timeline to adjust it. "
                                  "Drag clips left/right on the timeline to line them up with the log.")
                self.lbl_stamp.setText("—")
            else:
                self.title.setText(c.name)
                i = c.info
                if i:
                    extras = []
                    if i.is_hdr:
                        extras.append("HDR")
                    if not i.has_audio:
                        extras.append("no audio")
                    self.info.setText(f"{i.width}×{i.height}, {i.fps:.2f} fps, {fmt_time(i.duration, 0.1)} long"
                                      + (", " + ", ".join(extras) if extras else ""))
                self.track.setCurrentIndex(c.track)
                self.start.setValue(c.start)
                self.trim_in.setMaximum(max(0.0, c.duration))
                self.trim_in.setValue(c.trim_in)
                self.trim_out.setMaximum(max(0.0, c.duration))
                self.trim_out.setValue(c.src_out)
                self.tz.setValue(c.tz_shift_h)
                self.chk_end.setChecked(c.stamp_is_end)
                ts = video_start_utc(c)
                if ts is None:
                    self.lbl_stamp.setText("No timestamp in this file")
                else:
                    src = {"apple": "Apple metadata", "creation_time": "file metadata",
                           "file_mtime": "file date (less reliable)"}.get(i.creation_source, i.creation_source)
                    self.lbl_stamp.setText(time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts)) + f"\n({src})")
        finally:
            for w in self._controls:
                w.blockSignals(False)
        self.refresh_marks()

    def refresh_events(self) -> None:
        self.events.clear()
        self.events.addItem("…or pick an event", None)
        tel = self.state.telemetry
        if tel:
            for ev in tel.events:
                self.events.addItem(f"{ev.label}  @ {fmt_time(ev.t, 0.1)}", ev.t)

    def refresh_marks(self) -> None:
        st = self.state
        vm = st.video_mark
        if vm:
            c = st.project.clip(vm[0])
            self.lbl_vmark.setText(f"{vm[1]:.2f} s into {c.name}" if c else "—")
        else:
            self.lbl_vmark.setText("—")
        self.lbl_lmark.setText(f"Log mark: {fmt_time(st.log_mark, 0.1)}" if st.log_mark is not None
                               else "Log mark: —")
        self.btn_pick.blockSignals(True)
        self.btn_pick.setChecked(st.pick_log_mark)
        self.btn_pick.blockSignals(False)
        ok = bool(vm and st.project.clip(vm[0]) and st.log_mark is not None)
        self.btn_align.setEnabled(ok)

    # ---------------------------------------------------------------- sync tool

    def mark_video(self) -> None:
        st = self.state
        t = st.playhead
        c = st.selected_clip()
        if c is None or not c.contains(t):
            c = st.project.clip_at(0, t) or st.project.clip_at(1, t)
        if c is None:
            st.status.emit("Move the playhead onto a video clip first.")
            return
        st.select_clip(c.id)
        st.set_sync_marks(video=(c.id, t - c.offset))
        if st.telemetry is not None:
            st.pick_log_mark = True
            st.status.emit("Video marked. Now click the matching moment on the log graph (or pick an event).")
        self.lbl_result.setText("")
        self.refresh_marks()

    def _pick_toggled(self, on: bool) -> None:
        self.state.pick_log_mark = on
        if on:
            self.state.status.emit("Click the matching moment on the log graph.")

    def _event_chosen(self, idx: int) -> None:
        t = self.events.itemData(idx)
        if t is not None:
            self.state.pick_log_mark = False
            self.state.set_sync_marks(log=float(t))

    def do_align(self) -> None:
        st = self.state
        if not st.video_mark or st.log_mark is None:
            return
        c = st.project.clip(st.video_mark[0])
        if c is None:
            return
        new_off = align(c, st.video_mark[1], st.log_mark)
        delta = new_off - c.offset
        others = []
        if self.chk_chain.isChecked():
            others = [o for o in st.project.clips_on(c.track)
                      if o is not c and o.start >= c.end - 0.5 and not o.verified]
        # Move in an order that never makes clips overlap on the way:
        # later clips first when shifting later, earlier clips first when shifting earlier.
        chain = sorted([c] + others, key=lambda x: x.start, reverse=delta > 0)
        blocked = False
        st.undo.beginMacro(f"Align {c.name}")
        try:
            for o in chain:
                if st.project.overlaps(o.track, o.start + delta, o.end + delta, ignore=o.id):
                    if o is c:
                        blocked = True
                    continue
                if o is c:
                    st.edit_clip(o, f"Align {o.name}", offset=new_off, verified=True)
                else:
                    st.edit_clip(o, f"Shift {o.name}", offset=o.offset + delta)
        finally:
            st.undo.endMacro()
        if blocked:
            st.status.emit("Can't align: the clip would overlap another clip on the same track.")
            return
        st.set_playhead(st.log_mark)
        self.lbl_result.setText(f"Moved by {delta:+.2f} s. Play or step through to check it lines up.")
        st.set_sync_marks(video=None, log=None)

    # ---------------------------------------------------------------- edits

    def _track_changed(self, idx: int) -> None:
        c = self.state.selected_clip()
        if c is None or idx == c.track:
            return
        if self.state.project.overlaps(idx, c.start, c.end, ignore=c.id):
            self.state.status.emit(f"{TRACK_NAMES[idx]} already has a clip at that time.")
            self.refresh()
            return
        self.state.edit_clip(c, f"Move {c.name} to {TRACK_NAMES[idx]}", track=idx)

    def _start_changed(self, v: float) -> None:
        c = self.state.selected_clip()
        if c is None:
            return
        new_off = v - c.trim_in
        if self.state.project.overlaps(c.track, v, v + c.src_out - c.trim_in, ignore=c.id):
            self.state.status.emit("That would overlap another clip on the same track.")
            self.refresh()
            return
        self.state.edit_clip(c, f"Move {c.name}", merge_key=1, offset=new_off, verified=True)

    def _trim_changed(self, _v: float) -> None:
        c = self.state.selected_clip()
        if c is None:
            return
        a = self.trim_in.value()
        b = self.trim_out.value()
        if b <= a + 0.1:
            self.refresh()
            return
        self.state.edit_clip(c, f"Trim {c.name}", merge_key=2, trim_in=a,
                             trim_out=None if b >= c.duration - 1e-3 else b)

    def _tz_changed(self, v: float) -> None:
        c = self.state.selected_clip()
        if c is not None:
            self.state.edit_clip(c, f"Clock correction {c.name}", merge_key=3, tz_shift_h=v)

    def _end_toggled(self, on: bool) -> None:
        c = self.state.selected_clip()
        if c is not None:
            self.state.edit_clip(c, f"Timestamp setting {c.name}", stamp_is_end=on)
