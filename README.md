# agnostic-skills

Agent skills that work in any harness (Claude Code, Antigravity, Codex, opencode, Cursor, or a
bare terminal) on macOS, Linux and Windows. Each skill is one self-contained folder: a `SKILL.md`
the agent reads, an `INSTALL.md`, and `scripts/` in stdlib `python3` (3.8+), with no
dependencies and no network access.

## Skills

| Skill | What it does |
|---|---|
| [`thread-cost`](skills/thread-cost/) | Per-turn and per-thread cost estimates from the harness's own transcript, cost alerts in steps, and a capture-then-clear handoff at a cost or context threshold, so long threads stay cheap. |

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
