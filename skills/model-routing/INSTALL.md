# Install model-routing

Install adds one skill folder and one harness default, so a subagent spawn with no `model` never inherits a frontier driver's model. It is installed together with [`tiered-delegation`](../tiered-delegation/INSTALL.md), which defines the lean profiles. The rules: [`SKILL.md`](./SKILL.md).

## Requirements

- Stdlib `python3` (3.9+). Read-only, no network.
- `tiered-delegation` installed beside it.

## Install

1. Put the `model-routing` folder where your harness looks for skills, and install `tiered-delegation` beside it.
2. Set the harness default from the table in `SKILL.md` (Claude Code shown), then start a new session:

   ```json
   "env": {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"}
   ```

   Put it in `~/.claude/settings.json`.
3. Optional: register a tool that has its own runner/operator profiles in `~/.config/tiered-delegation/profiles.json` (schema in `pick_agent.py --help`), so `--needs <tool>` routes to them.

## Check it works

```sh
python3 <skill>/scripts/pick_model.py --task "grep every caller of Foo and count them"
python3 <skill>/scripts/test_pick_model.py
```

## Uninstall

Delete the folder and the env setting.

<details>
<summary><b>Per-harness locations</b></summary>

- Claude Code: `~/.claude/skills/model-routing`.
- Codex, opencode: `~/.agents/skills/model-routing`.
- Others: any fixed folder with a pointer in `AGENTS.md` or `GEMINI.md`.

</details>

<details>
<summary><b>What is in it</b></summary>

| Path | What it is |
|---|---|
| `SKILL.md` | When to pick, the tiers, harness wiring per harness, what not to set |
| `references/context-claude-code-subagent-model.md` | How Claude Code resolves a subagent's model, and how to re-verify after an upgrade |
| `scripts/pick_model.py` | Classifies a brief as sprinter / workhorse / architect and prints the model alias (or role with `--harness any`) |
| `scripts/pick_agent.py` | The leanest profile for a kind of work; extra tools register in a profiles file (`--help`) |
| `scripts/test_pick_model.py` | The classifier's regression briefs |

Each script takes `--help` and `--json`.

</details>

<details>
<summary><b>Related skills</b></summary>

Pairs with `tiered-delegation`, which defines the lean profiles.

</details>
