---
name: capture
description: Taking stills and screen recordings on a phone as evidence -- device shot and record start/stop on Android and iPhone, quieting notifications first, the .start clock file that lines video up with logs, driving a take fast, the screenrecord traps (180 s limit, -INT only, nohup, no host-side screencap loop), checking a pull with ffprobe, and handing the take to video-trim. Read before recording anything someone else will watch.
metadata:
  type: reference
  skill: device-testing
---

# Capture: stills and video

`D="python3 <skill>/scripts/device.py"` below. Files land under the scratch root from config
(`SCRATCH_ROOT`, see `config.example.env`).

## Before any capture

1. **Quiet the phone.** `$D quiet on`. A notification from another app (a payment, a message, a
   doorbell) leaks personal info and covers the UI under test. Android is silenced from the host;
   iOS has no host-side switch, so the person turns on Do Not Disturb and Settings >
   Notifications > Show Previews > Never, then `$D quiet on --confirmed`. `record start` checks it;
   `DEVICE_QUIET_REQUIRED=1` makes an unquiet start fail instead of warn.
2. **Unlock and keep awake.** A locked phone records the lockscreen and ignores input
   (`record start` refuses). On Android, `settings put global stay_on_while_plugged_in 7` keeps it
   awake on USB; put the old value back after.
3. **Set the scene before starting.** Install, app state, the starting screen -- all before the
   recorder runs. Every second recorded is a second someone scrubs through.

## Stills

`$D shot [--max-height N]` prints the PNG path. It is the real composited screen, overlays and
system dialogs included. A black frame usually means the screen is off, not that the app is
broken.

## Video

```bash
$D record start take1          # writes video/take1.start (device clock), starts the recorder
# ... drive the scenario ...
$D record stop take1           # stops cleanly, pulls, prints video/take1.mp4
$D record start long --segments   # Android: chains 170 s clips past the 3-minute limit
```

- **Android** uses `screenrecord` on the device, full resolution. iPhone uses QuickTime's USB
  screen capture at full resolution up to 60 fps (the terminal app needs Camera access; without it
  it falls back to ~5 fps screenshots). iOS capture needs the cable even when install works over
  the network.
  `DEVICE_IOS_RECORD=device` records with the device's own Screen Recording instead (what App Review
  asks for), then `media pull` exports it; see [ios.md](ios.md).
- **The `.start` file is the clock.** One line, the device's wall-clock time when recording
  began. `start + offset in the video` = the log timestamp, so a frame can be matched to its log
  line, and `video-trim` carries it through every cut.

### Drive the take fast

- **Wait on evidence, not time.** Clear the log before the take, then wait for the line that
  proves each step landed, with a timeout that fails the take (`$D tap X Y --wait '<regex>'`,
  `$D wait '<regex>'`).
- **No investigation mid-take.** Reading code or logs while recording is how a 6-minute capture
  ends up 80% frozen. Stop, investigate, record a fresh take.
- **Stop on the last evidence line**, not after a settle sleep; `video-trim` keeps a beat at
  every freeze edge.

### Android traps (by hand, without `record`)

- Default `--time-limit` is **180 s**, and a longer take is truncated with no error. Use
  `--time-limit 0` or `--segments`.
- `adb shell '... &'` without `nohup` and redirects never returns: the background process holds
  the shell's stdout.
- **Stop with `-INT` only.** `screenrecord` writes the MP4 index on a clean interrupt; anything
  harsher leaves a file with no moov atom that will not play and cannot be repaired.
- **Never poll `screencap` while recording** -- a host-side screenshot loop wedges `screenrecord`.
- ~1 MB per second at 8 Mbps full resolution; a 2-minute capture is over 100 MB. Delete it from
  the device afterwards.

## After the take

1. **Check the pull, not the file size.**
   `ffprobe -v error -show_entries format=duration -of default=nw=1 video/take1.mp4`. A duration
   much shorter than the flow means the recording died early (time limit, or the app crashed and
   took the surface with it).
2. **Scrub overlays, then trim a copy** with the [video-trim](../../video-trim/SKILL.md) skill:
   [scrub_overlays.py](../../video-trim/scripts/scrub_overlays.py) cuts any banner that slipped
   through, [trim.py](../../video-trim/scripts/trim.py) cuts the frozen stretches,
   [cut_windows.py](../../video-trim/scripts/cut_windows.py) keeps explicit windows when a held
   screen is the evidence. Keep the original until the work is done.
3. **Check every frame you hand over** -- [frame_strip.py](../../video-trim/scripts/frame_strip.py)
   makes a contact sheet -- for a banner,
   a debug overlay or a cut-short evidence screen before it goes anywhere.
4. A video encoded from frames (PNG dumps) is the evidence; delete the frame folder once the mp4
   plays, keeping a couple of named stills.
