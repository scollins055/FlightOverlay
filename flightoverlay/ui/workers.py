"""Running slow jobs (log loading, probing, exporting) off the UI thread."""
from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QThread, Signal


class Cancelled(Exception):
    pass


class Worker(QThread):
    """Runs fn(progress=..., cancel=...) in a thread. progress(fraction, message)."""

    progress = Signal(float, str)
    succeeded = Signal(object)
    failed = Signal(str, str)      # short message, details
    cancelled = Signal()

    def __init__(self, fn: Callable, parent=None):
        super().__init__(parent)
        self.fn = fn
        self._cancel = False
        self.cancel_hook: Callable[[], None] | None = None

    def cancel(self) -> None:
        self._cancel = True
        if self.cancel_hook:
            try:
                self.cancel_hook()
            except Exception:
                pass

    def is_cancelled(self) -> bool:
        return self._cancel

    def run(self) -> None:
        try:
            res = self.fn(progress=lambda f, m="": self.progress.emit(float(f), str(m)), cancel=self.is_cancelled)
        except Exception as e:  # noqa: BLE001 - report everything to the UI
            name = type(e).__name__
            if self._cancel or name in ("LoadCancelled", "ExportCancelled", "Cancelled"):
                self.cancelled.emit()
            else:
                self.failed.emit(str(e) or name, traceback.format_exc())
            return
        if self._cancel:
            self.cancelled.emit()
        else:
            self.succeeded.emit(res)
