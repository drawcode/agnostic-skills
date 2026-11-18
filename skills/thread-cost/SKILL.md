---
name: thread-cost
description: Use to keep agent threads cheap -- when a hook reports the last turn's cost, a $5 cost alert, a "capture now" block or an idle wake; at an iteration boundary (a PR opens or merges, before a long wait); when a thread feels long or expensive; or when asked what a thread cost. Shows per-turn and per-thread cost estimates from the harness's own transcript, alerts in steps, and at a cost or context threshold has the model write a handoff so the user can clear the thread and continue from it. Covers the capture-then-clear rule, the status command for harnesses without hooks, every threshold knob, and installing the hooks on Claude Code and Antigravity.
---

# Thread cost: capture, then clear

Cost grows with context, not with work. Every request re-reads the whole conversation, so a
long thread pays for its past on every step. In one measured ledger (292 captures), cache reads
were 49% of spend, 1h cache writes 23% and output 13%. Captures whose peak context was 400K+
were 53% of spend, at $0.114 a request against $0.052 under 100K. A fresh thread starts at
40-60K and its one cache write costs well under $1, so clearing never "wastes the cache".

`<skill>` below is the folder holding this file; the scripts are in `<skill>/scripts/`. They are
stdlib `python3` (3.8+), read-only towards the harness, and never touch the network except the
optional Antigravity local server on 127.0.0.1.

## The rule

- **Bounce at iteration boundaries.** When a PR opens or merges, or before a long wait (CI, a
  device, a build over an hour), write the handoff (the capture step below), then clear and
  continue from it.
- **Delegate screenshot- and log-heavy work** (device passes, big logs, many screenshots) to a
  subagent that returns a verdict. Anything read in the main thread is paid again on every later
  request.
- **Clear vs compact.** `/clear` (or a new conversation) when the handoff holds everything.
  `/compact` only when a clear would lose uncaptured state: a background agent or CI watch still
  running, an uncommitted worktree, an answer still owed. The model cannot run either; it names
  the one to run and, for `/compact`, why.
- **When a hook says capture:** start no new feature work, do the capture step, make no
  outward-facing writes (no PRs, store, ticket or chat posts), and end with the one line the
  instruction asks for. Re-ask any question that is still open.
- **The cost line:** when the prompt hook supplies last turn's cost, end the reply with it as one
  italic line, as given.

The **capture step** defaults to: write a handoff where the project keeps its notes (what is done,
current state, decisions, open questions, next steps) so a fresh thread can continue from it
alone, and commit it if it lives in a repo. A setup with its own notes system replaces the step
with `thread_capture_step` or `thread_capture_step_file` (see Config).

## What fires

| Trigger | Script | Does |
|---|---|---|
| Every prompt | `thread_cost_alert.py` | Gives the model the previous turn's cost to end the reply with: `_Last turn ~$0.81 (10 requests, opus; 1.2M in / 4K out), context ~76K, thread ~$1.31 (est.)_` (model mix costliest first; tokens in include cache reads). A session's first turn shows on screen at Stop as `First turn ~$...`. Off with `thread_turn_cost_line=0`. |
| Every $5 | `thread_cost_alert.py` | Alert on screen at Stop and to the model at the next prompt. |
| Live context 200K | `thread_cost_alert.py` | One on-screen note at Stop. |
| $15 (then every $15), or live context 300K (then every 100K) | `thread_cost_alert.py` | Blocks the stop once with the capture instruction. Context levels re-arm after a compact. |
| Live context 40K past the session's own start (then every 40K) | `thread_cost_alert.py` | Same capture block, keeping threads small. The baseline is the first main-thread request since the last compact. Also tells the model to record the handoff with `thread_handoff.py set`. Skipped for subagents. Needs a readable transcript (Claude Code). |
| SessionStart after `clear` or `compact` | `thread_handoff.py` | If a handoff was recorded for this cwd in the last 6h and not yet used, tells the new session to read it first. One-shot. |
| Resume past the model's cache lifetime (60 min Claude, 10 min Gemini) on a context over 100K | `thread_cost_alert.py` | The prompt cache has lapsed; the model says so in one line and suggests the handoff and a clear at a boundary. |
| 50 min with no user input (cost >= $1, context >= 60K) | `thread_idle_capture.py` | Wakes the thread once (Claude Code `asyncRewake`) to run the same capture, inside the 1h cache window so the capture reads warm. |

Cost is an API list-price estimate (`scripts/model_prices.json`, Anthropic, OpenAI and Gemini
models, with dated `changes` for intro prices). A subscription bills differently; read it as
relative size. Update the table when prices change (`as_of`).

## Harnesses

| Harness | Cost line, alerts, capture block | Idle wake, resume after clear |
|---|---|---|
| Claude Code | automatic: hooks in `settings.json` | automatic (`asyncRewake`, SessionStart) |
| Antigravity | automatic once a conversation has token data: PreInvocation and Stop hooks through `thread_hook_antigravity.py` (the capture block re-enters the loop once; a new conversation replaces `/clear`, and every message and capture ending says so, with no SessionStart resume step). Token data needs Antigravity's local server running. | no: hooks are synchronous and there is no clear/compact event |
| opencode, Codex, Cursor, a bare terminal | manual: run `status` at iteration boundaries | no |

`status` works in every harness and auto-detects the current session (Claude Code, opencode,
Codex, Antigravity):

```sh
python3 <skill>/scripts/thread_cost_alert.py status      # one line, then OK or CAPTURE NOW: ...
```

Other read-only checks:

```sh
python3 <skill>/scripts/thread_cost_alert.py check --session <id>    # what would fire
python3 <skill>/scripts/thread_cost_alert.py replay --session <id>   # where growth capture fires
python3 <skill>/scripts/thread_idle_capture.py check --session <id>
python3 <skill>/scripts/thread_usage.py report [--session <id>] [--json]   # time, tokens, cost
```

Install and uninstall: `INSTALL.md` beside this file (`scripts/thread_hooks_install.py`).

## Config

Every knob is a flag, an environment variable and a key in the skill's own `KEY=value` file:
`$THREAD_COST_CONFIG`, else `%APPDATA%\thread-cost\config.env` on Windows, else
`${XDG_CONFIG_HOME:-~/.config}/thread-cost/config.env`. Environment wins, then the file, then the
default. No file is needed. The script docstrings (`--help`) list every knob; the common ones:

| Key | Default | Meaning |
|---|---|---|
| `thread_cost_alert_usd` / `thread_cost_alert_step_usd` | 5 / 5 | first alert, then every step |
| `thread_cost_capture_usd` / `thread_cost_capture_step_usd` | 15 / 15 | cost capture |
| `thread_ctx_capture_k` / `thread_ctx_step_k` | 300 / 100 | context capture (K tokens) |
| `thread_ctx_growth_k` | 40 | growth capture over the session start; 0 = off |
| `thread_idle_capture_min` | 50 | idle wake; keep it under the 1h cache TTL |
| `thread_cache_ttl_min` | per model | prompt-cache lifetime for the idle-gap note and the context warning; unset = the last model's `cache_ttl_min` in `model_prices.json` (60 for Claude; 10 for Gemini, whose implicit cache has no documented TTL and measured warm at 9 min, cold at 27) |
| `thread_turn_cost_line` | 1 | 0 = no per-turn cost line |
| `thread_capture_step` / `thread_capture_step_file` | built in | the capture step, as one line or a text file |
| `thread_hooks_state_dir` | per-user state dir | hook state (`%LOCALAPPDATA%\thread-hooks`, else `${XDG_STATE_HOME:-~/.local/state}/thread-hooks`) |
| `thread_usage_ledger_dir` / `thread_usage_docs_root` | state dir / git top level | `thread_usage.py snapshot` ledger and doc root |

State lives outside every repo. Any hook failure prints nothing and exits 0, so a hook can never
break a turn.

## Add-ons

A setup with its own capture workflow may keep `addons/<name>.addon.md` beside this file,
recording only its delta (usually the capture step and where its scripts sit). Read this file
first, then the add-on; the add-on wins on conflict.
