---
name: command-matrix
description: Covers the per-platform command table (Android vs iOS backends), output formats, exit codes and coordinate conventions. Read when you need a command variant or platform difference.
metadata:
  type: reference
  skill: device-testing
---

## Commands

| Command | Android | iOS |
|---|---|---|
| `check`, `version`, `install [--symbol]`, `build-symbol` | adb, the APK | devicectl, the IPA |
| `preflight [--keep-logs]` | lock check, stay-awake, logcat buffer, stop, clear | lock check, stop, restart the syslog collector |
| `launch [--timeout] [--input-server] [--url] [--accept-alerts [S]]`, `stop`, `focus` | monkey/am (`VIEW` intent for `--url`), pidof, `mCurrentFocus` | devicectl (`--payload-url`), app state from DVT notifications; `--accept-alerts` answers system alerts through WDA (opt-in) |
| `rotate left\|right` | auto-rotate off, user rotation a quarter turn | CoreDevice orientation service, works through the lock |
| `bg`, `resume-check [--hold] [--settle]` | HOME key | the Home button (CoreDevice HID) |
| `tap`, `swipe`, `key` (+ `--wait RE --timeout S`, `--norm`) | `adb input` | the app's input server; `key` maps HOME, POWER, VOLUME_*, DEL, ENTER |
| `count`, `log`, `wait` | logcat | a background `syslog live` collector, DEBUG dropped |
| `nav`, `nav-list` | profile + `wm size`/`density` | profile + display size, scale x 160 as density |
| `shot [--max-height N]`, `record start/stop [--segments]` | screencap, screenrecord (`--segments`: 170 s clips chained on the device, joined with ffmpeg, so a take passes 3 minutes) | DVT screenshots; video is QuickTime's USB screen capture, full resolution up to 60 fps (needs Camera access for the terminal's app, falls back to ~5 fps screenshots); `DEVICE_IOS_RECORD=device` uses the device's own Screen Recording (see ios.md) |
| `prefs [key]` | `run-as` cat of `APP_ANDROID_PREFS_FILE` | `devicectl device copy from` of `APP_IOS_PREFS_FILE` |
| `location set <lat> <lng>\|route <gpx\|csv> [--speed]\|clear` | mock GPS provider through `cmd location` (no helper app; the mock-location appop is set for the shell, `clear` restores it) | `dvt simulate-location`, kept by a holder process |
| `network <profile> [--relaunch [--input-server]]\|list\|clear` | exit 3, no host-side route | `dvt condition`; applies only to an app started under it, so `--relaunch` |
| `clipboard set <text>\|get` | exit 3, no host-side route | CoreDevice pasteboard, exact UTF-8 |
| `burst <prefix> <n> <gap> [--swipe X1,Y1,X2,Y2 ...]`, `sheet <out> <img>...` | n stills a gap apart (or swipe rounds with a settle, then a still) and a contact sheet, written under the scratch dir; needs Pillow | same |
| `log-clear`, `clear-data [--grant PERM]` | logcat -c; `pm clear` then `pm grant` (a first-run state) | log-clear only; clear-data exits 3 |
| `awake on\|off` | stay-on-while-plugged-in (`preflight` turns it on) | a host-held power assertion, display effect unmeasured: also set Auto-Lock to Never; `off` releases it |
| `media push <file...>\|hide-others\|unhide\|picker\|build\|install` | exit 3 | the Photos library through an on-device helper app (`scripts/ios_media_helper`); hide-others records what it hid, unhide restores exactly that, nothing is deleted |
| `run <plan> [--keep-going]`, `sleep S` | a plan file: one command per line, `finally:` always runs | same |
| `perf sample <s> [name]` | SurfaceFlinger frame times for the app surface, `/proc` CPU, `dumpsys meminfo` PSS | DVT graphics fps, process monitor CPU and footprint |
| `crashes [--since] [--pull DIR]` | dropbox crash, ANR and native-crash entries for the package | `crash ls`/`pull` filtered to the executable |
| `quiet on\|off\|status [--confirmed]` | do not disturb (`cmd notification set_dnd priority`), heads-up banners off, status-bar notification icons hidden through SystemUI demo mode; the previous values are saved per serial and `off` restores them | no host-side switch: prints the steps (Do Not Disturb, Show Previews > Never); `--confirmed` records that they were done, stale after 12 h |
| `forward`, `info`, `text`, `type`, `deselect`, `keyboard`, `appearance` | `adb forward`, input server, `cmd uimode night` | usbmux forward, input server, CoreDevice style |

Output: the established commands print plain text that harnesses grep (`saw ...`, `TIMEOUT ...`,
`stopped ...`); the input-server and appearance commands print JSON. Exit codes: 0 ok, 1 failed,
2 a wait or a check came back negative, 3 not supported on this platform.

Coordinates are pixels of the device screen (`check`/`nav` use the same space), or 0-1 with
`--norm`. On iOS that is the screenshot's pixel size, whatever the app renders at.
