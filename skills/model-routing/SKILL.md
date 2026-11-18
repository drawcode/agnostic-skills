---
name: model-routing
description: Driver session only. Use before every subagent spawn that is not a pinned lean profile -- a general-purpose or catch-all agent, a workflow agent() call, a fork-free Task call on any harness -- and when setting up a harness, to choose the model from what the brief asks for. Always the newest model in the family (by alias, never a dated id): workhorse by default, sprinter wherever the work is pure mechanics, architect only when explicitly picked. Also covers the harness-level default so an un-modelled spawn never inherits a frontier driver's model.
metadata:
  type: skill
  pairs_with: ../tiered-delegation/SKILL.md
---

# Model routing

`tiered-delegation` decides **whether** to delegate and **which lean profile** runs it. This
skill decides **which model** runs any spawn that has no pinned model, and makes the cheap
choice the default instead of something the driver has to remember.

## Three rules

1. **Newest model, by family alias.** Pass `haiku` / `sonnet` / `opus` (or the harness's own
   "latest" alias), never a dated model id. The harness resolves the alias to the newest model in
   that family, so routing stays current with no edits. A dated id is only for a pinned
   reproduction, and then the brief says why.
2. **Workhorse is the floor for anything un-modelled.** The harness default for subagents is the
   workhorse alias, not "inherit". On a frontier driver, inheriting is the most expensive lane
   there is. Architect is never a default: it is picked explicitly, per spawn.
3. **Sprinter wherever possible.** If the brief is pure mechanics (grep, list, count, extract,
   rename, replace, convert, run a named command) and asks for no judgment, it goes to the
   sprinter alias. One judgment verb (review, summarize, rank, explain, implement, fix)
   keeps it at workhorse: the small tier measurably loses quality on judgment fields.

## Tool agents come in pairs

An agent that drives one tool (an engine Editor, a device, a browser) is usually written as one
mid-tier operator. Split it in two:

- **runner** (sprinter): runs the exact commands the brief gives and returns their output.
  It carries the tool's few traps in its own profile so it reads no docs, and any failure
  that needs judgment comes back `BLOCKED`.
- **operator** (workhorse): diagnosis, writing new probes, deciding what to run next.

Most tool work is "run these, give me the output". Without a runner, all of it pays mid-tier
rates and loads the operator's reading list. Register both with `pick_agent.py` so
`--needs <tool>` routes by kind: the pair goes in `pick_agent.py`'s profiles file (`pick_agent.py --help`).

## Pick it with the script, not by eye

```
python3 <skill dir>/scripts/pick_model.py --task "<the brief>"        # or --task-file, or --task -
python3 <skill dir>/scripts/pick_model.py --does extract --needs web   # kind already known
```

Prints `MODEL: <alias> tier=<tier> does=<kind> agent=<profile>`. Deterministic: it classifies
by verbs after dropping negated clauses ("do not refactor" is not a refactor). The higher
tier's signal wins, and no signal at all means workhorse. `agent` is the leanest profile from
`pick_agent.py`. Spawn that profile when it exists, since its own pinned model applies. If the
work needs a generic agent, pass `model=<alias>` on the spawn. `--harness any` prints the
role (`small` / `mid` / `strongest`) for a non-Claude harness to map.

Write the brief first, then pick: the classifier reads the brief you will actually send.
If it says workhorse and you think sprinter, the brief has a judgment step in it. Split that
step out rather than overriding the pick.

## Harness wiring

Each harness needs two settings: subagents default to the workhorse alias, and the lean
profiles are visible. Harness files point here; they never copy this.

| Harness | Default subagent model | Profiles |
|---|---|---|
| Claude Code | user `settings.json` -> `"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"}` | `~/.claude/agents/<tier>.md` symlinks to the tiered-delegation skill's `agents/` (or per repo `.claude/agents/`) |
| opencode | `agent.general.model` in `opencode.json` set to the provider's mid "latest" model | one agent per tier, each with a pinned `model` |
| Codex / Cursor / IDE agents | the tool's subagent or background-agent model setting, set to its mid model | the tiers as named presets, where supported |
| Own loop / raw API | the spawn tool's `model` argument defaults to the mid alias | pass the tier per call |

Claude Code precedence, verified against v2.1.314: a `model` on the call wins, then the
agent's frontmatter `model`, then `CLAUDE_CODE_SUBAGENT_MODEL`, then the parent's model.
So the default changes only un-modelled spawns: pinned profiles and explicit `opus` calls keep
their model. Do **not** set:

- `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`: it also overrides explicit picks, so an architect call
  would silently run on the default.
- `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL`: it overrides every workflow `agent()` model.

Env changes and new agent files load at session start.

Evidence, the env vars, and how to re-verify after an upgrade:
`references/context-claude-code-subagent-model.md`.

Do not try to enforce this with a hook that rewrites the spawn's `model`. In some session
modes Claude Code refuses any call whose input a user hook changed (the call is denied, not
modified). A hook that denies un-modelled spawns makes a frontier driver write the whole
brief again, which costs more than it saves. The env default plus the pinned profiles are
the enforcement.

## Do not

- Pass a dated model id for routine work, or `inherit` on a frontier driver.
- Default anything to architect. It is picked per spawn, at most once per phase.
- Send a ranking, summary or "most important" field to the sprinter tier because the rest of
  the brief is mechanical. Split it.
- Override the script's pick without changing the brief. Change the brief instead.
