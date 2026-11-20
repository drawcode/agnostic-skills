---
name: tiered-delegation
description: Driver session only (a spawned subagent never uses it and never spawns subagents). Use when planning or picking up any multi-step task -- breaking work into phases and subtasks, choosing which model tier runs each one, spawning subagents from a skill, or deciding whether to escalate. Routes each unit of work to the cheapest model that can finish it (architect / workhorse / sprinter), defines how a stuck sprinter or workhorse promotes a question upward, and how the architect asks the dev when intent is genuinely unclear. Also use when a task feels expensive, when a skill's steps could run in parallel, or when asked to make work more token-efficient.
allowed-tools:
  - Agent
  - SendMessage
  - ListAgents
  - AskUserQuestion
  - Read
  - Write
  - Edit
  - Bash
---

# Tiered delegation

The constraint is token budget, not capability. Cheap models on well-specified work buys
breadth -- more subtasks, more verification, more features shipped together. Spend the
savings on coverage, not on a bigger model.

Pick the model per unit of work, never per session.

## Driver only, one level deep

These rules are for the **driving session** only.

- **A spawned subagent does not run this rule.** No per-prompt loop, no estimator, no mini plan,
  no frontier-driver offload. It does its brief inline, itself, and returns the result (or a
  `BLOCKED` block).
- **No subagent spawns another subagent.** Delegation is exactly one level: driver -> subagent.
  Work too big for the brief is promoted as `BLOCKED` ("needs splitting"); the driver re-plans
  and fans it out.
- **Every brief says so.** Each delegated brief carries the line:
  `You are a subagent: do this work yourself. Do not spawn subagents or apply delegation rules.`
- A subagent may still *call scripts*; scripts are not agents.

## The tiers

| Tier | Role | Claude model | Runs |
|---|---|---|---|
| **architect** | strongest reasoning model | `opus` (and `fable`) | Ambiguous scope, root-cause work, plan authoring, final review of a risky diff, arbitrating a promoted question. Rare -- at most one per phase. |
| **workhorse** | solid mid model | `sonnet` | The default. Implementation against a known target, bounded-diff review, PR text, multi-file edits where the files are already named, anything that ranks or summarizes. |
| **sprinter** | small, fast model | `haiku` | Pure extraction and mechanics with the context handed in: greps, file inventories, log scraping, renames, schema fills, checkbox passes, formatting, ids/counts/lists. |

Map by role, not by name, on a non-Claude harness. `fable` sits at the architect tier or above
it -- it is **not** a fast/cheap lane. Default to `opus` and reach for `fable` only when the task
genuinely needs it.

Default to the cheapest tier that can plausibly finish. If a subtask cannot drop a tier,
that usually means its context is under-specified -- sharpen the handoff instead of upgrading
the model.

## Lean profiles: the brief is the whole context

A subagent never sees the driver's conversation, but a generic one still loads every tool
schema, every MCP server, the skills list and the full CLAUDE.md before it reads its brief:
**~37.5k tokens measured** for a `general-purpose` agent running one `wc -l`. The profiles in
`agents/` carry only what their work needs, pin their model, skip CLAUDE.md (workhorse carries
a short house-rules digest instead) and have no spawn tool, so "one level deep" is enforced,
not just asked for:

| Profile | Model | Tools | Fixed context (measured 2026-09-30) |
|---|---|---|---|
| `runner` | haiku | Bash | 4.8k -- runs the command lines the brief gives |
| `scout` | haiku | Bash, Read, Grep, Glob | 5.5k -- read-only extraction |
| `sprinter` | haiku | + Edit, Write | 6.3k -- mechanical edits, target named |
| `workhorse` | sonnet | + Edit, Write | 8.2k -- implementation with judgment |
| `architect` | opus | read-only, keeps CLAUDE.md | 16.8k -- plans, root cause, verdicts |

Pick with `python3 <skill dir>/scripts/pick_agent.py --does <kind> [--needs web,mcp,...]`.
Only a need no lean profile carries (web, MCP, skills, or a tool your profiles file registers) falls back to a heavier
agent -- and then still with the tier's `model`, passed explicitly: a generic agent without one
inherits the driver's model, which on a frontier driver is the most expensive lane there is.
Pick that model from the brief with `pick_model.py`, and set the harness default so a missed
`model` lands on workhorse, not the driver: the `model-routing` skill (`../model-routing/SKILL.md`).
Split a workhorse task before spawning it: any part that is mechanical goes to a sprinter. The brief carries every file, command and
project rule the work needs, since the profile brings none. Re-measure with
`tally_cost.py --source claude-code --agent-overhead`. Where the spend actually goes, and what
the profiles save on real history: `references/context-subagent-cost-audit.md`.

## Per-prompt loop

Run before doing the work:

1. **Estimate** inline vs delegate with the script, not by eye:
   `python3 <skill dir>/scripts/estimate_delegation.py --paths <files/dirs> [--ext .ts] [--json]`
   prints `VERDICT: delegate|inline` against a ~6k-token per-agent overhead floor.
2. **Delegate** what decomposes into independent, well-specified chunks, each to the cheapest
   capable tier. Fan independent chunks out in one message.
3. **Tally** estimated vs actual cost after a delegated run when the harness records usage and
   pulling it is cheap (`<skill dir>/scripts/tally_cost.py`).
4. **Script** any step with a re-derivable answer -- counts, inventories, greps, parsing, cost
   math. Scripts, not model tokens.
5. Never trade correctness for a cheaper tier. Promote a question instead of guessing.
6. Keep briefs harness-agnostic: name files, symbols and the expected output shape.

Script contracts: `references/context-per-prompt-delegation-loop.md`.

## A frontier driver does no mechanical work

When the orchestrating model is frontier (`opus`, `fable` or above), this skill applies to
every task, not only plans. Anything a workhorse or sprinter can finish leaves the session:
CLI runs, greps, finds, parsing, file and folder creation, file edits with a known target,
renames, formatting. The frontier model keeps scoping, root cause, reasoned wording, synthesis
and final review.

- Batch the mechanical steps into one brief for one agent. Do not spawn an agent per command.
- The only frontier-inline exception is a lone scout call (one `ls`, one `grep`) whose answer
  the orchestrator needs before its next decision.
- On a `sonnet` or `haiku` driver the plain inline floor applies instead.
- Haiku gets pure extraction (ids, counts, lists, greps). Anything that ranks, summarizes or
  picks "the most important" thing goes to sonnet or stays with the driver. Measured: haiku-written
  judgment fields scored 1.35/2 vs 1.86 inline; sonnet held at 1.91 vs 1.98
  (`references/context-delegation-ab-benchmark.md`).

## The inline floor

The cheapest tier for a one-liner is no agent. A single `find`/`grep`/`ls`/`wc`, a one-file read
or a stat runs inline. Every subagent pays a system prompt, tool schemas, a fresh context, the
brief, its reasoning and the marshalled result -- ~5-8k on a lean profile, ~37k on a generic one. A terse brief on trivial delegated work can also
be refused as a suspected prompt injection, costing a second round trip.

## Every task opens with a mini plan

Before doing the work, write phases, then subtasks under each phase, each subtask tagged with
its tier and the exact context it needs. See `references/mini-plan-template.md`.

Rules that make the plan cheap:

- Name files, symbols and line numbers in the subtask. A subtask that has to *find* its target
  is a tier more expensive than one that is handed it.
- Scout inline first when discovery is cheap (one `grep`, one `ls`), then fan out over what you
  found. Do not pay an agent to learn what you already know.
- Independent subtasks go out in one message so they run concurrently.
- Sequence by dependency only. Do not barrier phases that have no real dependency.

## Skills spawn subagents too

A skill runs inline in the calling session, so invoking a skill does not change the model.
The lever is inside: a skill's steps should be delegated to tiered subagents wherever the
step is self-contained.

- The skill body stays in the orchestrating session -- it holds the plan and the decisions.
- Each self-contained step becomes an `Agent` call with an explicit `model` and a complete brief.
- Sweeps, inventories and per-file passes fan out at sprinter tier.
- Only the synthesis and the judgement calls stay at the orchestrator's tier.

## Escalation: promote the question, never guess

A subagent that hits something its brief does not cover **stops and asks**. Guessing, silently
widening scope, and inventing a fallback are all worse than a round trip.

```
sprinter --+
           +--> workhorse --> architect --> dev (the user)
workhorse -+
```

- Peers talk directly. Use `SendMessage` with the agent's name to reach a running agent that
  holds the context you need; `ListAgents` shows who is live.
- If the answer needs a decision rather than a fact, promote up a tier rather than sideways.
- The **architect** is the last stop that can decide. When intent, priority, or ship risk is
  genuinely unclear, it asks the dev -- routed through the orchestrating session, which is the
  only guaranteed channel to a human.
- A blocked subagent returns a structured block, not prose. Format in
  `references/escalation-protocol.md`.

## Do not

- Spawn an agent for work smaller than the agent's own overhead. One `grep` is cheaper inline,
  but on a frontier driver batch the rest of the mechanical work out rather than running it inline.
- Run a mechanical pass at architect tier because the surrounding feature is important.
- Hand a subtask a vague goal and a big model to compensate.
- Let haiku write anything that ranks, summarizes or picks "the most important" item.
- Let a subagent spawn a subagent, or run this rule inside a subagent.
- Let a stuck agent improvise. A promoted question costs less than a wrong diff.
