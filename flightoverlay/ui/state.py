"""Shared application state that every panel observes."""
from __future__ import annotations

from fractions import Fraction

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoStack

from ..core.project import Clip, Project
from ..telemetry.series import Telemetry

CLIP_FIELDS = ("offset", "track", "trim_in", "trim_out", "tz_shift_h", "stamp_is_end", "verified", "auto_placed")


class AppState(QObject):
    projectReset = Signal()           # a different project was loaded / created
    clipsChanged = Signal()           # clips added, removed, moved or trimmed
    telemetryChanged = Signal()
    playheadChanged = Signal(float)
    selectionChanged = Signal(str)    # clip id ('' = none)
    overlayChanged = Signal()         # overlay layout, style or units
    widgetSelected = Signal(str)      # overlay widget id
    exportSettingsChanged = Signal()
    rangeChanged = Signal()
    syncMarksChanged = Signal()
    dirtyChanged = Signal(bool)
    status = Signal(str)

    def __init__(self):
        super().__init__()
        self.project = Project()
        self.telemetry: Telemetry | None = None
        self.playhead = 0.0
        self.selected_clip_id = ""
        self.selected_widget_id = ""
        self.undo = QUndoStack(self)
        self.video_mark: tuple[str, float] | None = None   # (clip id, seconds into the clip)
        self.log_mark: float | None = None
        self.pick_log_mark = False                          # next click on the log graph sets log_mark
        self.dirty = False
        self.undo.indexChanged.connect(self._undo_index_changed)

    # ---------------------------------------------------------------- project

    def set_project(self, project: Project, telemetry: Telemetry | None = None) -> None:
        self.project = project
        self.telemetry = telemetry
        self.selected_clip_id = ""
        self.video_mark = None
        self.log_mark = None
        self.pick_log_mark = False
        self.undo.clear()
        span = project.clips_span()
        self.playhead = span[0] if span else 0.0
        self.set_dirty(False)
        self.projectReset.emit()
        self.telemetryChanged.emit()
        self.clipsChanged.emit()
        self.overlayChanged.emit()
        self.playheadChanged.emit(self.playhead)

    def set_telemetry(self, tel: Telemetry | None, path: str | None) -> None:
        self.telemetry = tel
        self.project.log_path = path
        self.mark_dirty()
        self.telemetryChanged.emit()
        self.overlayChanged.emit()

    def _undo_index_changed(self, _idx: int) -> None:
        self.mark_dirty()

    def mark_dirty(self) -> None:
        self.set_dirty(True)

    def set_dirty(self, d: bool) -> None:
        if d != self.dirty:
            self.dirty = d
            self.dirtyChanged.emit(d)

    # ---------------------------------------------------------------- time

    def fps(self) -> Fraction:
        return self.project.output_fps()

    def frame_dur(self) -> float:
        return 1.0 / float(self.fps())

    def set_playhead(self, t: float) -> None:
        lo, hi = self.extent()
        t = max(lo, min(hi, t))
        if abs(t - self.playhead) > 1e-9:
            self.playhead = t
            self.playheadChanged.emit(t)

    def extent(self) -> tuple[float, float]:
        """Time range covered by the log and all clips."""
        lo, hi = 0.0, 0.0
        if self.telemetry is not None:
            hi = self.telemetry.duration
        span = self.project.clips_span()
        if span:
            if self.telemetry is None:
                lo, hi = span
            else:
                lo, hi = min(lo, span[0]), max(hi, span[1])
        if hi - lo < 1.0:
            hi = lo + 60.0
        return lo, hi

    # ---------------------------------------------------------------- selection

    def selected_clip(self) -> Clip | None:
        return self.project.clip(self.selected_clip_id) if self.selected_clip_id else None

    def select_clip(self, cid: str | None) -> None:
        cid = cid or ""
        if cid != self.selected_clip_id:
            self.selected_clip_id = cid
            self.selectionChanged.emit(cid)

    def select_widget(self, wid: str) -> None:
        self.selected_widget_id = wid
        self.widgetSelected.emit(wid)

    # ---------------------------------------------------------------- clip edits (undoable)

    @staticmethod
    def clip_fields(c: Clip) -> dict:
        return {k: getattr(c, k) for k in CLIP_FIELDS}

    def edit_clip(self, clip: Clip, text: str, merge_key: int = -1, **changes) -> None:
        from .commands import ClipEdit
        before = self.clip_fields(clip)
        after = dict(before)
        after.update(changes)
        if after == before:
            return
        self.undo.push(ClipEdit(self, clip.id, before, after, text, merge_key))

    def commit_clip_change(self, clip: Clip, before: dict, text: str) -> None:
        """Record an edit already applied directly to the clip (e.g. by dragging)."""
        from .commands import ClipEdit
        after = self.clip_fields(clip)
        if after != before:
            self.undo.push(ClipEdit(self, clip.id, before, after, text))

    def apply_clip_fields(self, clip_id: str, fields: dict) -> None:
        c = self.project.clip(clip_id)
        if c is None:
            return
        for k, v in fields.items():
            setattr(c, k, v)
        self.clipsChanged.emit()

    # ---------------------------------------------------------------- overlay / export

    def overlay_edited(self) -> None:
        self.mark_dirty()
        self.overlayChanged.emit()

    def export_edited(self) -> None:
        self.mark_dirty()
        self.exportSettingsChanged.emit()

    def set_range(self, a: float | None, b: float | None) -> None:
        self.project.range_in, self.project.range_out = a, b
        self.mark_dirty()
        self.rangeChanged.emit()
        self.clipsChanged.emit()

    def set_sync_marks(self, video: tuple[str, float] | None | bool = False, log: float | None | bool = False):
        if video is not False:
            self.video_mark = video
        if log is not False:
            self.log_mark = log
        self.syncMarksChanged.emit()
