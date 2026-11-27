# Tiered delegation: install

Routes each unit of agent work to the cheapest model tier that can finish it (architect /
workhorse / sprinter), with a per-prompt inline-vs-delegate check, an escalation protocol, and
three scripts that do the routing and cost math so the model does not.

## What is in it

| Path | What it is | Needed? |
|---|---|---|
| `SKILL.md` | The rules as a skill | Harnesses with skills |
| `prompt-tiered-delegation.md` | The same rules as one paste-ready block | Yes, unless the harness loads skills |
| `references/` | Mini plan template, escalation format, and the rationale, script contracts and A/B benchmark behind the rules | Read on demand |
| `scripts/estimate_delegation.py` | Prints `VERDICT: delegate\|inline` from payload size vs agent overhead | Recommended |
| `scripts/pick_agent.py` | Maps what a unit of work does and needs to the leanest profile; extra tools register in a profiles file (`--help`) | Recommended |
| `scripts/tally_cost.py` | Sums real token and cost per session, compares a delegated run to an inline one, measures each agent type's fixed context (`--agent-overhead`) | Optional |
| `agents/*.md` | Lean subagent profiles (runner, scout, sprinter, workhorse, architect): pinned model, minimal tools, no CLAUDE.md, no spawn tool | Recommended on Claude Code |

Install it with its pair, `model-routing`, which picks the model for any spawn that has no
pinned profile and sets the harness default.

The scripts need only stdlib `python3` (3.9+), are read-only, touch no network, and take `--help` and
`--json`. Exit `0` is ok, `2` is bad input.

## Quick install -- Claude Code, one project

```sh
mkdir -p <repo>/.claude/skills
cp -R tiered-delegation <repo>/.claude/skills/
chmod +x <repo>/.claude/skills/tiered-delegation/scripts/*.py
python3 <repo>/.claude/skills/tiered-delegation/scripts/estimate_delegation.py --tokens 20000
```

Start a session in the repo and ask what skills it has; `tiered-delegation` should be listed.
The skill triggers on multi-step work. To make the rules standing for **every** prompt, also
append `prompt-tiered-delegation.md` to `<repo>/CLAUDE.md` or `AGENTS.md`, and set its
`Estimator:` line to `.claude/skills/tiered-delegation/scripts/estimate_delegation.py`.

Link the lean agent profiles too, one symlink per file (Claude Code reads agents only at session
start, so start a new session afterwards):

```sh
mkdir -p <repo>/.claude/agents
for a in runner scout sprinter workhorse architect; do
  ln -sfn ../skills/tiered-delegation/agents/$a.md <repo>/.claude/agents/$a.md
done
```

For every project on the machine, use `~/.claude/skills/` and `~/.claude/agents/` instead, and set
the subagent default model as the `model-routing` skill describes, so an un-modelled spawn runs on
the mid tier rather than the driver's model.

If the project keeps skills canonically somewhere else (a versioned docs folder, say), put the
folder there and symlink `.claude/skills/tiered-delegation` to it rather than copying twice.

## Per harness

| Harness | Rules go in | Skill goes in | How a subagent gets its tier |
|---|---|---|---|
| Claude Code | `<repo>/CLAUDE.md` or `AGENTS.md` | `<repo>/.claude/skills/<name>` (symlink) | `Agent` tool, `model: opus \| sonnet \| haiku` per spawn |
| opencode | `<repo>/AGENTS.md` | its skills dir; it can also read `.claude/skills/` | one subagent per tier in `opencode.json`, each with a pinned `model` |
| Codex CLI | `<repo>/AGENTS.md` | its skills dir if supported, else paste `SKILL.md` + references under the rules | its subagent or model-selection feature; with none, the loop, scripts and escalation still apply |
| Cursor / IDE agents | an always-apply project rule, or `AGENTS.md` | paste under the rules | the tool's model picker or background-agent model |
| Raw API / own loop | the orchestrator's system prompt | -- | give it a spawn tool with a `model` argument, and give subagents the `BLOCKED` format in their own prompt |

Prefer project scope over a global instructions file. A global file is shared with every other
project on the machine.
A subagent with no pinned model runs at the driver's rate and throws away most of the saving.

## Non-Claude models

Map by role, not by name: architect = the strongest reasoning model, workhorse = a solid mid
model, sprinter = a small, fast model. Before trusting the small tier with anything past pure
extraction, run a quick A/B -- the measured rule is that a haiku-class model loses real quality
when it writes ranking or summary fields.

## Rule history

- **2026-10-31** -- harness default and model routing. A machine had the profiles on disk
  but linked nowhere, so every spawn was a ~37.5k-token generic agent on the driver's Opus.
  Now `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` is the floor for any un-modelled spawn, and
  `model-routing` / `pick_model.py` picks sprinter for pure-mechanics briefs.

- **2026-09-30** -- lean subagent profiles. A generic subagent loaded ~37.5k tokens before its
  brief (every tool, MCP server, skill list and CLAUDE.md); the profiles load 4.8k-8.2k (16.8k
  for the architect, which keeps CLAUDE.md), measured on the same one-line task. Subagents were
  47% of delegated spend across 126 spawns, so this is the cheapest lever the kit has.

- **2026-09-25** -- the per-prompt loop is standing: estimate with the script, delegate what
  fits, tally when cheap, script every deterministic step. Everything stays harness-agnostic.
- **2026-09-24** -- on a frontier driver, all mechanical work leaves the session, batched into
  one brief. The only inline exception is a lone scout call.
- **2026-09-24** -- the small tier does pure extraction only. Judgment scored 1.35 vs 1.86 inline;
  the mid tier held at 1.91 vs 1.98.
- **2026-09-24** -- `fable` is architect tier or above, not a cheap lane.
- **Earlier** -- the inline floor: below ~6k tokens of agent overhead, delegation costs more than
  inline, and terse briefs on trivial work get refused as suspected prompt injection.

## Uninstall

Delete the skill folder (or its link) and the pasted block. Nothing else is written anywhere.
