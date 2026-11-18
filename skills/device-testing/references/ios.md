# iOS backend

`device` on a physical iPhone. Same commands as Android; this page is what differs underneath.

## Setup, once per Mac and phone

- **Xcode** supplies `xcrun devicectl` (install, launch, processes, lock state, app files). A
  trimmed Xcode is enough.
- **pymobiledevice3** in its own venv: `python3 -m venv ~/.venvs/pymobiledevice3 &&
  ~/.venvs/pymobiledevice3/bin/pip install pymobiledevice3`. Override with `PYMOBILEDEVICE3`.
- **ffmpeg** for `record` (`brew install ffmpeg`). Without Homebrew: the static macOS builds that
  ffmpeg.org links to, from evermeet.cx (`/ffmpeg/info/ffmpeg/release` and `/ffmpeg/info/ffprobe/release` name the
  current zips; the old `getrelease/.../zip` links answer 410), unzipped into `~/.local/bin`. Intel builds only.
- **Camera access for the app hosting the terminal** (VS Code, Terminal, iTerm) for full-rate `record`:
  System Settings > Privacy & Security > Camera. The phone's screen is a capture device, and those
  count as cameras. Without it `record` falls back to ~5 fps screenshots and says so. A request from
  a terminal child can hang with no prompt and then read as denied; switch it on in Settings.
- **The phone:** paired over USB, Developer Mode on, "Trust This Computer" accepted.
- **Over the network (no USB):** when no iPhone shows on usbmux, `devices` falls back to paired
  phones `devicectl` can reach on the network. `install`, `launch`, `stop` and `focus` work (focus
  only says the app is running, not front or back); `pymobiledevice3` captures (`shot`, `log`,
  `record`) need USB. Verified 2026-11-06: a 253 MB IPA installed in about 27 s.
- **Config:** `DEVICE_<ALIAS>_SERIAL="<UDID>"` names it like any Android device, and
  `APP_IOS_BUNDLE_ID` sets the app.

## Traps

- **Unlock the phone before the first developer command of a session.** Screenshots, appearance,
  app state and the tunnel need the developer disk image, which only mounts while the phone is
  unlocked; locked, `devicectl` reports "developer disk image could not be mounted". It mounts by
  itself once unlocked. **After a reboot or a replug it may stay unmounted** until something asks for it: WDA
  then dies with `InvalidService` / "DTX capability handshake timed out". `xcrun devicectl device info
  ddiServices` mounts it; `native start` now runs that first. Auto-Lock cannot be set from the host: set it to Never on the phone.
- **iOS 27: devicectl lists no apps even unlocked.** The backend falls back to `pymobiledevice3 apps
  query` (installation_proxy), shaped like a devicectl entry, so `installed`, `version`, `pids` and `focus` work.
- **A landscape app: set `DEVICE_IOS_ORIENTATION`** (`landscape-right` = notch on the left, or
  `landscape-left`). The display reports its native portrait size and `universal-hid-service` takes
  native portrait coordinates, but a landscape app's screenshots come back landscape. With it set,
  `size()` and profiles are in the landscape frame and taps are turned onto the panel. `landscape-right`
  was measured on an iOS 27 iPhone 2026-10-31 (a landscape point (0.5, 0.877) hit the button only as
  native (0.123, 0.5)); `landscape-left` is the mirror and not yet measured.
- **A locked phone lists no apps.** `devicectl device info apps` returns an empty list while the
  passcode is required, so `check` reports `installed no` for an app that is there. `pymobiledevice3
  apps list` still sees it. Unlock, then trust `installed`.
- **No `sudo` needed.** On iOS 17+ pymobiledevice3 falls back to a userspace tunnel by itself.
- **pymobiledevice3 commands disagree on `--udid`** (`core-device` has none), so the backend selects
  the phone with `PYMOBILEDEVICE3_UDID` for every call.
- **The syslog is huge.** An app process logs tens of thousands of lines a minute, mostly DEBUG from
  CoreMotion, CoreLocation and networking. The collector drops DEBUG (`DEVICE_IOS_SYSLOG_ARGS`);
  the app's own log arrives at NOTICE from its own image, e.g. `<App>{<EngineFramework>}`.
- **Wi-Fi networks with client isolation or a VPN filter cannot carry any of this**, the same as
  wireless adb. Stay on USB.

## How each command works here

| Command | Mechanism |
|---|---|
| `focus` | the DVT app-state notification stream's opening snapshot: `Running` in front, `Suspended` behind |
| `bg` | `pymobiledevice3 developer core-device hid button home`, which works on iOS 26 |
| `launch` | `devicectl device process launch --activate`; with the input server it adds `--terminate-existing` and the env var; without, a running app resumes; `--url` adds `--payload-url`. System apps (Safari, Settings) work too, the app lookup includes them |
| `stop` | `devicectl device process terminate`, pids matched to the app's bundle path |
| `count`/`log`/`wait` | a background `syslog live --process-name <executable>` per phone into the scratch dir, inside a supervisor shell that restarts it when the relay drops (it drops when a USB screen capture starts and sometimes mid-take; a few lines in the gap are lost); `preflight` restarts it, so logs start clean |
| `nav` | display size from `core-device get-display-info`, density = UI scale x 160, so `dp` is a point |
| `record` | `DEVICE_IOS_RECORD`, default `auto`: **capture** first, QuickTime's path. Setting CoreMediaIO's `AllowScreenCaptureDevices` makes the USB phone an AVFoundation device, matched by its `DeviceName`; `ios_screen_recorder.swift` (compiled on first use into the scratch `bin/`) writes H.264 at full resolution, ~56-60 fps, video only, and `<name>.start` when the first frame lands; `stop` remuxes to `<name>.mp4`, the same files as Android, so `video-trim` and uploads work unchanged. ~28 Mbps raw, so trim before upload. Then **stream** on the `hid` route (iOS 27), then **shots**: one DVT screenshot channel (`ios_recorder.py`), ~5 fps |
| `prefs` | `devicectl device copy from --domain-type appDataContainer` of `APP_IOS_PREFS_FILE`, needs a debuggable (development-signed) build |
| `shot` | `developer core-device screen-capture screenshot`, falling back to `dvt screenshot`; same PNG size, median ~0.98 s against ~1.10 s (5 runs each, process start-up is most of both) |
| `appearance` | `core-device user-interface-style dark|light` |
| `location set\|route\|clear` | `developer dvt simulate-location set\|play\|clear`. The simulation lives only while the connection is open, so a background holder process keeps it (pid in the backend state) and `clear` kills it, then clears. `route` writes a timed GPX (one point per second at `--speed`) and plays it, returning when the last point is reached. CoreDevice's `location` group only lists scenarios, so DVT is the one route. Proved in-app: a jump made the game show its speed warning, and the map moved to the target |
| `network <profile>\|list\|clear` | `developer dvt condition set\|list\|clear` through `ios_condition_holder.py`, a background process that keeps the DVT session open. **The condition reaches only processes launched inside that session**: measured with 100% loss held for minutes, an already-running app, an app started by devicectl and a cold-started app all kept getting HTTP 200 at ~90 ms, and `condition list` from a second connection reads `isActive` false. `network <profile> --relaunch [--input-server]` makes the holder launch the app (process-control launch, kills a running copy): then the app's CFNetwork log shows `summary for task failure ... response_status=-1` with 4 s and 15 s timeouts, and the game sits on a black screen at startup. `clear` stops the holder (it clears the condition on SIGTERM), then, because the holder-launched copy is left on a black screen, stops the app and relaunches it normally (with the input-server env when `--input-server` was used), so the map returns and requests succeed. Proven 2026-10-02: 100% loss gave 11 `response_status=-1` lines; `clear` relaunched and the map returned with 166 `response_status=200`. The condition can take several clears to drop, so `clear` retries up to eight times. **Setting the condition after the launch, in the same session, does not reach the app either**: an app launched by the holder with no condition, then 100% loss set on that session's `ConditionInducer` for 25 s, kept answering `response_status=200` (582 -> 603, zero `-1`) (2026-10-09). So a cut timed to one request is not possible on iOS; use an Android airplane-mode cut. Profiles are Apple's link conditioner set (100% loss, very poor, edge, 3G, LTE, Wi-Fi), plus thermal and GPU conditions in `list` |
| `clipboard set\|get` | `developer core-device copy` and `paste`: the general pasteboard, exact UTF-8 (a round trip of `Hello こんにちは é` came back identical) |
| `perf sample <s>` | `developer dvt graphics` (`CoreAnimationFramesPerSecond`, system-wide, 1 Hz) and `dvt sysmon process monitor process` (`cpuUsage`, `physFootprint`) on the app's executable. No per-frame times, so p95 frame time is n/a |
| `crashes [--since] [--pull]` | `crash ls` filtered to `<executable>-*` (`.ips`); `crash pull --match` for `--pull` |
| `check` (wda line) | `provision list`: the profile whose application-identifier ends in the runner bundle; prints `wda  expires <date> (<n> days)`, with a warning under 2 days |
| `shot` | `developer core-device screen-capture screenshot`, falling back to `dvt screenshot`; same PNG size, median ~0.98 s against ~1.10 s (5 runs each; process start-up is most of both) |
| `rotate` | `core-device rotate left|right`: the device itself turns, so it works with the orientation lock on and reaches a portrait-locked app's system UI; no getter, each turn reports the new orientation |

## Quiet: notifications are manual on iOS

`quiet` cannot be automated here. Focus and Do Not Disturb have no host-side API: `pymobiledevice3`
has no subcommand for them (its `notification` group posts Darwin notifications, which does not
change a Focus), and a configuration profile cannot turn one on. So `quiet on` prints the steps and
`quiet on --confirmed` stores a timestamp for the UDID in the state file:

1. Control Center > Focus > Do Not Disturb on.
2. Settings > Notifications > Show Previews > Never, so a banner that slips through (a Focus can
   allow some people and apps) shows no content.

`quiet status` reports the confirmation and its age; older than 12 h counts as stale, because a
Focus can end on a schedule or when the phone is used. `preflight` and `record start` warn
(`!! notifications not silenced`) until it is confirmed, and fail under `DEVICE_QUIET_REQUIRED=1`.
`shot` warns once per process, from the state file only, so it stays fast. `quiet off` clears the
confirmation and prints the undo steps; it does not touch the phone.

On Android all of it is automatic (see the `quiet` row in SKILL.md): `cmd notification set_dnd
priority`, `heads_up_notifications_enabled 0` and SystemUI demo mode with notifications hidden.
Priority mode still lets starred contacts and alarms through. Demo mode ends on reboot; `quiet
status` then reads off and the next `preflight` re-applies it.

## Touches: three routes, picked by `DEVICE_IOS_INPUT`

`auto` (the default) uses `hid` on iOS 27 or later and `server` below it. `check` prints the
resolved route as `input`.

- **`hid`**: CoreDevice's universal HID service (`core-device universal-hid-service tap|drag|type`),
  host-side touch and typing with no signing. Pixels from `size()` are scaled to its 0..65535 range;
  a swipe is a `drag` with `--duration` and `--steps` from `ms`. On iOS 26 the device refuses it:
  "Remote control requires iOS 27.0 or later on this device (code 9021)", and the backend turns that
  into an error naming the fix (`DEVICE_IOS_INPUT=server`). Typing is printable ASCII only. The
  named hardware buttons (`hid button`) work on iOS 26 and back `bg`; DEL and ENTER still use the
  input server.
- **`server`**: the app's input server ([input-server.md](input-server.md)). It reaches everything
  the app draws, not native overlays drawn above it (system alerts, the soft keyboard's keys, a
  native debug console).
- **WebDriverAgent is a last resort, not a route.** Everything the input server and CoreDevice can
  reach goes through them. WDA (XCUITest, signed with a personal Apple ID team, 7-day profiles; the
  app under test keeps its own signing) is kept for UI iOS draws itself that nothing else reaches on
  iOS 26: the soft keyboard's real keys and system alerts. It is exposed as opt-in commands only.
- The Accessibility Inspector service lists native elements, but its press does nothing outside a
  debuggable process, and a game engine is one opaque view to it.
- **Record on `hid`**: `display start-video-stream` writes raw RTP/HEVC; `record stop` depacketizes
  it with pymobiledevice3's `misc/rtp_dump.py` and encodes an mp4 with ffmpeg. Screen capture is tried
  first on every route.
- **Seen 2026-11-17 (macOS host, iOS 17+ phone):** the DVT `screenshot` worked, but `hid` taps and
  `display start-video-stream` both failed with "Failed to start service" after pymobiledevice3 fell
  back to the no-root userspace tunnel. No `.rtp` was written and screen capture was unavailable, so
  `record` produced nothing. Suspected fix, untested: a root tunnel (`sudo pymobiledevice3 remote tunneld`).
  If taps fail, treat it as a tunnel problem, not a coordinate problem, and change route (SKILL.md § Rules, iOS input
  route order): the app's input server first, XCUITest/WDA for native UI, the root tunnel last.

## Native UI through WebDriverAgent (opt-in)

For UI iOS draws itself: the soft keyboard's real keys and system alerts. Everything else stays on
the input server or CoreDevice; `DEVICE_IOS_INPUT` has no `wda` value and `tap`/`swipe` never use it.

- **Setup once:** check out WebDriverAgent, set `DEVICE_IOS_WDA_SRC`, `DEVICE_IOS_WDA_TEAM` and
  `DEVICE_IOS_WDA_BUNDLE_PREFIX`, then `native build` (signed with the team's Automatic profile, phone
  connected and unlocked) and `native install`. Then trust the developer on the phone: Settings >
  General > VPN & Device Management.
- **7-day expiry:** a free-team profile lapses after 7 days; the runner then fails to launch. Rerun
  `native build` and `native install`.
- **Trap:** Xcode reuses a cached profile that has not lapsed yet, so a rebuild a day before expiry
  keeps the old date (`check` still warns). Move the runner's file out of
  `~/Library/Developer/Xcode/UserData/Provisioning Profiles/` first (the one whose
  `application-identifier` ends in the runner bundle), then build: a fresh 7-day profile is issued.
- **Run:** `native start` launches the runner with `pymobiledevice3 developer dvt xcuitest` (no
  xcodebuild), forwards its port and opens a session; any other `native` or `alert` command starts it
  when it is down. `native stop` kills runner and forward. `native status` prints `/status` and the
  front app's bundle id, which proves an action.
- **Commands:** `native tap|hold|swipe` take screen pixels like `tap` (`--norm`, `--wait RE`);
  `native press <label> [--hold ms]` clicks an element by accessibility id, else by label, which is
  how keyboard keys (`a`, `delete`, `return`) are pressed; `native source --grep RE` greps the
  accessibility tree (a text field's `value:` shows what was typed); `alert accept|dismiss|text`
  answers a system alert, and `alert text` reports `"alert": false` when none is up.
- **Trap:** a live WDA session overrides `location set` (DVT simulate-location): the app snaps back to the real
  location after the first WDA action, while the holder process still runs (seen 2026-10-18, iOS 27.0.1). Use an
  in-app location tool for apps that have one, or stop WDA (`native stop`) before relying on a simulated location.
- **Trap:** WDA's `/wda/tap` returns success and does nothing on iOS 26. The commands use W3C touch
  actions (`/actions`) in points (pixels / UI scale) instead.
- **Trap:** a runner that is alive but never answers `/status` (seen on iOS 26.6.2: `ready` false, `runner` and
  `forward` true) is wedged. When a `native` or `alert` command finds one still not ready after 12 s, it stops
  runner and forward and starts once more (which remounts the DDI); if that is not ready either, it fails as usual.
- **Trap:** on iOS 26.6.2 the phone can show a system sheet "Enter iPhone Passcode for "XCTest"" (Enable UI
  Automation). It blocks every native command; the runner log prints `initializationForUITestingDidFailWithError`
  about 60 s after `testRunnerReadyWithCapabilities` and the runner keeps relaunching. A restart cannot help, only
  a person typing the passcode on the phone. `start()` now fails fast with that message when the line shows up
  in new log bytes, and does not restart.
- **Typing speed:** `native keys <label>... [--gap ms]` taps keys in one action from cached key rects (one finger per
  key: on a single source the move after a lift lands as a second press, so `abc` typed `Abbcc`). It skips the
  per-key lookup, but over a game-engine view XCTest still delivers about one touch per 5 s, and `waitForIdleTimeout` 0
  does not change that. Fast typing (a burst under a second) is not reachable through WDA in a game: do it by hand.
- **Limits seen:** the long-press accent popup never opens for a synthesized touch. A 2.5 s
  `native hold` on a key, recorded at full rate, shows only the key's normal preview the whole
  time, and the release types the plain letter; `native swipe ... --hold MS` (hold, then slide
  onto where the popup would be) types nothing. WDA also cannot read `source` mid-gesture: it
  answers only after the whole action ends. Long-press accents stay a manual check.

### System alerts: `launch --accept-alerts` and `alert watch`

`launch ... --accept-alerts [S]` (default 20 s) and `alert watch S [accept|dismiss]` poll WDA for a
system alert, print its text and answer it, until S seconds pass; both start WDA when it is down.
Proved on a real alert, with no change to any app's permissions: Safari on a page that asks for
location shows `"<site>" Would Like to Use Your Location`; `alert text` returned the text, `alert
dismiss` cleared it (`alert text` then `"alert": false`), and a second load was answered by
`alert watch 20 accept`. The text carries Unicode isolation marks around the site name.

### Pasting into an app's text field

Both routes work on a game-engine text field. `clipboard set 'Hello こんにちは é'`, then `native hold` for 1.5 s on the
field (the keyboard's real `UITextField`, which sits above the keys when an input field is focused,
not the greyed field in the page): iOS shows a `Paste | AutoFill` menu, and `native press Paste`
fills the field with the exact text. No "Allow Paste" alert appeared (same-device pasteboard written
through CoreDevice). The input server's `text` (replaces the value) and `type` (appends through the
native keyboard) also take the Unicode string and show it correctly, so paste is only needed when a
test is about the paste path itself or a string too long to type.

## Clocks: log lines against video time

`record` writes `<name>.start` from the Mac clock; syslog lines carry the phone's. Measured over
two ~15k-line samples (line timestamp against Mac arrival time) the phone runs about 30-50 ms ahead
of the Mac including delivery delay, well under the ~100 ms that would matter, so `.start` stays on
the Mac clock and needs no correction. Both are local time with no date. The ~5 fps `shots` recorder
now writes milliseconds like the others. Re-measure if a phone and Mac ever drift (no NTP).

## Photos library and capture prep (`media`, measured iPadOS 27.2, 2026-11-05)

A device has no host route into Photos (`simctl addmedia` is simulator-only; AFC files under DCIM
are never indexed; AirDrop from the host reported "sent" twice and never arrived). `media` uses a
small helper app (`scripts/ios_media_helper`, signed with the WDA team as
`<DEVICE_IOS_WDA_BUNDLE_PREFIX>.MediaHelper`): the host copies files and a request into its
Documents with `devicectl device copy to`, launches it, and reads `result.json` back.

- `media push <files> --album NAME` adds them to Photos in that album.
- `media hide-others --album NAME` hides every asset PhotoKit can see outside the album, after
  writing their ids to `hidden-by-helper.json`. Never deletes. ⚠⚠ **`media unhide` cannot undo it
  when the Hidden album is locked** (Face ID/passcode for Hidden, the default): a hidden asset is
  then invisible to every app, by id, by `includeHiddenAssets` and through the Hidden smart album
  (measured iPadOS 27.2, 2026-11-05: 4 of 4 not found). The owner unhides in Photos > Hidden. The
  ledger keeps every id it could not restore. So prefer `media picker` first: on the measured iPad
  the picker never showed the owner's items PhotoKit could not see, and nothing needed hiding.
- `media picker` shows the system photo picker (PHPicker), so a screenshot proves what an app's
  picker will show before a store or review capture. **Check this first**: on the measured iPad, 10
  of the owner's items PhotoKit could not see (Full Access read "3 Photos") were also absent from the
  picker, so nothing more needed hiding. Turning Shared Albums off did not remove them.
- Prompts: the first run asks for Photos access. **Never `alert accept` it** (it can pick Limited or
  Don't Allow, and a denial then needs Settings > Apps > Media Helper > Photos). Press
  `kTCCServicePhotos-allow-button` (stable across locales). Each hide/unhide raises "Allow X to hide
  N photos?"; the backend presses Hide/Unhide.
- Prep for a capture, all through WDA: Auto-Lock `Never` (Settings > Display & Brightness,
  `AUTOLOCK`), Do Not Disturb (Control Center > Focus > `mode-Do Not Disturb`; ⚠ Control Center can
  open on the Home page, keep away from real accessory tiles), sample media pushed, picker checked.
  Restore after: Auto-Lock, Focus, `media unhide`.

- **On-device recording** (`DEVICE_IOS_RECORD=device`): the device's own Screen Recording, which is
  what App Review asks for ("captured on a physical device"). Start: Control Center > `Screen
  Recording` (3 s countdown). Stop: the red status-bar pill (found by colour in a screenshot) >
  "Stop screen recording?" > `Stop`; Control Center reopens on whichever page was last shown, so its
  button is not a reliable stop. Then `media pull` exports the newest video since the start. The
  recording lands in Photos: run `media hide-others` after, or it shows in the next take's picker.
  A host USB capture (`capture`, QuickTime's path) shows no indicator on the device but needs the
  device to appear as a CoreMediaIO device, which took ~30 s on this iMac and then gave no frames.
