<p align="center"><img src="assets/hero.svg" alt="agnostic-skills: agent skills that run in any harness, on any OS" width="100%"></p>

<p align="center">
  <a href="https://drawcode.github.io/agnostic-skills/"><img src="https://img.shields.io/badge/docs-drawcode.github.io-b18cff?style=flat-square" alt="Docs site"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-fbc743?style=flat-square" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/python-3.9%2B%20stdlib-43e5fb?style=flat-square" alt="python 3.9+, stdlib only">
  <img src="https://img.shields.io/badge/macOS%20%C2%B7%20Linux%20%C2%B7%20Windows-62fb43?style=flat-square" alt="macOS, Linux, Windows">
</p>

**Drop-in skills for coding agents.** Each one is a folder your agent reads, plus small python
scripts that do the repeatable parts, so the model spends its tokens on judgment.

- Works in Claude Code, Codex, opencode, Cursor, Antigravity, or a bare terminal.
- Plain `python3`, standard library only. No pip installs, no network calls.
- Nothing personal baked in: settings come from env vars or a small config file.

Browse the skills on the docs site: **[drawcode.github.io/agnostic-skills](https://drawcode.github.io/agnostic-skills/)**

## Skills

### Keep agent work cheap

<a href="skills/thread-cost/"><img src="skills/thread-cost/assets/banner.svg" alt="thread-cost" width="100%"></a>
See what each turn costs, get alerts in steps, and hand off before a thread gets expensive.

<a href="skills/tiered-delegation/"><img src="skills/tiered-delegation/assets/banner.svg" alt="tiered-delegation" width="100%"></a>
Route each piece of work to the cheapest model tier that can finish it, with lean subagent profiles.

<a href="skills/model-routing/"><img src="skills/model-routing/assets/banner.svg" alt="model-routing" width="100%"></a>
Pick the model for a spawn from its brief, and make a missed `model` land on the mid tier. Install with `tiered-delegation`.

<a href="skills/deterministic-scripts/"><img src="skills/deterministic-scripts/assets/banner.svg" alt="deterministic-scripts" width="100%"></a>
Turn repeatable steps into scripts with one contract, and block secrets and home paths before they commit.

### Test and show your app

<a href="skills/device-testing/"><img src="skills/device-testing/assets/banner.svg" alt="device-testing" width="100%"></a>
Install, tap, swipe, screenshot and wait on logs on an attached Android phone or iPhone, with one command set.

<a href="skills/gameplay-verification/"><img src="skills/gameplay-verification/assets/banner.svg" alt="gameplay-verification" width="100%"></a>
Prove a gameplay fix in a live round, measure it, and keep the player's save safe. Any engine.

<a href="skills/video-trim/"><img src="skills/video-trim/assets/banner.svg" alt="video-trim" width="100%"></a>
Crop a recording to the app, cut the frozen stretches, scrub notification banners, and join clips with chapters.

More are on the way.

## Install a skill

1. **Copy the folder.** Put `skills/<name>/` where your harness looks for skills, and leave it there
   (hooks call its scripts by absolute path).
2. **Follow its `INSTALL.md`.** Installers report first and change nothing until you pass `--write`.

<details>
<summary><b>Where each harness looks for skills</b></summary>

| Harness | Folder |
|---|---|
| Claude Code | `~/.claude/skills/<name>` (all projects) or `<repo>/.claude/skills/<name>` |
| Codex, opencode | `~/.agents/skills/<name>`, or the harness's own skills folder |
| Anything else | Any fixed folder, plus a pointer to its `SKILL.md` in your always-loaded rules (`AGENTS.md`, `GEMINI.md`, and so on) |

</details>

<details>
<summary><b>What an installer will and won't do</b></summary>

- Reports what it would change, and changes nothing until you pass `--write`.
- Backs up each file it touches.
- Uninstalls cleanly.
- A skill that drives outside tools (ffmpeg, adb, Xcode) lists them in its `INSTALL.md`.

</details>

## How they're built

Every skill follows the same few rules. [`AUTHORING.md`](AUTHORING.md) shows how to write your own
the same way.

<details>
<summary><b>No personal or machine values</b></summary>

Each skill reads its settings from environment variables, then its own `KEY=value` file
(`~/.config/<skill>/config.env`, or `%APPDATA%\<skill>\config.env` on Windows), then the defaults.
No config file is needed.

</details>

<details>
<summary><b>State stays outside your repos</b></summary>

State goes in the per-user state folder (`${XDG_STATE_HOME:-~/.local/state}`, or `%LOCALAPPDATA%`
on Windows), never in your project.

</details>

<details>
<summary><b>Hooks fail silent</b></summary>

A hook that hits an error prints nothing and exits 0, so it never breaks a turn.

</details>

<details>
<summary><b>Base plus add-on</b></summary>

To tune a skill for your setup without forking it, keep an `addons/<name>.addon.md` beside its
`SKILL.md`. Record only what differs (for example your capture step or where your notes live). The
agent reads the base first, then the add-on, and the add-on wins on conflict.

</details>

## Made by

These skills run every day across our studios' app, game and web work.

<p>
  <a href="https://drawcode.com"><img src="assets/brand/drawcode.svg" alt="drawcode: design and develop" height="64"></a>&nbsp;
  <a href="https://drawk.com"><img src="assets/brand/drawk.svg" alt="drawk: product research and develop" height="64"></a>&nbsp;
  <a href="https://drawlabs.com"><img src="assets/brand/drawlabs.svg" alt="drawlabs: game studio" height="64"></a>
</p>

[drawcode.com](https://drawcode.com) · [drawk.com](https://drawk.com) · [drawlabs.com](https://drawlabs.com) · [Docs site](https://drawcode.github.io/agnostic-skills/)

## License

[MIT](LICENSE)
