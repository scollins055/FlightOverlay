"""Main window: puts the preview, timeline and panels together; menus, shortcuts, import, save/open."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QCheckBox, QDockWidget, QFileDialog, QHBoxLayout, QLabel,
                               QMainWindow, QMessageBox, QProgressDialog, QSplitter, QToolButton, QVBoxLayout,
                               QWidget)

from .. import APP_NAME, PROJECT_EXT, __version__
from ..core import ffmpeg_tools
from ..core.media import is_log_file, is_video_file, probe
from ..core.project import Clip, Project
from ..core.sync import auto_place, offset_from_timestamp
from ..telemetry.loader import load_telemetry
from .clip_panel import ClipPanel
from .commands import AddClips, RemoveClip
from .export_dialog import ExportDialog
from .overlay_panel import OverlayPanel
from .playback import PlaybackController
from .preview_widget import PreviewWidget
from .side_panels import MediaPanel, ValuesPanel
from .timeline_widget import TimelinePanel, fmt_time
from .workers import Worker

FILE_FILTER = ("Flight logs and videos (*.bin *.BIN *.tlog *.log *.mp4 *.MP4 *.mov *.MOV *.mkv *.avi *.m4v "
               "*.mts *.MTS *.insv *.lrv *.LRV);;Flight logs (*.bin *.tlog *.log);;"
               "Videos (*.mp4 *.mov *.mkv *.avi *.m4v *.mts);;All files (*)")

QUICK_START = """<h3>Quick start</h3>
<ol>
<li><b>Import</b> your flight log (.bin from the SD card, or .tlog from Mission Planner) and the videos from
the flight. You can select them all at once, or drag them onto the window.</li>
<li>Videos are placed on the timeline using their recording time. Check the sync: play or scrub and compare the
video with the overlay (the artificial horizon is a good check while turning).</li>
<li>To fix the sync, pause on a recognisable moment (launch, touchdown, a sharp bank), click <b>Mark video</b>
(or press M), click the same moment on the log graph, then click <b>Align</b>. You can also drag clips on
the timeline, or nudge the selected clip with <b>,</b> and <b>.</b> (one frame) or <b>Shift+,</b> /
<b>Shift+.</b> (one second).</li>
<li>Choose what data to show in the <b>Overlay</b> panel. Drag items in the preview to move them; use the mouse
wheel over an item to resize it.</li>
<li>Put a second camera on <b>Cam B</b> to show it picture-in-picture where the clips overlap.</li>
<li>Optionally set IN and OUT markers (I and O keys) to export only part of the timeline.</li>
<li>Click <b>Export video</b>.</li>
</ol>
<p><b>Keys:</b> Space play/pause, ←/→ step one frame, Shift+←/→ one second, Home/End jump,
Ctrl+Z undo.</p>"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        from .state import AppState
        self.state = AppState()
        self.playback = PlaybackController(self.state, self)
        self.settings = QSettings(APP_NAME, APP_NAME)
        self._remember_layout = not os.environ.get("FLIGHTOVERLAY_TEST")
        self._workers: list[Worker] = []
        self.setAcceptDrops(True)
        self.resize(1500, 950)

        # ---- centre: preview + transport, then timeline
        self.preview = PreviewWidget(self.state, self.playback)
        centre_top = QWidget()
        v = QVBoxLayout(centre_top)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        v.addWidget(self.preview, 1)
        v.addLayout(self._transport_bar())
        self.timeline = TimelinePanel(self.state, self.playback)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(centre_top)
        split.addWidget(self.timeline)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([620, 280])
        self.setCentralWidget(split)

        # ---- docks
        self.media_panel = MediaPanel(self.state, self)
        self.overlay_panel = OverlayPanel(self.state)
        self.clip_panel = ClipPanel(self.state, self)
        self.values_panel = ValuesPanel(self.state)
        self.media_panel.setMinimumWidth(210)
        for w in (self.overlay_panel, self.clip_panel, self.values_panel):
            w.setMinimumWidth(340)
        self.dock_media = self._dock("Files", self.media_panel, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.dock_overlay = self._dock("Overlay", self.overlay_panel, Qt.DockWidgetArea.RightDockWidgetArea)
        self.dock_clip = self._dock("Clip && Sync", self.clip_panel, Qt.DockWidgetArea.RightDockWidgetArea)
        self.dock_values = self._dock("Data values", self.values_panel, Qt.DockWidgetArea.RightDockWidgetArea)
        self.tabifyDockWidget(self.dock_overlay, self.dock_clip)
        self.tabifyDockWidget(self.dock_clip, self.dock_values)
        self.dock_overlay.raise_()
        self.resizeDocks([self.dock_media, self.dock_overlay], [250, 380], Qt.Orientation.Horizontal)

        self._build_actions()
        self.statusBar().showMessage("Import a flight log and videos to begin.")
        self.state.status.connect(lambda m: self.statusBar().showMessage(m, 10000))
        self.state.dirtyChanged.connect(lambda _d: self._update_title())
        self.state.projectReset.connect(self._update_title)
        self.state.selectionChanged.connect(self._on_selection)
        self.state.playheadChanged.connect(self._update_time_label)
        self.playback.playingChanged.connect(self._on_playing)
        self._update_title()
        self._update_time_label(0.0)
        if self._remember_layout:
            geo = self.settings.value("geometry")
            if isinstance(geo, QByteArray):
                self.restoreGeometry(geo)
            st = self.settings.value("windowState")
            if isinstance(st, QByteArray):
                self.restoreState(st)

    # ---------------------------------------------------------------- building

    def _dock(self, title, widget, area) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName(title.replace("&", "").replace(" ", "_"))
        d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable |
                      QDockWidget.DockWidgetFeature.DockWidgetFloatable |
                      QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(area, d)
        return d

    def _transport_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 2)

        def btn(text, tip, fn, w=34):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setMinimumWidth(w)
            b.clicked.connect(fn)
            row.addWidget(b)
            return b

        btn("⏮", "Go to start (Home)", self.go_start)
        btn("−1s", "Back one second (Shift+←)", lambda: self.playback.step(-1.0), 40)
        btn("◀|", "Back one frame (←)", lambda: self.playback.step(-self.state.frame_dur()))
        self.btn_play = btn("▶", "Play / pause (Space)", self.playback.toggle, 48)
        btn("|▶", "Forward one frame (→)", lambda: self.playback.step(self.state.frame_dur()))
        btn("+1s", "Forward one second (Shift+→)", lambda: self.playback.step(1.0), 40)
        btn("⏭", "Go to end (End)", self.go_end)
        self.time_label = QLabel()
        self.time_label.setMinimumWidth(260)
        self.time_label.setStyleSheet("font-family: Consolas, monospace; font-size: 10pt; padding-left: 8px;")
        row.addWidget(self.time_label)
        row.addStretch(1)
        btn("Mark video (M)", "Sync tool: mark this moment in the video", self.clip_panel_mark, 110)
        btn("IN", "Start the export here (I)", self.set_in, 36)
        btn("OUT", "End the export here (O)", self.set_out, 40)
        btn("Clear IN/OUT", "Export everything", lambda: self.state.set_range(None, None), 90)
        ov = QCheckBox("Overlay")
        ov.setChecked(True)
        ov.toggled.connect(self._toggle_overlay)
        row.addWidget(ov)
        mute = QCheckBox("Mute")
        mute.toggled.connect(self.playback.set_muted)
        row.addWidget(mute)
        return row

    def _build_actions(self) -> None:
        mb = self.menuBar()
        tb = self.addToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        def act(text, fn, shortcut=None, tip=None):
            a = QAction(text, self)
            a.triggered.connect(fn)
            if shortcut:
                if isinstance(shortcut, (list, tuple)):
                    a.setShortcuts([QKeySequence(s) for s in shortcut])
                else:
                    a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setToolTip(tip)
                a.setStatusTip(tip)
            self.addAction(a)
            return a

        a_new = act("&New project", self.new_project, "Ctrl+N")
        a_open = act("&Open project…", self.open_project, "Ctrl+O")
        a_save = act("&Save project", self.save, "Ctrl+S")
        a_saveas = act("Save project &as…", self.save_as, "Ctrl+Shift+S")
        a_import = act("&Import files…", self.import_dialog, "Ctrl+I",
                       "Add a flight log and/or video files")
        a_export = act("&Export video…", self.export_dialog, "Ctrl+E", "Make the final video")
        a_quit = act("E&xit", self.close)
        self.recent_menu = None

        f = mb.addMenu("&File")
        for a in (a_new, a_open, a_save, a_saveas):
            f.addAction(a)
        self.recent_menu = f.addMenu("Open &recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        f.addSeparator()
        f.addAction(a_import)
        f.addAction(a_export)
        f.addSeparator()
        f.addAction(a_quit)

        a_undo = self.state.undo.createUndoAction(self, "&Undo")
        a_undo.setShortcut(QKeySequence.StandardKey.Undo)
        a_redo = self.state.undo.createRedoAction(self, "&Redo")
        a_redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        e = mb.addMenu("&Edit")
        e.addAction(a_undo)
        e.addAction(a_redo)
        e.addSeparator()
        e.addAction(act("Remove selected clip", lambda: self.remove_clip(self.state.selected_clip()), "Delete"))
        e.addAction(act("Place selected clip from timestamp",
                        lambda: self.place_from_timestamp(self.state.selected_clip())))
        e.addAction(act("Place all unsynced clips from timestamps", self.place_all))

        pb = mb.addMenu("&Playback")
        pb.addAction(act("Play / pause", self.playback.toggle, "Space"))
        pb.addAction(act("Next frame", lambda: self.playback.step(self.state.frame_dur()), "Right"))
        pb.addAction(act("Previous frame", lambda: self.playback.step(-self.state.frame_dur()), "Left"))
        pb.addAction(act("Forward 1 s", lambda: self.playback.step(1.0), "Shift+Right"))
        pb.addAction(act("Back 1 s", lambda: self.playback.step(-1.0), "Shift+Left"))
        pb.addAction(act("Go to start", self.go_start, "Home"))
        pb.addAction(act("Go to end", self.go_end, "End"))
        pb.addSeparator()
        pb.addAction(act("Nudge clip later by 1 frame", lambda: self.nudge_selected(1, "f"), [".", "Alt+Right"]))
        pb.addAction(act("Nudge clip earlier by 1 frame", lambda: self.nudge_selected(-1, "f"), [",", "Alt+Left"]))
        pb.addAction(act("Nudge clip later by 1 s", lambda: self.nudge_selected(1, "s"),
                         ["Shift+.", ">", "Alt+Shift+Right"]))
        pb.addAction(act("Nudge clip earlier by 1 s", lambda: self.nudge_selected(-1, "s"),
                         ["Shift+,", "<", "Alt+Shift+Left"]))
        pb.addAction(act("Mark video moment (sync)", self.clip_panel_mark, "M"))
        pb.addSeparator()
        pb.addAction(act("Set IN marker", self.set_in, "I"))
        pb.addAction(act("Set OUT marker", self.set_out, "O"))
        pb.addAction(act("Clear IN/OUT", lambda: self.state.set_range(None, None)))

        vm = mb.addMenu("&View")
        for d in (self.dock_media, self.dock_overlay, self.dock_clip, self.dock_values):
            vm.addAction(d.toggleViewAction())
        vm.addAction(act("Fit timeline", self.timeline.tl.fit_all, "Ctrl+0"))

        hm = mb.addMenu("&Help")
        hm.addAction(act("Quick start", self.show_help, "F1"))
        hm.addAction(act("Create demo project…", self.create_demo))
        hm.addAction(act("About", self.show_about))

        tb.addAction(a_import)
        tb.addAction(a_save)
        tb.addSeparator()
        tb.addAction(a_undo)
        tb.addAction(a_redo)
        tb.addSeparator()
        a_place = act("Auto-place clips", self.place_all, tip="Place clips that haven't been synced yet "
                                                              "using their recording time")
        tb.addAction(a_place)
        a_sync = act("Sync tool", lambda: (self.dock_clip.show(), self.dock_clip.raise_()),
                     tip="Line a video up with the log by matching one moment")
        tb.addAction(a_sync)
        tb.addSeparator()
        tb.addAction(a_export)

    # ---------------------------------------------------------------- small UI helpers

    def _update_title(self) -> None:
        name = os.path.basename(self.state.project.path) if self.state.project.path else "Untitled"
        self.setWindowTitle(f"{name}{' *' if self.state.dirty else ''} — {APP_NAME}")

    def _update_time_label(self, t: float) -> None:
        p = self.state.project
        parts = [f"log {fmt_time(t, 0.1)}"]
        for tr, name in ((0, "A"), (1, "B")):
            c = p.clip_at(tr, t)
            if c is not None:
                parts.append(f"{name}: {fmt_time(t - c.offset, 0.1)}")
        tel = self.state.telemetry
        if tel is not None and tel.utc_offset is not None:
            parts.append(time.strftime("%H:%M:%S", time.localtime(t + tel.utc_offset)))
        self.time_label.setText("   ".join(parts))

    def _on_playing(self, playing: bool) -> None:
        self.btn_play.setText("⏸" if playing else "▶")

    def _on_selection(self, cid: str) -> None:
        if cid and not self.dock_clip.isFloating():
            self.dock_clip.show()
            self.dock_clip.raise_()

    def _toggle_overlay(self, on: bool) -> None:
        self.preview.show_overlay = on
        self.preview.update()

    def clip_panel_mark(self) -> None:
        self.dock_clip.show()
        self.dock_clip.raise_()
        self.playback.pause()
        self.clip_panel.mark_video()

    def go_start(self) -> None:
        self.playback.pause()
        a = self.state.project.output_range()[0] if self.state.project.clips else self.state.extent()[0]
        self.state.set_playhead(a)

    def go_end(self) -> None:
        self.playback.pause()
        b = self.state.project.output_range()[1] if self.state.project.clips else self.state.extent()[1]
        self.state.set_playhead(b)

    def set_in(self) -> None:
        p = self.state.project
        out = p.range_out
        if out is not None and out <= self.state.playhead:
            out = None
        self.state.set_range(self.state.playhead, out)

    def set_out(self) -> None:
        p = self.state.project
        a = p.range_in
        if a is not None and a >= self.state.playhead:
            a = None
        self.state.set_range(a, self.state.playhead)

    # ---------------------------------------------------------------- clip editing

    def nudge_selected(self, amount: int, kind: str) -> None:
        c = self.state.selected_clip()
        if c is None:
            self.state.status.emit("Select a clip on the timeline first.")
            return
        delta = amount * (self.state.frame_dur() if kind == "f" else 1.0)
        if self.state.project.overlaps(c.track, c.start + delta, c.end + delta, ignore=c.id):
            self.state.status.emit("Can't move it further: it would overlap the next clip.")
            return
        self.playback.pause()
        self.state.edit_clip(c, f"Nudge {c.name}", merge_key=1, offset=c.offset + delta, verified=True)
        self.state.status.emit(f"{c.name} now starts at {fmt_time(c.start, 0.1)} ({delta:+.3f} s)")

    def remove_clip(self, c: Clip | None) -> None:
        if c is not None:
            self.state.undo.push(RemoveClip(self.state, c))

    def place_from_timestamp(self, c: Clip | None) -> None:
        if c is None:
            return
        tel = self.state.telemetry
        if tel is None or tel.utc_offset is None:
            QMessageBox.information(self, "Can't place by time",
                                    "The flight log has no GPS time, so videos can't be placed by their "
                                    "timestamps. Use the sync tool instead.")
            return
        off = offset_from_timestamp(c, tel)
        if off is None:
            self.state.status.emit(f"{c.name} has no usable timestamp.")
            return
        if self.state.project.overlaps(c.track, off + c.trim_in, off + c.src_out, ignore=c.id):
            other = 1 - c.track
            if self.state.project.overlaps(other, off + c.trim_in, off + c.src_out, ignore=c.id):
                self.state.status.emit("That position overlaps other clips on both tracks.")
                return
            self.state.edit_clip(c, f"Place {c.name}", offset=off, track=other, auto_placed=True, verified=False)
        else:
            self.state.edit_clip(c, f"Place {c.name}", offset=off, auto_placed=True, verified=False)
        self.state.set_playhead(c.start)

    def place_all(self) -> None:
        tel = self.state.telemetry
        todo = [c for c in self.state.project.clips if not c.verified]
        if not todo:
            self.state.status.emit("All clips have been synced by hand already.")
            return
        notes = []
        self.state.undo.beginMacro("Auto-place clips")
        try:
            for c in sorted(todo, key=lambda c: (c.info.creation_utc or 0) if c.info else 0):
                before = self.state.clip_fields(c)
                notes.append(f"{c.name}: {auto_place(self.state.project, c, tel, self.state.playhead)}")
                self.state.commit_clip_change(c, before, f"Place {c.name}")
        finally:
            self.state.undo.endMacro()
        self.state.clipsChanged.emit()
        self.timeline.tl.fit_all()
        self.state.status.emit("; ".join(notes))

    # ---------------------------------------------------------------- importing

    def import_dialog(self) -> None:
        start = self.settings.value("last_dir", os.path.expanduser("~"))
        paths, _ = QFileDialog.getOpenFileNames(self, "Import flight log and videos", start, FILE_FILTER)
        if paths:
            self.settings.setValue("last_dir", os.path.dirname(paths[0]))
            self.import_files(paths)

    def import_files(self, paths: list[str]) -> None:
        logs = [p for p in paths if is_log_file(p)]
        vids = [p for p in paths if is_video_file(p)]
        projs = [p for p in paths if p.lower().endswith(PROJECT_EXT)]
        other = [os.path.basename(p) for p in paths if p not in logs + vids + projs]
        if projs and not logs and not vids:
            self.open_project(projs[0])
            return
        if other:
            self.state.status.emit("Ignored files that aren't logs or videos: " + ", ".join(other[:5]))
        if len(logs) > 1:
            QMessageBox.information(self, "One log per project",
                                    f"A project uses one flight log. Using {os.path.basename(logs[0])}.")
        if logs:
            self.load_log(logs[0], then=lambda: self.add_videos(vids))
        else:
            self.add_videos(vids)

    def _run(self, title: str, fn, on_ok, cancellable: bool = True) -> None:
        dlg = QProgressDialog(title, "Cancel" if cancellable else None, 0, 1000, self)
        dlg.setWindowTitle(APP_NAME)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(250)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        w = Worker(fn, self)
        self._workers.append(w)

        def finish():
            dlg.close()
            if w in self._workers:
                self._workers.remove(w)

        w.progress.connect(lambda f, m: (dlg.setValue(int(f * 1000)), dlg.setLabelText(f"{title}\n{m}")))
        w.succeeded.connect(lambda res: (finish(), on_ok(res)))
        w.failed.connect(lambda msg, det: (finish(), self._error(title, msg, det)))
        w.cancelled.connect(finish)
        dlg.canceled.connect(w.cancel)
        w.start()

    def _error(self, title: str, msg: str, details: str = "") -> None:
        box = QMessageBox(QMessageBox.Icon.Warning, APP_NAME, f"{title} failed:\n\n{msg}", parent=self)
        if details:
            box.setDetailedText(details)
        box.exec()

    def load_log(self, path: str, then=None, ask: bool = True) -> None:
        st = self.state
        if ask and st.telemetry is not None and st.project.log_path and \
                os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(st.project.log_path)):
            r = QMessageBox.question(self, "Replace flight log?",
                                     f"This project already uses {os.path.basename(st.project.log_path)}.\n"
                                     f"Replace it with {os.path.basename(path)}?")
            if r != QMessageBox.StandardButton.Yes:
                if then:
                    then()
                return

        def job(progress, cancel):
            return load_telemetry(path, progress, cancel)

        def ok(tel):
            st.set_telemetry(tel, os.path.abspath(path))
            msg = (f"Loaded {os.path.basename(path)}: {fmt_time(tel.duration)} of data, "
                   f"{len(tel.channels)} data channels.")
            if tel.utc_offset is None:
                msg += " No GPS time in this log, so videos must be synced by hand."
            st.status.emit(msg)
            unplaced = [c for c in st.project.clips if not c.verified]
            if unplaced and tel.utc_offset is not None and not then:
                self.place_all()
            if then:
                then()

        self._run(f"Reading {os.path.basename(path)}", job, ok)

    def add_videos(self, paths: list[str]) -> None:
        st = self.state
        have = {os.path.normcase(os.path.abspath(c.path)) for c in st.project.clips}
        new = [p for p in paths if os.path.normcase(os.path.abspath(p)) not in have]
        if len(new) < len(paths):
            st.status.emit("Some videos were already in the project and were skipped.")
        if not new:
            if st.project.clips and st.telemetry is not None:
                unplaced = [c for c in st.project.clips if not c.verified]
                if unplaced:
                    self.place_all()
            return

        def job(progress, cancel):
            out = []
            for i, p in enumerate(new):
                if cancel():
                    break
                progress(i / len(new), os.path.basename(p))
                try:
                    out.append((p, probe(p), None))
                except Exception as e:  # noqa: BLE001
                    out.append((p, None, str(e)))
            return out

        def ok(results):
            clips, errors, notes = [], [], []
            ok_results = [(p, info) for p, info, err in results if info is not None]
            errors = [f"{os.path.basename(p)}: {err}" for p, info, err in results if info is None]
            ok_results.sort(key=lambda x: (x[1].creation_utc or 0, os.path.basename(x[0]).lower()))
            proj = st.project
            for p, info in ok_results:
                c = Clip(path=os.path.abspath(p), info=info)
                notes.append(f"{c.name}: {auto_place(proj, c, st.telemetry, st.playhead)}")
                proj.clips.append(c)
                clips.append(c)
            for c in clips:  # AddClips re-adds them so the import is one undo step
                proj.clips.remove(c)
            if clips:
                st.undo.push(AddClips(st, clips, f"Add {len(clips)} video(s)"))
                st.select_clip(clips[0].id)
                st.set_playhead(clips[0].start)
                self.timeline.tl.fit_all()
            if errors:
                QMessageBox.warning(self, "Some files couldn't be read", "\n".join(errors))
            if notes:
                st.status.emit("; ".join(notes))
            if clips and st.telemetry is None:
                st.status.emit("Videos added. Import the flight log too, then they'll be placed by their "
                               "recording time.")

        self._run("Reading video files", job, ok)

    # ---------------------------------------------------------------- project files

    def maybe_save(self) -> bool:
        if not self.state.dirty or (not self.state.project.clips and not self.state.project.log_path):
            return True
        r = QMessageBox.question(self, APP_NAME, "Save changes to this project?",
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                 QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            return self.save()
        return r == QMessageBox.StandardButton.Discard

    def new_project(self) -> None:
        if self.maybe_save():
            self.playback.pause()
            self.state.set_project(Project())

    def open_project(self, path: str | None = None) -> None:
        if not self.maybe_save():
            return
        if not path:
            start = self.settings.value("last_dir", os.path.expanduser("~"))
            path, _ = QFileDialog.getOpenFileName(self, "Open project", start, f"{APP_NAME} project (*{PROJECT_EXT})")
            if not path:
                return
        try:
            proj = Project.load(path)
        except Exception as e:  # noqa: BLE001
            self._error("Opening the project", str(e))
            return
        self.playback.pause()
        self.state.set_project(proj)
        self._add_recent(path)
        missing = [c.name for c in proj.clips if not os.path.exists(c.path)]
        if proj.log_path and not os.path.exists(proj.log_path):
            missing.insert(0, os.path.basename(proj.log_path))
        if missing:
            QMessageBox.warning(self, "Missing files", "These files can't be found (moved or deleted?):\n\n" +
                                "\n".join(missing))
        if proj.log_path and os.path.exists(proj.log_path):
            def after():
                self.state.set_dirty(False)
                self.timeline.tl.fit_all()
            self.load_log(proj.log_path, then=after, ask=False)

    def save(self) -> bool:
        if not self.state.project.path:
            return self.save_as()
        try:
            self.state.project.save()
        except OSError as e:
            self._error("Saving", str(e))
            return False
        self.state.set_dirty(False)
        self._add_recent(self.state.project.path)
        self.state.status.emit(f"Saved {self.state.project.path}")
        return True

    def save_as(self) -> bool:
        p = self.state.project
        base = p.log_path or (p.clips[0].path if p.clips else os.path.join(os.path.expanduser("~"), "flight"))
        start = os.path.splitext(base)[0] + PROJECT_EXT
        path, _ = QFileDialog.getSaveFileName(self, "Save project", start, f"{APP_NAME} project (*{PROJECT_EXT})")
        if not path:
            return False
        if not path.lower().endswith(PROJECT_EXT):
            path += PROJECT_EXT
        p.path = os.path.abspath(path)
        self._update_title()
        return self.save()

    def _add_recent(self, path: str) -> None:
        if not self._remember_layout:
            return
        rec = [r for r in (self.settings.value("recent", []) or []) if r != path]
        rec.insert(0, path)
        self.settings.setValue("recent", rec[:8])

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        for r in rec:
            a = self.recent_menu.addAction(r)
            a.triggered.connect(lambda _=False, path=r: self.open_project(path))
        if not rec:
            self.recent_menu.addAction("(none)").setEnabled(False)

    # ---------------------------------------------------------------- export / help

    def export_dialog(self) -> None:
        if not self.state.project.clips:
            QMessageBox.information(self, "Nothing to export", "Import some video files first.")
            return
        self.playback.pause()
        dlg = ExportDialog(self.state, self)
        dlg.exec()

    def show_help(self) -> None:
        QMessageBox.information(self, "Quick start", QUICK_START)

    def show_about(self) -> None:
        try:
            ff = ffmpeg_tools.ffmpeg()
        except FileNotFoundError:
            ff = "not found"
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<b>{APP_NAME}</b> {__version__}<br>Overlay ArduPilot flight data on your videos."
                          f"<br><br>FFmpeg: {ff}")

    def create_demo(self) -> None:
        if not self.maybe_save():
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose an empty folder for the demo files",
                                                  os.path.join(os.path.expanduser("~"), "Documents"))
        if not folder:
            return
        folder = os.path.join(folder, "FlightOverlay demo")
        from ..demo import make_demo_project

        def job(progress, cancel):
            return make_demo_project(folder, progress=lambda f: progress(f, "Making demo videos"))

        self._run("Creating demo project", job, lambda path: self.open_project(path), cancellable=False)

    # ---------------------------------------------------------------- window events

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.import_files(paths)

    def closeEvent(self, e) -> None:
        if not self.maybe_save():
            e.ignore()
            return
        self.playback.pause()
        for w in list(self._workers):
            w.cancel()
            w.wait(3000)
        if self._remember_layout:
            self.settings.setValue("geometry", self.saveGeometry())
            self.settings.setValue("windowState", self.saveState())
        e.accept()
        QApplication.instance().quit()
