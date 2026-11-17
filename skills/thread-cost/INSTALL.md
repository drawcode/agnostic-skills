# Thread cost: install

Per-turn cost estimates, $5 alerts, and capture-then-clear for agent threads. What it does and
the rule it enforces: [`SKILL.md`](./SKILL.md).

Needs `python3` 3.8+ (stdlib only). Works on macOS, Linux and Windows (`py` in place of `python3`).

## What is in it

| Path | What it is |
|---|---|
| `SKILL.md` | The rule, what fires, harness support, config keys |
| `scripts/thread_cost_alert.py` | Cost line, alerts, capture block; `status` / `check` / `replay` |
| `scripts/thread_idle_capture.py` | Idle wake before the prompt cache lapses (Claude Code) |
| `scripts/thread_handoff.py` | Records the handoff; resumes from it after `/clear` or `/compact` |
| `scripts/thread_hook_antigravity.py` | Antigravity hook adapter |
| `scripts/thread_usage.py` | Reads Claude Code, opencode, Codex and Antigravity sessions: time, tokens, cost |
| `scripts/model_prices.json` | Price table (USD per million tokens, with an `as_of` date) |
| `scripts/thread_hooks_common.py` | Shared config and state helpers |
| `scripts/thread_hooks_install.py` | Wires, checks and removes the hooks |

## Install

1. Put the `thread-cost` folder where it will stay. The hooks call its scripts by absolute path.
   - Claude Code: `~/.claude/skills/thread-cost` (all projects), or `<repo>/.claude/skills/thread-cost`.
   - Antigravity, opencode, Codex: their skills folder, or any fixed folder.
2. Check, then wire the hooks:

   ```sh
   python3 <skill>/scripts/thread_hooks_install.py           # report
   python3 <skill>/scripts/thread_hooks_install.py --write   # add the missing hooks
   ```

   This merges five hooks into Claude Code's `settings.json`, and one named `thread-cost` hook into
   Antigravity's `config/hooks.json` when `~/.gemini` exists. Other hooks are kept, and each file is
   backed up first. `--no-idle` leaves out the idle wake. `--harness` limits it to one harness.
3. Check it reads the current session:

   ```sh
   python3 <skill>/scripts/thread_cost_alert.py status
   ```

   In Claude Code, the next reply should end with a `_Last turn ~$...` line (the first turn shows
   `First turn ~$...` on screen at Stop instead).

Hooks are read at session start; a running session may need a restart to pick them up.

## Harnesses without hooks (opencode, Codex, Cursor, a bare terminal)

Paste this into the project's `AGENTS.md` (or the harness's always-loaded rules):

```markdown
## Thread cost
At an iteration boundary (a PR opens or merges, before a long wait, after about 40K of new
context), run `python3 <skill>/scripts/thread_cost_alert.py status`. When it prints CAPTURE NOW,
do what it says: write the handoff, make no outward writes, then tell the user to start a new
conversation from the handoff. Delegate screenshot- and log-heavy work to a subagent that returns
a verdict.
```

## Configure

Optional; with no config the defaults apply. Put `KEY=value` lines in
`~/.config/thread-cost/config.env` (`%APPDATA%\thread-cost\config.env` on Windows, or the file
`$THREAD_COST_CONFIG` names), or set the environment variables the script `--help` lists. The keys
are in `SKILL.md`, Config. Most setups change only:

- `thread_capture_step_file=~/path/to/capture-step.txt`: one paragraph telling the model how this
  setup records a handoff (which folder, what to update, whether to commit). Without it, the
  built-in step asks for a handoff doc where the project keeps its notes.
- `thread_ctx_growth_k=80` (or `0`) if the 40K growth capture fires too often.

## Update and uninstall

- **Update:** replace the folder in place; the hooks keep working. Run the installer again if the
  folder moved.
- **Uninstall:** `python3 <skill>/scripts/thread_hooks_install.py --uninstall --write`, then delete
  the folder. Hook state is under `thread_hooks_state_dir` (default
  `${XDG_STATE_HOME:-~/.local/state}/thread-hooks`, or `%LOCALAPPDATA%\thread-hooks` on Windows), and
  the config file (if you made one) under `~/.config/thread-cost/`; delete them too if you want.
