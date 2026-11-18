---
name: context-delegation-ab-benchmark
description: Measured A/B of inline vs tiered delegation vs small-model-only on a frontier driver (Opus 4.7 and Opus 5.5) -- cost, wall time, turns, fact recall and a blinded judge -- the evidence for the tiered-delegation rule and where it breaks.
metadata:
  type: report
  skill: tiered-delegation
  updated: 2026-09-26
---

# Delegation A/B benchmark

> **Summary:** On a 40k-token extraction task, tiered delegation matched inline Opus on fact
> accuracy at 28-75% lower cost and 10-30% less wall time. On a trivial lookup the rule correctly
> stayed inline; forcing delegation there cost +46% and 6x the latency. Haiku alone was cheapest
> and lost facts. Judgment text degraded when the driver handed it to haiku, not when it used sonnet.
> **Use when:** someone doubts that delegation saves anything, asks whether a cheaper tier costs
> quality, or is deciding which output fields a subagent may own.
> **Area:** agent practice

Only the corpus's shape is recorded; its content does not matter to the result. The rule it
backs is
[context-tiered-delegation-protocol.md](context-tiered-delegation-protocol.md).

## Setup

- Driver: `claude -p` (Claude Code CLI) with two frontier drivers, `claude-opus-4-7` and
  `claude-opus-5-5`. Older CLI builds reject 5.5.
- Three arms, same task prompt, only the appended system rule differs:
  - `inline` -- do everything yourself; the subagent tool is disallowed.
  - `rule` -- the tiered-delegation rule (small tier for mechanical, mid tier for bounded
    reasoning, inline floor for a trivial lookup, keep synthesis yourself).
  - `forced` -- hand the whole task to one haiku subagent.
- 3 reps per arm per task per driver = 36 runs. Read-only tools, no MCP servers.
- Ground truth computed by regex, not by a model.

### Tasks

- **small** -- "how many `.md` files are in this directory?" One `ls`.
- **large** -- 18 markdown notes, 156 KB (~40k tokens). For every file: every ticket id of a
  single `KEY-NNN` pattern (52 distinct in truth), every pull-request reference (41 distinct),
  and one sentence naming the single most important next step. JSON out.

### Quality measures

- Fact fields: micro precision and recall against the regex truth.
- The next-step sentence: a blinded Opus 5.5 judge reads each note and scores every candidate
  0 (wrong or unsupported), 1 (supported, not the top step), 2 (the top step). Candidates are
  shuffled and relabelled; arms are unblinded only after scoring.
- Wall time from the harness clock. **`duration_ms` in `claude -p` JSON undercounts runs where a
  subagent did the work** (19s reported vs 69.5s wall), so it is not used.

## Small task -- trivial lookup

All 18 runs answered correctly.

| Driver | Arm | Cost mean | Wall mean |
|---|---|---|---|
| Opus 5.5 | inline | $0.087 | 3.3s |
| Opus 5.5 | rule | $0.096 | 3.3s |
| Opus 5.5 | forced | $0.127 | 19.0s |
| Opus 4.7 | inline | $0.170 | 4.4s |
| Opus 4.7 | rule | $0.139 | 4.0s |
| Opus 4.7 | forced | $0.116 | 9.1s |

- The rule stayed inline -- same time as inline, no subagent spend. The inline floor works.
- Forcing delegation on 5.5: +46% cost, 6x wall time, same answer.
- On 4.7 the small-task cost is dominated by prompt-cache writes of the system prompt, which vary
  run to run ($0.033 to $0.192 within one arm). Time is the clean signal there: forced is 2x.

## Large task -- 40k-token extraction

| Driver | Arm | Cost mean | Wall mean | Turns | Ticket P / R | PR recall | Judge (0-2) |
|---|---|---|---|---|---|---|---|
| Opus 4.7 | inline | $1.87 | 189s | 28 | 1.00 / 1.00 | 0.99 | 1.86 |
| Opus 4.7 | rule | **$0.47** (-75%) | **132s** (-30%) | 5 | 1.00 / 1.00 | 1.00 | 1.35 |
| Opus 4.7 | forced | $0.33 | 109s | 2 | 0.94 / 0.99 | 0.86 | 0.98 |
| Opus 5.5 | inline | $1.07 | 88s | 25 | 1.00 / 1.00 | 1.00 | 1.98 |
| Opus 5.5 | rule | **$0.77** (-28%) | **80s** (-10%) | 9 | 0.99 / 1.00 | 1.00 | 1.91 |
| Opus 5.5 | forced | $0.43 | 164s | 2 | 0.99 / 0.97 | 0.88 | 1.48 |

Judge score spread (count of 0 / 1 / 2 grades):

| Driver | inline | rule | forced |
|---|---|---|---|
| Opus 4.7 | 2 / 1 / 33 | 7 / 21 / 26 | 9 / 37 / 8 |
| Opus 5.5 | 0 / 1 / 53 | 2 / 1 / 51 | 5 / 18 / 31 |

- Opus 4.7 `rule` chose **haiku** subagents; Opus 5.5 `rule` chose **sonnet**. That choice is the
  whole difference in judge score between the two drivers.
- `forced` (haiku alone) lost facts: one run missed 18 of 63 file-level PR refs, another missed 19,
  one invented 14 ticket ids, one added 8 bogus PR refs. Cheapest arm, least trustworthy.
- Inline is not flawless either: one Opus 4.7 inline run dropped the closing `}` of its JSON.
  Strict parsing scores it 0; with the brace restored its facts were fine, and it is counted so.
- Most runs in every arm report one extra PR ref against truth -- a regex edge in the truth set,
  identical across arms, so it does not move the comparison.
- Judge cost $0.95 per pass. Total spend about $19 across 36 runs and 2 judge passes.

## What this means for the rule

1. **The inline floor is right.** A single lookup stays inline; delegating it only adds latency
   and cost. The rule arm got this right on both drivers without being told which task was which.
2. **Delegating extraction is free quality-wise and cheaper.** Fact recall on the rule arm equalled
   inline on both drivers, at a quarter (4.7) to three quarters (5.5) of the cost.
3. **Judgment fields must not go to the small tier.** When 4.7 handed the next-step sentence to
   haiku, the judge score fell from 1.86 to 1.35. When 5.5 used sonnet it held at 1.91 vs 1.98.
   Small tier for pure extraction; mid tier or the driver for anything that ranks, summarizes or
   picks the "most important" thing.
4. **Never small-model-only on work with a quality bar.** The forced arm is where the savings
   story breaks: missed and invented ids with no orchestrator checking them.
5. **Savings shrink as the driver gets cheaper and faster.** Opus 5.5 inline is already 43%
   cheaper and 2x faster than 4.7 inline on this task, so the delegated margin is 28%, not 75%.

## Reproducing it

The benchmark harness is not included. Rebuilding it takes four pieces, all scriptable:

1. **Fixture** -- copy a set of notes into a scratch dir and compute truth with regex (ids per
   file, refs per file).
2. **Run** -- `claude -p` per arm and rep with `--append-system-prompt` carrying the arm's rule,
   `--output-format json`, read-only tools; record wall time from your own clock.
3. **Score** -- micro precision/recall per fact field against truth; parse output strictly.
4. **Judge** -- shuffle and relabel the judgment-field candidates per note, have a blinded
   frontier model grade 0/1/2, unblind afterwards.

Keep raw run JSON in scratch, not in the repo.
