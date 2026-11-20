---
name: device-testing
description: Use when driving or testing an app on a physical Android phone or iPhone from the terminal -- one `device` command set routed to the attached device's platform. Install a build and prove which change it carries, launch, stop, background and resume, screenshot and record, tap, swipe and type, wait on log evidence, run named steps from a screen-map profile, read the app's preferences, and switch the system dark/light style. Also use when a test pass needs to run the same way on Android and iOS, when an iPhone seems impossible to automate without WebDriverAgent, or when adding a platform or a project add-on. App, project and device specifics live in config, profiles and add-ons, never here.
allowed-tools:
  - Bash
  - Read
---

# Device testing

Drive an attached Android phone or iPhone from the terminal with one command set (`scripts/device.py`,
routed per platform): screenshot, read, tap, prove the screen changed. Knows no app: config, profile
(`DEVICE_NAV_PROFILE`) and add-on (`DEVICE_TESTING_ADDONS`) carry specifics; see `references/extending.md`.

## Efficiency

- Drive any flow of 2+ inputs with one `device run <plan>` call, never one input per agent turn; replace `sleep` with `wait <regex>` / `nav <id>` on log evidence.
- Logs are state, screenshots are evidence: check with `log`/`wait`, `shot --max-height 1000`, Read the small copy.
- A project may ship reusable plans and a runner agent; look in its add-on skill before hand-driving.
- Delegate a whole pass to the `device-runner` subagent (`agents/device-runner.md`): it returns a verdict and evidence paths, not screenshots. `scripts/device_tap_nudge.py` is an optional PostToolUse hook that nudges after 3 single inputs in a row (setup: its docstring).

## Loop

```bash
D=<skills>/device-testing/scripts/device.py
$D devices ; $D --device <alias> check
$D preflight                             # unlocked, awake, logs ready, app stopped, logs cleared
$D install <apk|ipa> --symbol MY_CONST   # refuses a build that predates the change
$D launch ; $D nav <step>
$D tap 540 1200 --wait 'Opened.*Menu'    # proven by a log line, not exit 0
$D shot ; $D log '<tag>' 40
$D run <plan> [--keep-going]             # one command per line, `finally:` always runs
```

`--device` = alias, adb serial or UDID, before the command. Exit: 0 ok, 1 failed, 2 negative wait, 3 unsupported.
Coordinates are pixels or 0-1 with `--norm`. iOS taps need an in-app input server: `launch --input-server`, then `forward`.

## Rules

- Prove every action (`--wait` on a log line or a screenshot).
- **iOS input route order:** when host `hid` taps or recording fail, switch to routes the test owns, never
  to a root/sudo tunnel first: 1) the app's **in-app input server** (`DEVICE_IOS_INPUT=server`, [input-server.md](references/input-server.md));
  if the app has none, embed one (dev builds only, off unless the launch env var is set, loopback only), 2) **XCUITest / WebDriverAgent**
  for iOS-level native UI ([ios.md](references/ios.md) § Native UI), 3) a root tunnel only as the last fallback, asked of the user with the reason.
- A profile belongs to one screen (`#!size`, `#!density`).
- `preflight` before; after any pass, screenshots or recording: `stop` the app (a running game heats the phone); `bg` only mid-test for a resume check, then `stop`.
- Quiet the phone before any recording or screenshot (`quiet`); iOS: do the steps, `quiet on --confirmed`.
- Keep the device awake for the whole pass (`preflight` does; `awake off` after).
- Record coordinates normalized (0-1), pixels only beside the fraction with the screen size.
- Anything typed twice goes in a Python 3 `.py` script, never bash/zsh.

## Read when

| Situation | File |
|---|---|
| Layers, full loop, reference index | [references/overview-and-loop.md](references/overview-and-loop.md) |
| Per-platform command table, output, coordinates | [references/command-matrix.md](references/command-matrix.md) |
| Plans, full rules, iOS touch model | [references/rules-and-plans.md](references/rules-and-plans.md) |
| Every command's arguments | [references/commands.md](references/commands.md) |
| Stills, video | [references/capture.md](references/capture.md) |
| Runner profile, tap-nudge hook | [agents/device-runner.md](agents/device-runner.md), `scripts/device_tap_nudge.py --help` |
| New platform or add-on | [references/extending.md](references/extending.md) |
| iOS in-app input protocol / setup / simulator | [references/input-server.md](references/input-server.md), [references/ios.md](references/ios.md), [references/ios-simulator.md](references/ios-simulator.md) |
