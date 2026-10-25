# FlightOverlay

Put ArduPilot flight data on top of your flight videos.

Import a flight log (`.bin` from the flight controller's SD card, or a `.tlog` from Mission Planner) plus the
videos from your phone or action camera. Line them up on a timeline, pick which data to show, and export one MP4
with the overlay burned in.

- **Overlay data:** airspeed, ground speed, altitude, climb rate, throttle, flight mode, battery, distance to
  home, RSSI, GPS, wind and more.
- **Gauges:** artificial horizon (instrument or HUD lines), heading tape, speed/altitude tapes, a mini-map of the
  GPS track, home arrow, wind arrow and flight timer.
- **Automatic placement:** videos are placed using their recording time and the GPS time in the log.
- **Sync tool:** mark one moment in the video, click the same moment on the log graph, then click Align.
- **Two cameras:** shown picture-in-picture or side by side where they overlap.
- **Fast export:** uses the NVIDIA (NVENC) encoder when available. HDR phone video is converted to normal colours.

## Getting it running

### Option 1: build the standalone app (recommended)

In PowerShell, from this folder:

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1 -Shortcut
```

The build script:

1. sets up Python packages
2. downloads FFmpeg
3. builds `dist\FlightOverlay\FlightOverlay.exe`
4. puts a shortcut on your desktop

The `dist\FlightOverlay` folder is self-contained: copy it anywhere, or to another Windows PC. No Python is
needed there.

### Option 2: run from source

Double-click `run.bat`. The first run sets up a Python environment and downloads FFmpeg.

Requires Python 3.12 or newer (`py` launcher).

## How to use it

1. **Import files.** Use *Import files* (Ctrl+I), or drag them onto the window. Select the log and all the videos
   from the flight at once.
2. **Check the sync.** Scrub the timeline or press Space to play, and compare the video with the overlay. The
   horizon during a turn, or the moment of launch, are good checks. Clips placed from timestamps show `~auto`
   until you confirm them.
3. **Fix the sync** with the *Clip & Sync* panel:
   1. Pause on a moment you can recognise: hand launch, wheels leaving the ground, touchdown, or a sharp bank.
   2. Click **Mark video** (or press **M**).
   3. Click the same moment on the log graph, or pick an event such as *Throttle up* or *Takeoff*.
   4. Click **Align**. Later clips from the same camera (e.g. GoPro chapter files) move with it.

   For fine-tuning, select a clip and press `,` / `.` to move it one frame, or `Shift+,` / `Shift+.` to move it
   one second. You can also drag clips on the timeline.
4. **Choose the data to show** in the *Overlay* panel.
   - Tick or untick items, or pick a ready-made layout (Dashboard, Plane HUD or Minimal) and click *Apply*.
   - In the preview, drag items to move them and use the mouse wheel over an item to resize it.
   - Right-click an item in the preview to hide it.
5. **Second camera (optional).** Put a clip on the **Cam B** track. Where it overlaps Cam A it's shown
   picture-in-picture. Drag the small picture in the preview to move it, or choose *side by side* in the export
   settings.
6. **Export part of the flight (optional).** Set IN and OUT markers (keys **I** and **O**).
7. **Export video** (Ctrl+E). Choose resolution, quality and sound source, then click *Export*.

Save your work as a project file (`.fovproj`) with Ctrl+S. To try everything without your own files, use
*Help → Create demo project*.

### Keyboard shortcuts

| Key | Action |
|---|---|
| Space | Play / pause |
| ← / → | Step one frame |
| Shift+← / Shift+→ | Step one second |
| Home / End | Jump to the start / end |
| `,` / `.` | Move the selected clip one frame earlier / later |
| Shift+`,` / Shift+`.` | Move the selected clip one second |
| M | Mark the video moment (sync tool) |
| I / O | Set the export IN / OUT markers |
| Delete | Remove the selected clip |
| Ctrl+Z / Ctrl+Y | Undo / redo |

## Tips and troubleshooting

- **Where the logs are.**
  - `.bin` logs are in `APM\LOGS` on the flight controller's SD card, or you can download them with Mission
    Planner (*DataFlash Logs → Download*).
  - `.tlog` files are in `Documents\Mission Planner\logs`.
  - The `.bin` log has more data at a higher rate, so use it if you can.
- **A video lands hours away from the flight.** Many action cameras store local time as if it were UTC. The app
  detects whole-hour offsets automatically. You can also set *Clock correction* in the Clip & Sync panel, then
  click *Place clip from timestamp*.
- **A video is consistently a few seconds off.** Camera clocks drift. Use the sync tool once, and the other
  files from that camera follow.
- **No GPS time in the log** (no GPS fix during logging). Videos can't be placed automatically. Drag them into
  place and use the sync tool.
- **Some data is greyed out** in the Overlay panel. That log doesn't contain it; for example, there's no
  airspeed without an airspeed sensor. Hover over the item to see why.
- **Export speed.** A 1080p export runs at roughly twice real time on a desktop PC, so a 10-minute flight takes
  about 5 minutes. 4K source footage takes longer. The graphics-card encoder helps most with 4K and H.265.
- **Logs load faster the second time.** Loaded logs are cached in `%LOCALAPPDATA%\FlightOverlay\cache`. It's
  safe to delete.

## Command-line tools

Useful for checking a log or scripting exports:

```
python -m flightoverlay.cli dump-channels flight.bin --at 120 300
python -m flightoverlay.cli render-frame flight.bin --t 150 --out frame.png --preset "Plane HUD"
python -m flightoverlay.cli export my_flight.fovproj --out result.mp4
python -m flightoverlay.cli demo C:\temp\demo
```

From the packaged build, use `FlightOverlay.exe --cli <command> ...`. Run `FlightOverlay.exe --self-test
report.txt` to check that an install works.

## For developers

```
flightoverlay/
  core/        project model, timeline segments, sync maths, ffmpeg/ffprobe helpers
  telemetry/   log loading (pymavlink), data channels, derived values, events, units
  overlay/     overlay layout, presets and the QPainter widgets (shared by preview and export)
  render/      export: FFmpeg filter graphs, per-segment encoding, audio, joining
  ui/          the PySide6 interface
tests/         pytest suite (uses a simulated flight; export tests need FFmpeg)
tools/         FFmpeg download, icon, UI smoke test
```

Run the tests with `.venv\Scripts\python -m pytest`.

FFmpeg is downloaded by `tools\get_ffmpeg.ps1` (a GPL build from gyan.dev). Its licence is copied next to the
binaries.
