# Install device-testing

Drive an attached Android phone or iPhone from the terminal with one command set: install a
build and prove which change it carries, launch, tap, swipe, type, screenshot, record, and wait
on log evidence. Install is a folder copy plus an optional config file. What it does and when to use each command: [`SKILL.md`](./SKILL.md).

## Requirements

- `python3` 3.9+ (stdlib only). Windows: `py` in place of `python3`.
- Android: `adb` on PATH.
- iOS: Xcode (`xcrun devicectl`) and `pymobiledevice3`.
- A phone, set up as in the toggle below.

## Install

1. Put the `device-testing` folder where your harness looks for skills (details in the toggle below).

   ```sh
   cp -R device-testing ~/.claude/skills/device-testing
   ```

2. Copy the config and set at least `APP_ANDROID_PACKAGE` and `APP_IOS_BUNDLE_ID`:

   ```sh
   mkdir -p ~/.config/device-testing
   cp config.example.env ~/.config/device-testing/config.env
   ```

3. Check it:

   ```sh
   python3 <skill>/scripts/device.py devices
   python3 <skill>/scripts/device.py check
   ```

4. Optional, Claude Code: the runner subagent and the tap-nudge hook (see the toggle below).

## Check it works

```sh
python3 <skill>/scripts/device.py check
```

## Uninstall

Delete the folder and the config folder:

```sh
rm -rf ~/.claude/skills/device-testing
# optional: also drop your settings
rm -rf ~/.config/device-testing
```

<details>
<summary><b>Per-harness locations</b></summary>

- Claude Code: `~/.claude/skills/device-testing` (all projects), or `<repo>/.claude/skills/device-testing`.
- Codex, opencode: `~/.agents/skills/device-testing`, or the harness's own skills folder.
- Other harnesses: any fixed folder, with a pointer to its `SKILL.md` in your always-loaded rules (`AGENTS.md`, `GEMINI.md`, and so on).

</details>

<details>
<summary><b>Dependencies and install commands</b></summary>

| Dependency | For | Install |
|---|---|---|
| `python3` 3.9+ | every script (stdlib only) | Windows: `py` in place of `python3` |
| `adb` on PATH | Android | Android SDK platform-tools (`brew install android-platform-tools`, `apt install adb`, or the SDK manager) |
| Xcode (`xcrun devicectl`) | iOS: install, launch, processes, app files | macOS only; a trimmed Xcode is enough |
| `pymobiledevice3` | iOS: screenshots, logs, USB forward, conditions | `python3 -m venv ~/.venvs/pymobiledevice3 && ~/.venvs/pymobiledevice3/bin/pip install pymobiledevice3` (or set `PYMOBILEDEVICE3` to its path) |
| `ffmpeg` on PATH | iOS `record` | `brew install ffmpeg`, or a static build in `~/.local/bin` |
| Pillow (optional) | small screenshot copies, contact sheets | `python3 -m pip install Pillow`; without it you get full-size stills and a note |
| `idb` (optional) | `scripts/ios-sim-harness.py` only | `pip install fb-idb` and `brew tap facebook/fb && brew install idb-companion` |

</details>

<details>
<summary><b>The phone</b></summary>

- Android: USB debugging on and the host authorized.
- iPhone: paired over USB, Developer Mode on, "Trust This Computer" accepted.
- Full-rate iOS recording needs Camera access for the app hosting the terminal (System Settings > Privacy & Security > Camera). Without it `record` falls back to about 5 fps screenshots and says so.
- Details: [`references/ios.md`](references/ios.md).

</details>

<details>
<summary><b>iOS input</b></summary>

- On iOS 27+ taps go through the host (`hid`).
- On older iOS, or when host taps fail, the app needs an in-app input server: a small loopback HTTP endpoint the app starts only when launched with an environment variable, kept out of release builds.
- The protocol is in [`references/input-server.md`](references/input-server.md).
- `device launch --input-server` then `device forward` connects to it.

</details>

<details>
<summary><b>Configuration</b></summary>

Copy `config.example.env` to `~/.config/device-testing/config.env`, or point `DEVICE_TESTING_CONFIG` at another file. Every key can also come from the environment. Never commit a filled-in copy. Device aliases (`DEVICE_<ALIAS>_SERIAL`) are optional.

| Key | Default | Meaning |
|---|---|---|
| `APP_ANDROID_PACKAGE` | empty | Android app id (set it) |
| `APP_IOS_BUNDLE_ID` | empty | iOS bundle id (set it) |
| `DEVICE_<ALIAS>_SERIAL` | none | Optional alias; on iOS the UDID |
| `DEVICE_TESTING_ADDONS` | empty | `:`-separated add-on modules |
| `SCRATCH_ROOT` | system temp dir | Screenshots, recordings, logs |
| `PYMOBILEDEVICE3` | `~/.venvs/pymobiledevice3/bin/pymobiledevice3`, else PATH | Path to the `pymobiledevice3` CLI |

Every other key is commented in `config.example.env`.

</details>

<details>
<summary><b>Claude Code extras: runner subagent and tap-nudge hook</b></summary>

- The runner subagent: copy or symlink `agents/device-runner.md` into `~/.claude/agents/`.
- The tap-nudge hook, in `~/.claude/settings.json`:

  ```json
  "hooks": { "PostToolUse": [ { "matcher": "Bash", "hooks": [
    { "type": "command", "command": "python3 <skill>/scripts/device_tap_nudge.py" } ] } ] }
  ```

- Tune it with `DEVICE_TAP_NUDGE_EVERY` and `DEVICE_TAP_NUDGE_CLI` (`--help`).

</details>

<details>
<summary><b>Your app's specifics</b></summary>

The base knows no app. Put app ids, a screen-map profile and reusable plans in an add-on next to it (for example `myapp-device-testing-addon/`) and list it in `DEVICE_TESTING_ADDONS`. How: [`references/extending.md`](references/extending.md).

</details>

<details>
<summary><b>What is in it</b></summary>

| Path | What it is |
|---|---|
| `SKILL.md` | The loop, the rules, which reference to read when |
| `config.example.env` | Every config key, commented |
| `scripts/device.py` | The `device` CLI; `device help` lists every command |
| `scripts/device_testing/` | Per-platform backends (Android, iOS, iOS hid/WDA/media), plans, nav profiles |
| `scripts/device_tap_nudge.py` | Optional PostToolUse hook: nudges toward a plan after 3 single inputs in a row |
| `scripts/ios-sim-harness.py` | Build a tiny UIKit harness around plugin `.mm` files and drive it on a simulator |
| `scripts/ios_media_helper/` | Xcode project for the iOS Photos helper behind `device media` |
| `agents/device-runner.md` | A subagent profile that runs a whole device pass and returns a verdict and evidence paths |
| `references/` | Command matrix, every command's arguments, capture, plans, iOS, simulator, extending |

</details>

<details>
<summary><b>What it writes</b></summary>

Scratch output (screenshots, recordings, logs) goes under `SCRATCH_ROOT` (default: the system temp dir). Nothing is written anywhere else. To remove the skill, delete the folder and `~/.config/device-testing/`.

</details>
