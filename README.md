# agnostic-skills

Agent skills that work in any harness (Claude Code, Antigravity, Codex, opencode, Cursor, or a
bare terminal) on macOS, Linux and Windows. Each skill is one self-contained folder: a `SKILL.md`
the agent reads, an `INSTALL.md`, and `scripts/` in stdlib `python3` (3.8+), with no
Python dependencies and no network access. A skill that drives outside tools (ffmpeg, adb, Xcode)
lists them in its `INSTALL.md`.

## Skills

| Skill | What it does |
|---|---|
| [`deterministic-scripts`](skills/deterministic-scripts/) | Move the repeatable steps of agent work (counts, inventories, parsing, build and device recipes, checks) out of model tokens and into scripts with one contract (`--help`, `--json`, exit codes), read personal and machine values from a home config instead of prompts, and block secrets, emails and home paths with a pre-commit scan. Also scaffolds and lints scripts and finds repeated commands in a transcript. |
| [`thread-cost`](skills/thread-cost/) | Per-turn and per-thread cost estimates from the harness's own transcript, cost alerts in steps, and a capture-then-clear handoff at a cost or context threshold, so long threads stay cheap. |
| [`video-trim`](skills/video-trim/) | Crop a screen recording to the app's own window (so the desktop, other windows or chat around it never reach a viewer), cut the dead time out (ffmpeg freeze detection, with a beat kept at each edge and a source-time subtitle track), keep explicit or log-driven windows, scrub notification banners out by OCR, build slowed before/after side-by-sides and contact sheets, and join recordings into one review video with chapters. |
| [`device-testing`](skills/device-testing/) | Drive an attached Android phone or iPhone from the terminal with one command set: install a build and prove which change it carries, launch, tap, swipe, type, screenshot, record, and wait on log evidence instead of sleeping. Plans run a whole flow in one call; a runner subagent profile and an optional hook keep agents from sending one tap per turn. |
| [`gameplay-verification`](skills/gameplay-verification/) | Verify a gameplay fix by driving a live round into the failing state and measuring the change, not by reasoning that it compiles. Reach a real round (and know what a scripted shortcut skips), protect the player's save, probe state rather than screenshots, sample rates and sub-second effects from inside the frame, and report what was and was not exercised. Any engine; your engine and game go in add-ons. |
| [`tiered-delegation`](skills/tiered-delegation/) | Route each unit of agent work to the cheapest model tier that can finish it (architect / workhorse / sprinter): a per-prompt inline-vs-delegate check, lean subagent profiles that load 5-8k tokens instead of ~37k, an escalation protocol, and scripts for the routing and cost math. |
| [`model-routing`](skills/model-routing/) | Pick the model for any spawn without a pinned profile from what its brief asks for (family alias, never a dated id), and set the harness default so a missed `model` lands on the mid tier, not the driver's. Install with `tiered-delegation`. |

More are on the way.

## Install a skill

1. Copy `skills/<name>/` to where your harness looks for skills and leave it there; hooks call its
   scripts by absolute path.
   - Claude Code: `~/.claude/skills/<name>` (all projects) or `<repo>/.claude/skills/<name>`.
   - Codex, opencode: `~/.agents/skills/<name>`, or the harness's own skills folder.
   - Other harnesses: any fixed folder, with a pointer to its `SKILL.md` in your always-loaded rules
     (`AGENTS.md`, `GEMINI.md`, and so on).
2. Follow that skill's `INSTALL.md`. Skills with hooks ship an installer that reports first and
   changes nothing until you pass `--write`, backs up each file it touches, and uninstalls cleanly.

## Conventions

[`AUTHORING.md`](AUTHORING.md) explains how these skills are built and how to write your own the same way: configs kept out, base skills with add-ons that override them, a portable contexts folder your harness files point at, and keeping secrets and PII away from the agent.


- **No personal or machine values.** Each skill reads its settings from environment variables,
  then its own `KEY=value` file (`~/.config/<skill>/config.env`, or `%APPDATA%\<skill>\config.env`
  on Windows), then the defaults. No config file is needed.
- **State stays outside your repos**, in the per-user state folder
  (`${XDG_STATE_HOME:-~/.local/state}`, or `%LOCALAPPDATA%` on Windows).
- **Hooks fail silent.** A hook that hits an error prints nothing and exits 0, so it never breaks a
  turn.
- **Base plus add-on.** To tune a skill for your setup without forking it, keep an
  `addons/<name>.addon.md` beside its `SKILL.md`. Record only what differs (for example your
  capture step or where your notes live). The agent reads the base first, then the add-on, and
  the add-on wins on conflict.

## License

[MIT](LICENSE)
