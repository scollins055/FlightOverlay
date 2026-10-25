"""Preview playback: one media player per camera track, driven by the shared playhead."""
from __future__ import annotations

import os

from PySide6.QtCore import QElapsedTimer, QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QImage, QTransform
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink

from ..core.project import Clip

PS = QMediaPlayer.PlaybackState
MS = QMediaPlayer.MediaStatus

# Re-seek a playing video if it drifts further than this from the playhead (ms).
DRIFT_MS = 300


class TrackPlayer(QObject):
    frameReady = Signal(int)

    def __init__(self, track: int, parent=None):
        super().__init__(parent)
        self.track = track
        self.player = QMediaPlayer(self)
        self.sink = QVideoSink(self)
        self.audio = QAudioOutput(self)
        self.player.setVideoSink(self.sink)
        self.player.setAudioOutput(self.audio)
        self.sink.videoFrameChanged.connect(self._on_frame)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.errorOccurred.connect(self._on_error)
        self.path: str | None = None
        self.clip: Clip | None = None
        self.ready = False
        self.want: tuple[float, bool] = (0.0, False)
        self.error = ""
        self._frame: QVideoFrame | None = None
        self._image: QImage | None = None
        self._last_seek = -1

    # -- frames are converted lazily, only when the preview paints
    def image(self) -> QImage | None:
        if self._image is None and self._frame is not None and self._frame.isValid():
            img = self._frame.toImage()
            if not img.isNull():
                img = self._fix_rotation(img)
                self._image = img
        return self._image

    def _fix_rotation(self, img: QImage) -> QImage:
        info = self.clip.info if self.clip else None
        if info is None or info.rotation not in (90, 180, 270):
            return img
        # Recent Qt versions already apply the rotation; only fix frames that are clearly still sideways.
        want_portrait = info.height > info.width
        is_portrait = img.height() > img.width()
        if info.rotation in (90, 270) and want_portrait != is_portrait:
            # ffprobe reports the display-matrix angle counter-clockwise
            return img.transformed(QTransform().rotate(-info.rotation % 360))
        return img

    def _on_frame(self, frame: QVideoFrame) -> None:
        self._frame = QVideoFrame(frame)
        self._image = None
        self.frameReady.emit(self.track)

    def _on_status(self, st) -> None:
        if st in (MS.LoadedMedia, MS.BufferedMedia, MS.BufferingMedia) and not self.ready:
            self.ready = True
            self._apply(force_seek=True)
        elif st == MS.InvalidMedia:
            self.error = "This video can't be played in the preview"
            self.frameReady.emit(self.track)

    def _on_error(self, err, msg="") -> None:
        if err != QMediaPlayer.Error.NoError:
            self.error = msg or str(err)
            self.frameReady.emit(self.track)

    def show(self, clip: Clip | None, src_t: float, playing: bool) -> None:
        if clip is None or not os.path.exists(clip.path):
            if self.path is not None:
                self.player.stop()
                self.player.setSource(QUrl())
                self.path = None
                self.clip = None
                self._frame = None
                self._image = None
                self.ready = False
                self.frameReady.emit(self.track)
            return
        self.clip = clip
        self.want = (src_t, playing)
        if clip.path != self.path:
            self.path = clip.path
            self.ready = False
            self.error = ""
            self._frame = None
            self._image = None
            self._last_seek = -1
            self.player.setSource(QUrl.fromLocalFile(clip.path))
            return
        if self.ready:
            self._apply()

    def _apply(self, force_seek: bool = False) -> None:
        src_t, playing = self.want
        fps = self.clip.info.fps if self.clip and self.clip.info and self.clip.info.fps > 0 else 30.0
        if playing:
            pos = max(0, int(round(src_t * 1000)))
        else:
            # Aim at the middle of the nearest frame: positions are whole ms but frames start
            # at fractional ms, so aiming at the frame start can show the previous frame.
            k = max(0, round(src_t * fps))
            pos = int(round((k + 0.5) / fps * 1000))
        state = self.player.playbackState()
        if playing:
            if state != PS.PlayingState:
                self.player.setPosition(pos)
                self.player.play()
            elif force_seek or abs(self.player.position() - pos) > DRIFT_MS:
                self.player.setPosition(pos)
        else:
            if state != PS.PausedState:
                self.player.pause()
            if force_seek or pos != self._last_seek:
                self._last_seek = pos
                self.player.setPosition(pos)

    def set_muted(self, muted: bool) -> None:
        self.audio.setMuted(muted)


class PlaybackController(QObject):
    playingChanged = Signal(bool)
    frameReady = Signal()

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.tracks = [TrackPlayer(0, self), TrackPlayer(1, self)]
        for tp in self.tracks:
            tp.frameReady.connect(lambda _t: self.frameReady.emit())
        self.timer = QTimer(self)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self._tick)
        self.clock = QElapsedTimer()
        self.playing = False
        self.muted = False
        self._t0 = 0.0
        self._internal = False
        state.playheadChanged.connect(self._on_playhead)
        state.clipsChanged.connect(self.refresh)
        state.exportSettingsChanged.connect(self.refresh)
        state.projectReset.connect(self.pause)

    def toggle(self) -> None:
        self.pause() if self.playing else self.play()

    def play(self) -> None:
        if self.playing:
            return
        lo, hi = self.state.extent()
        if self.state.playhead >= hi - 0.05:
            self.state.set_playhead(lo)
        self.playing = True
        self._t0 = self.state.playhead
        self.clock.start()
        self.timer.start()
        self.refresh()
        self.playingChanged.emit(True)

    def pause(self) -> None:
        if not self.playing:
            self.refresh()
            return
        self.playing = False
        self.timer.stop()
        self.refresh()
        self.playingChanged.emit(False)

    def step(self, seconds: float) -> None:
        self.pause()
        self.state.set_playhead(self.state.playhead + seconds)

    def _tick(self) -> None:
        t = self._t0 + self.clock.elapsed() / 1000.0
        _, hi = self.state.extent()
        self._internal = True
        try:
            self.state.set_playhead(min(t, hi))
        finally:
            self._internal = False
        self.refresh()
        if t >= hi:
            self.pause()

    def _on_playhead(self, t: float) -> None:
        if self._internal:
            return
        if self.playing:
            self._t0 = t
            self.clock.restart()
        self.refresh()

    def refresh(self) -> None:
        p = self.state.project
        t = self.state.playhead
        clips = [p.clip_at(0, t), p.clip_at(1, t)]
        for tp, clip in zip(self.tracks, clips):
            tp.show(clip, (t - clip.offset) if clip else 0.0, self.playing)
        # audio: follow the export audio choice, fall back to the other camera
        pref = p.export.audio
        order = [0, 1] if pref != "B" else [1, 0]
        audible = None
        if pref != "none" and not self.muted:
            for i in order:
                c = clips[i]
                if c is not None and c.info is not None and c.info.has_audio:
                    audible = i
                    break
        for i, tp in enumerate(self.tracks):
            tp.set_muted(i != audible)

    def set_muted(self, m: bool) -> None:
        self.muted = m
        self.refresh()
