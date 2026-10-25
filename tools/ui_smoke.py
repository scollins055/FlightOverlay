"""Drive the GUI without a person: open a project, scrub, sync, play, and save screenshots.

    python tools/ui_smoke.py demo/demo.fovproj demo/frames
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["FLIGHTOVERLAY_TEST"] = "1"
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(cond, timeout=20.0, step=50) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        wait(step)
    return False


def main() -> int:
    proj_path, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    from flightoverlay.app import _dark_palette
    from flightoverlay.ui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    _dark_palette(app)
    win = MainWindow()
    win.resize(1600, 1000)
    win.show()
    st = win.state

    def shot(name):
        wait(150)
        win.grab().save(os.path.join(out_dir, name))
        print("saved", name, flush=True)

    shot("ui_0_empty.png")
    win.open_project(proj_path)
    ok = wait_until(lambda: st.telemetry is not None)
    print("telemetry loaded:", ok, "clips:", len(st.project.clips), flush=True)
    st.set_playhead(95.0)
    ok = wait_until(lambda: all(tp.image() is not None for tp in win.playback.tracks if tp.path), 15)
    print("frames ready:", ok, [tp.path and os.path.basename(tp.path) for tp in win.playback.tracks], flush=True)
    wait(500)
    shot("ui_1_pip.png")

    # sync tool: camera A is 2 s late in the demo. Mark a video moment and align to the same log time + error
    clip = st.project.clips_on(0)[0]
    off0 = clip.offset
    st.select_clip(clip.id)
    st.set_playhead(clip.offset + 6.0)  # 6 s into the clip
    wait(300)
    win.clip_panel.mark_video()
    print("video mark:", st.video_mark, "pick mode:", st.pick_log_mark, flush=True)
    true_log = 64.0 + 6.0          # demo clip really starts at log 64.0
    st.set_sync_marks(log=true_log)
    win.clip_panel.do_align()
    later = st.project.clips_on(0)[1]
    print(f"aligned offset {off0:.2f} -> {clip.offset:.2f}; next clip now {later.offset:.2f}", flush=True)
    st.undo.undo()
    print(f"after undo: {clip.offset:.2f} / {later.offset:.2f}", flush=True)
    st.undo.redo()
    print(f"after redo: {clip.offset:.2f} / {later.offset:.2f}", flush=True)
    wait_until(lambda: win.playback.tracks[0].image() is not None, 10)
    wait(600)
    shot("ui_2_aligned.png")

    # playback for 2 seconds
    t_before = st.playhead
    win.playback.play()
    wait(2000)
    win.playback.pause()
    print(f"played from {t_before:.2f} to {st.playhead:.2f}", flush=True)
    shot("ui_3_after_play.png")

    # overlay panel: switch to HUD preset
    idx = win.overlay_panel.preset.findText("Plane HUD")
    win.overlay_panel.preset.setCurrentIndex(idx)
    win.overlay_panel._apply_preset()
    win.dock_overlay.raise_()
    shot("ui_4_hud.png")

    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    # drag a clip on the timeline with the mouse
    tl = win.timeline.tl
    win.playback.pause()
    c2 = st.project.clips_on(0)[1]
    st.select_clip(None)
    row = tl.row_rect(0)
    x = int(tl.x_of((c2.start + c2.end) / 2))
    y = int(row.center().y())
    off_before, n_undo = c2.offset, st.undo.count()
    QTest.mousePress(tl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, QPoint(x, y))
    for dx in range(0, 61, 10):
        QTest.mouseMove(tl, QPoint(x + dx, y))
    QTest.mouseRelease(tl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, QPoint(x + 60, y))
    print(f"timeline drag: offset {off_before:.2f} -> {c2.offset:.2f} (expected +{60 / tl.pps:.2f}), "
          f"undo steps {n_undo} -> {st.undo.count()}", flush=True)

    # keyboard nudge of the selected clip
    o = c2.offset
    win.nudge_selected(1, "f")
    win.nudge_selected(1, "f")
    print(f"nudge 2 frames: {o:.4f} -> {c2.offset:.4f}", flush=True)

    # drag an overlay item in the preview
    cfg = st.project.overlay.get("battery")
    fr = win.preview.frame_rect()
    r = win.preview.renderer.widget_rect(cfg, fr.width(), fr.height()).translated(fr.topLeft())
    p0 = r.center().toPoint()
    x0 = cfg.x
    QTest.mousePress(win.preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p0)
    QTest.mouseMove(win.preview, p0 + QPoint(40, -30))
    QTest.mouseMove(win.preview, p0 + QPoint(80, -60))
    QTest.mouseRelease(win.preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p0 + QPoint(80, -60))
    print(f"preview drag battery: x {x0:.3f} -> {cfg.x:.3f}", flush=True)
    shot("ui_5_dragged.png")

    # new project + import through the window (log and videos together)
    folder = os.path.dirname(os.path.abspath(proj_path))
    files = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith((".bin", ".mp4"))]
    st.set_dirty(False)
    win.new_project()
    win.import_files(files)
    ok = wait_until(lambda: st.telemetry is not None and len(st.project.clips) == 3, 30)
    print("import:", ok, [(c.name, c.track, round(c.offset, 1)) for c in st.project.clips], flush=True)

    # export through the dialog
    from flightoverlay.ui.export_dialog import ExportDialog
    st.set_range(st.project.clips_on(0)[0].start, st.project.clips_on(0)[0].start + 3.0)
    dlg = ExportDialog(st, win)
    dlg.out.setText(os.path.join(out_dir, "dialog_export.mp4"))
    dlg.show()
    dlg.start()
    ok = wait_until(lambda: dlg.worker is None, 120)
    print("dialog export:", ok, dlg.status.text(), flush=True)
    shot_dlg = dlg.grab()
    shot_dlg.save(os.path.join(out_dir, "ui_6_export_dialog.png"))
    dlg.close()

    st.set_dirty(False)
    win.close()
    QCoreApplication.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
