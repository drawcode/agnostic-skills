# Install thread-cost

Install adds hooks that show per-turn cost estimates, raise $5 alerts, and ask the model to write a handoff before you clear a thread. What it does and the rule it enforces: [`SKILL.md`](./SKILL.md).

## Requirements

- `python3` 3.9+ (stdlib only). On Windows use `py` in place of `python3`.
- macOS, Linux or Windows.

## Install

The installer reports first and changes nothing until `--write`.

1. Put the `thread-cost` folder where it will stay. The hooks call its scripts by absolute path.
   - Claude Code: `~/.claude/skills/thread-cost` (all projects), or `<repo>/.claude/skills/thread-cost`.
   - Antigravity, opencode, Codex: their skills folder, or any fixed folder.
2. Report what is missing:

   ```sh
   python3 <skill>/scripts/thread_hooks_install.py
   ```

3. Add the missing hooks:

   ```sh
   python3 <skill>/scripts/thread_hooks_install.py --write
   ```

4. Restart a running session. Hooks are read at session start.

## Check it works

```sh
python3 <skill>/scripts/thread_cost_alert.py status
```

## Uninstall

```sh
python3 <skill>/scripts/thread_hooks_install.py --uninstall --write
```

Then delete the folder. Optional cleanup: hook state under `thread_hooks_state_dir` (default `${XDG_STATE_HOME:-~/.local/state}/thread-hooks`, or `%LOCALAPPDATA%\thread-hooks` on Windows), and the config file (if you made one) under `~/.config/thread-cost/`.

<details>
<summary><b>What the installer changes</b></summary>

- Merges five hooks into Claude Code's `settings.json`.
- Adds one hook named `thread-cost` to Antigravity's `config/hooks.json` when `~/.gemini` exists.
- Keeps your other hooks, and backs up each file first.
- `--no-idle` leaves out the idle wake.
- `--harness` limits it to one harness.
- In Claude Code, the next reply should end with a `_Last turn ~$...` line. The first turn shows `First turn ~$...` on screen at Stop instead.

</details>

<details>
<summary><b>Harnesses without hooks (opencode, Codex, Cursor, a bare terminal)</b></summary>

Paste this into the project's `AGENTS.md` (or the harness's always-loaded rules):

```markdown
## Thread cost
At an iteration boundary (a PR opens or merges, before a long wait, after about 40K of new
context), run `python3 <skill>/scripts/thread_cost_alert.py status`. When it prints CAPTURE NOW,
do what it says: write the handoff, make no outward writes, then tell the user to start a new
conversation from the handoff. Delegate screenshot- and log-heavy work to a subagent that returns
a verdict.
```

</details>

<details>
<summary><b>Configuration</b></summary>

Optional; with no config the defaults apply.

- Put `KEY=value` lines in `~/.config/thread-cost/config.env` (`%APPDATA%\thread-cost\config.env` on Windows, or the file `$THREAD_COST_CONFIG` names).
- Or set the environment variables the script `--help` lists.
- All keys are in `SKILL.md`, Config.

Most setups change only:

| Key | Example | Meaning |
|---|---|---|
| `thread_capture_step_file` | `~/path/to/capture-step.txt` | One paragraph telling the model how this setup records a handoff (which folder, what to update, whether to commit). Without it, the built-in step asks for a handoff doc where the project keeps its notes. |
| `thread_ctx_growth_k` | `80` (or `0`) | Use if the 40K growth capture fires too often. |

</details>

<details>
<summary><b>What is in the folder</b></summary>

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

</details>

<details>
<summary><b>Update</b></summary>

Replace the folder in place; the hooks keep working. Run the installer again if the folder moved.

</details>
