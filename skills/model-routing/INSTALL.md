# Model routing: install

Pick the model for any subagent spawn that has no pinned profile, from what its brief asks for,
and set the harness default so a missed `model` never inherits a frontier driver's model. The
rules: [`SKILL.md`](./SKILL.md). Pairs with `tiered-delegation`, which defines the lean profiles.

## What is in it

| Path | What it is |
|---|---|
| `SKILL.md` | When to pick, the tiers, harness wiring per harness, what not to set |
| `references/context-claude-code-subagent-model.md` | How Claude Code resolves a subagent's model, and how to re-verify after an upgrade |
| `scripts/pick_model.py` | Classifies a brief as sprinter / workhorse / architect and prints the model alias (or role with `--harness any`) |
| `scripts/pick_agent.py` | The leanest profile for a kind of work; extra tools register in a profiles file (`--help`) |
| `scripts/test_pick_model.py` | The classifier's regression briefs |

Stdlib `python3` (3.9+), read-only, no network; `--help` and `--json` on each script.

## Install

1. Put the `model-routing` folder where your harness looks for skills (Claude Code:
   `~/.claude/skills/model-routing`; Codex, opencode: `~/.agents/skills/model-routing`; others: any
   fixed folder with a pointer in `AGENTS.md` or `GEMINI.md`). Install `tiered-delegation` beside it.
2. Set the harness default from the table in `SKILL.md` (Claude Code: `"env":
   {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"}` in `~/.claude/settings.json`), then start a new session.
3. Check it:

   ```sh
   python3 <skill>/scripts/pick_model.py --task "grep every caller of Foo and count them"
   python3 <skill>/scripts/test_pick_model.py
   ```

4. Optional: register a tool that has its own runner/operator profiles in
   `~/.config/tiered-delegation/profiles.json` (schema in `pick_agent.py --help`), so
   `--needs <tool>` routes to them.

To remove it, delete the folder and the env setting.
