"""Undoable edits."""
from __future__ import annotations

from PySide6.QtGui import QUndoCommand

from ..core.project import Clip


class ClipEdit(QUndoCommand):
    def __init__(self, state, clip_id: str, before: dict, after: dict, text: str, merge_key: int = -1):
        super().__init__(text)
        self.state = state
        self.clip_id = clip_id
        self.before = before
        self.after = after
        self._merge_key = merge_key

    def id(self) -> int:
        return self._merge_key

    def mergeWith(self, other) -> bool:
        if not isinstance(other, ClipEdit) or other.clip_id != self.clip_id or other._merge_key != self._merge_key:
            return False
        self.after = other.after
        return True

    def redo(self) -> None:
        self.state.apply_clip_fields(self.clip_id, self.after)

    def undo(self) -> None:
        self.state.apply_clip_fields(self.clip_id, self.before)


class AddClips(QUndoCommand):
    def __init__(self, state, clips: list[Clip], text: str = "Add videos"):
        super().__init__(text)
        self.state = state
        self.clips = clips

    def redo(self) -> None:
        for c in self.clips:
            if c not in self.state.project.clips:
                self.state.project.clips.append(c)
        self.state.clipsChanged.emit()

    def undo(self) -> None:
        for c in self.clips:
            if c in self.state.project.clips:
                self.state.project.clips.remove(c)
        if self.state.selected_clip_id in {c.id for c in self.clips}:
            self.state.select_clip(None)
        self.state.clipsChanged.emit()


class RemoveClip(QUndoCommand):
    def __init__(self, state, clip: Clip):
        super().__init__(f"Remove {clip.name}")
        self.state = state
        self.clip = clip

    def redo(self) -> None:
        if self.clip in self.state.project.clips:
            self.state.project.clips.remove(self.clip)
        if self.state.selected_clip_id == self.clip.id:
            self.state.select_clip(None)
        self.state.clipsChanged.emit()

    def undo(self) -> None:
        if self.clip not in self.state.project.clips:
            self.state.project.clips.append(self.clip)
        self.state.clipsChanged.emit()
