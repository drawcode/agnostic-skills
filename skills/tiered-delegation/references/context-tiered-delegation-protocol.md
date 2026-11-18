---
name: context-tiered-delegation-protocol
description: How work is split across model tiers (architect / workhorse / sprinter), the inline floor below which delegation loses, how skills spawn subagents, and how a stuck agent promotes its question up to the architect and then to the dev -- the rationale behind the tiered-delegation skill.
metadata:
  type: context
  skill: tiered-delegation
  updated: 2026-09-26
---

# Tiered delegation protocol

> Driver only, one level deep: a spawned subagent does not run this loop and never spawns
> subagents of its own. See `SKILL.md` -> "Driver only, one level deep".

> **Summary:** Route each unit of agent work to the cheapest model that can finish it, open every
> task with a mini plan that makes that possible, never delegate below an agent's own overhead,
> and have a blocked agent promote its question instead of guessing.
> **Use when:** planning or picking up any multi-step task, deciding whether a skill should spawn
> subagents, or when a task feels expensive.
> **Area:** agent practice

The procedure is the skill's [SKILL.md](../SKILL.md). This doc is the rationale; the skill is the
procedure. The commands behind it are in
[context-per-prompt-delegation-loop.md](context-per-prompt-delegation-loop.md), the evidence in
[context-delegation-ab-benchmark.md](context-delegation-ab-benchmark.md).

## The tiers

| Tier | Role | Claude model | Runs |
|---|---|---|---|
| **architect** | strongest reasoning model | `opus`, and `fable` | Ambiguous scope, root cause, plan authoring, final review of a risky diff, arbitrating a promoted question. At most one per phase. |
| **workhorse** | solid mid model | `sonnet` | The default. Implementation against a named target, bounded-diff review, PR text, multi-file edits where the files are known, anything that ranks or summarizes. |
| **sprinter** | small, fast model | `haiku` | Pure extraction with the context handed in: greps, inventories, log scraping, renames, schema fills, checkbox passes, ids/counts/lists. |

- Map by **role**, not by name, when the harness runs another model family. Before trusting the
  small tier with anything past pure extraction, run a quick A/B.
- `fable` sits at the architect tier or above, not in the cheap lane. Default to `opus`.
- The constraint is token budget, not capability. Cheap models on well-specified work buys
  breadth. If a subtask cannot drop a tier, the usual cause is an under-specified brief -- sharpen
  the handoff rather than upgrading the model.

## A frontier driver does no mechanical work

On an `opus`/`fable`-class driver, delegation applies to every task, not only planned ones. CLI
runs, greps, finds, parsing, file creation, edits with a known target, renames and formatting
all leave the session, **batched into one brief for one agent** -- never one agent per command.
The driver keeps scoping, root cause, reasoned wording, synthesis and final review. The only
inline exception is a lone scout call whose answer the driver needs before its next decision.
On a `sonnet` or `haiku` driver, the plain inline floor applies instead.

## The inline floor

Tiering only pays when the work is bigger than the cost of handing it off. Every subagent carries
a fixed overhead: its system prompt, tool schemas, a fresh context, the brief, its reasoning and
its result marshalled back -- about 6k tokens. Below that floor, delegation is **more** expensive
and slower than inline, for identical output.

- **One-liners run inline.** A single `find`/`grep`/`ls`/`wc`, a one-file read, a stat.
- **Delegate when the payload amortises the overhead:** a sweep over many files, a multi-file
  edit, a log scrape with parsing, an inventory that needs judgement about what to keep.
- **The tier for trivial work is "no agent", not "cheap agent".** A sprinter is still an agent.
- A trivial task wrapped in a terse "run this and report only the output" brief can trip a
  subagent's prompt-injection guard, fail outright and cost a second round trip.

Measured on a one-line directory count in a mid-size repo:

| Arm | Extra context spun up | Outcome |
|---|---|---|
| Inline shell call | none | correct, first try, effectively free |
| Delegated, terse "report only the number" brief | one fresh agent | **refused** as a suspected injection |
| Delegated, fully briefed benign framing | one fresh agent + a longer brief | correct, at many times the inline cost |

## Mini plans

Every task opens with phases, then subtasks, each tagged with its tier and the exact context it
needs -- files, symbols, line numbers, the command to run. Parallelism is marked explicitly;
anything unmarked is sequential. Phase gates are facts ("compiles clean", "15/15 green"), not
vibes. A subtask that must find its own target costs a tier more than one handed it. Template:
[mini-plan-template.md](mini-plan-template.md).

Keep the plan wherever the work's handoff notes live, beside the findings.

## Skills spawn subagents

Invoking a skill does not change the model -- the skill body runs inline in the calling session.
The lever is inside the skill: self-contained steps are delegated to tiered subagents with an
explicit model, sweeps and per-file passes fan out at sprinter tier, and only synthesis and
judgement stay with the orchestrator. New skills should be written that way from the start.

## Escalation

```
sprinter --+
           +--> workhorse --> architect --> dev
workhorse -+
```

A subagent that hits something its brief does not cover stops and asks. Guessing, silently
widening scope and inventing a fallback are all worse than a round trip.

- **Facts** go peer to peer, to the agent that already holds the context.
- **Decisions** go up a tier.
- **Intent, priority or ship risk** go from the architect to the dev, routed through the
  orchestrating session -- a subagent cannot reliably prompt a human itself. Questions are batched.
- A block is returned as a structured record -- tried / found / question / options / the option
  the agent would pick if forced -- never as prose. Format:
  [escalation-protocol.md](escalation-protocol.md).

The same question arriving from two agents means the plan is wrong, not the agents. A mis-tiered
subtask is re-issued at the right tier, not finished by the driver.

## Anti-patterns

- Spawning an agent for work smaller than its own overhead.
- Running a mechanical pass at architect tier because the feature around it matters.
- Handing a vague goal to a big model to compensate.
- Letting the small tier write anything that ranks, summarizes or picks "the most important" item.
- Letting a stuck agent improvise.
