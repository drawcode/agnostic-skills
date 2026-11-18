---
name: rules-and-plans
description: Covers the iOS touch model summary, plan files (device run) and the full rules that keep a pass honest. Read when writing a plan or deciding quiet, stop/bg or profile rules.
metadata:
  type: reference
  skill: device-testing
---

## Touch on iOS goes through the app

iOS 26 gives the host no touch injection without a signed XCTest runner. So input comes from an
**input server inside the app**: a small HTTP server on the device's loopback that injects touches
into the app's own input pipeline. The protocol is in
[input-server.md](input-server.md); any app can implement it. Run
`launch --input-server`, then `forward`, before the first tap. CoreDevice's HID service injects
host-side touches from iOS 27 on and can replace this path there. Setup and traps:
[ios.md](ios.md).

## Plans

`device run <plan>` runs a file of commands, one per line (whole-line `#` comments), in-process, and
prints `ok|FAIL <line>` with timing. It stops at the first failure unless `--keep-going`, always runs
the lines after a `finally:` line (stop the recording, stop the app), and exits 1 if any step failed.
A project keeps its smoke plans beside its profiles and wraps them in an add-on command.
`run` takes a path as given, else resolves it under the skills folder, so an add-on's plans are run as
`<wrapper> run <addon dir>/flows/<area>-<flow>.plan`. Name plans `<area>-<flow>.plan` (add `-<device>` when
coordinates are device-specific); put the device and the start state in the header comment; write one the
second time a flow is hand-driven. Plans stay in the add-on, never in the base. The `device-runner` agent
runs them; `scripts/device_tap_nudge.py` nudges toward them.

## Rules that keep a pass honest

- **Prove every action.** `--wait` on a log line, or a screenshot read afterwards. A tap at the
  wrong pixel lands on whatever is there and exits 0.
- **A profile belongs to one screen.** `#!size` refuses another resolution; a normalized profile is
  guarded by `#!density` instead, because density changes the layout itself.
- **Start clean, end put away: `stop` the app when you are done.** `preflight` before a pass. After
  any testing, screenshots or recording, `stop` the app: a game left running keeps the GPU busy and
  the phone gets hot. `bg` only to hand the app back mid-test (a resume or lifecycle check), and
  `stop` it once that check ends. State inherited from the last run is also how a pass measures the
  wrong thing.
- **Keep the device awake for the whole pass.** A screen that dims or locks mid-pass taps the lock
  screen and records it. `preflight` turns it on (`awake on|off` by hand): Android sets
  stay-on-while-plugged-in; iOS holds a power assertion from the host, unmeasured for the display, so
  until it is proven also set Auto-Lock to Never. `awake off` after.
- **Quiet the phone before any recording or screenshot.** A notification leaks personal info (a
  payment banner in a video is how this rule exists) and covers the UI under test. `preflight`
  and `record start` run `quiet` for you: Android is silenced automatically; iOS has no host-side
  switch, so it warns (`!! notifications not silenced`) until you do the steps and run
  `quiet on --confirmed`. Set `DEVICE_QUIET_REQUIRED=1` to make that warning a failure.
  `quiet off` when handing the device back.
- **Record coordinates normalized**: 0-1 of the screen from the top-left, sent with `--norm`, in
  notes, skills and profiles. Pixels belong to one screen; keep them only beside the fraction,
  labeled with that screen's size.
- **Anything you type twice belongs in a script**: generic steps here, app steps in the add-on.
  Scripts are Python 3 with a `.py` extension, never bash/zsh, so they run on any OS.
- **A release build's own log is readable on an emulator, not a phone.** `run-as` refuses a
  non-debuggable app; on a `google_apis` emulator image, `adb root` then `adb shell cat
  /data/data/<package>/files/...` reads it. Use an image whose API level has the feature under test
  (the screen-capture callback needs 34+). `adb shell input keyevent 120` takes a real system
  screenshot (the same path as the hardware keys); `adb screencap` does not trigger app callbacks.
- **A screenshot is not proof a toast was seen.** System overlays (the screenshot preview) sit
  above app toasts; capture over time (every 2 s) until the overlay is gone.
- **A field report is read off the device before it is theorised about.** The app's own log, the
  data file the feature writes and the preferences usually settle it in minutes. iOS prefs plists are
  binary: read them with python's `plistlib`, not `plutil -convert json` (it fails on `NSDate`/data
  values).
- **Pull evidence by exact path, never a broad match over user data.** A folder match on a
  container copies the user's own files (a backup zip is their whole history) onto the Mac. If it
  happens, delete the copies once read and say so; the originals stay on the device.
