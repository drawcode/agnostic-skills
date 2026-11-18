---
name: context-subagent-cost-audit
description: Measured Claude Code spend over 6.5 weeks of real sessions -- where the tokens go (cache reads dominate), whether delegation paid, what a subagent loads before its brief (37.5k generic vs 4.8-8.2k lean), and what the lean profiles would have saved. The evidence behind the lean agent profiles.
metadata:
  type: report
  skill: tiered-delegation
  updated: 2026-09-30
---

# Subagent cost audit

> **Summary:** 86 driver sessions and 133 subagents, 2026-08-16 to 2026-09-30 (36 active days),
> came to ~$4,400 at API list prices (~$97/calendar day, ~$2,900 per 30 days). 66% of it was
> cache reads: long Opus driver sessions re-reading their own growing context. Subagents were
> 8% of the total. A generic subagent loaded ~37.5k tokens before its brief; lean profiles load
> 4.8-8.2k. Re-priced on the lean profiles, past subagents drop 7% (same models) or 34% (opus
> subagents moved to sonnet) -- ~3% of total spend. The big lever is the driver, not the subagents.
> **Use when:** deciding where to cut token spend, doubting the lean profiles, or re-running the
> audit after the rules change.
> **Area:** agent practice

Figures are list-price equivalents (Anthropic first-party API, prices cached 2026-06-24), not
what a subscription bills. Only transcripts still on disk count. Every number below comes from
`scripts/tally_cost.py --source claude-code`; re-run it, do not re-derive.

## Where the spend goes

| Component | Share |
|---|---|
| cache reads (whole context re-read every turn) | 66% |
| cache writes | 24% |
| output | 11% |
| uncached input | ~0% |

| Who | Spend |
|---|---|
| Opus 5 driver sessions | ~90% |
| subagents (all models) | ~8% ($367) |
| of which opus subagents | $164 |
| of which haiku subagents | $6 |

Single iteration sessions reached 100-200M cache-read tokens and $50-170 each.

## Did delegation pay?

| Arm (observational, different tasks) | Sessions | Median $/session | Median $/prompt |
|---|---|---|---|
| spawned subagents | 16 | $29 | $4.32 |
| never delegated | 70 | $40 | $7.12 |

Suggestive, not controlled. Subagents were 47% of the delegating sessions' spend, ~$2.76 each.
The sprinter tier was barely used ($6 of haiku against $164 of opus subagents): the saving the
tier rule promises was mostly not taken.

## What a subagent loads before its brief

The driver's conversation never reaches a subagent (forks aside). The fixed part is its own
system prompt, every allowed tool schema (MCP servers included), the skills list and CLAUDE.md
with its imports. Median first request minus brief, by agent type:

| Agent type | Fixed context |
|---|---|
| `general-purpose` | 37,551 |
| `claude-code-guide` | 26,526 |
| `architect` (lean, opus, keeps CLAUDE.md) | 16,833 |
| `Explore` | 12,493 |
| `unity-editor-cli` (`tools:` Bash, Read, Grep, Glob) | 9,683 |
| `workhorse` (lean, sonnet) | 8,174 |
| `sprinter` (lean, haiku, edits) | 6,277 |
| `scout` (lean, haiku, read-only) | 5,522 |
| `runner` (lean, haiku, Bash only) | 4,830 |

A haiku `general-purpose` agent spent 34,490 tokens to run one `wc -l`. Restricting `tools:`
is most of the win (the MCP and Editor tools are the bulk); `omitClaudeMd: true` takes the rest
(CLAUDE.md with its imports is ~8.6k here -- the architect/workhorse gap). The lean profiles
were measured on that same one-line task; all five answered correctly.

## Projection: the same history on lean profiles

Each past subagent's requests re-priced with the prefix shrunk by (observed - lean) fixed
context: at the request's blended input rate on the first request, at the cache-read rate after
(assumes cache hits, so conservative). 95 of 133 subagents were types a lean profile replaces.

| Scenario | Subagent spend | Change |
|---|---|---|
| actual | $367 | -- |
| A: lean profiles, same models | $342 | -7% |
| B: lean + opus subagents at sonnet | $243 | -34% |

Why A is small: after the first request the fixed prefix is a cache read at 0.1x input price.
A subagent's cost is its own reading and output, so the model it runs on matters more than its
starting context. The lean profiles pin the model, which is what makes B the default path.

## Levers, biggest first

1. **Driver session length.** Cache reads on long Opus sessions are two thirds of spend. Start
   a fresh iteration (with a handoff file) or compact earlier.
2. **Tier the subagents.** Opus subagents at sonnet price is most of the subagent saving;
   `pick_agent.py` and the lean profiles pin the model per profile.
3. **Lean profiles.** 4-8x less fixed context per spawn; matters most for many short
   subagents, and moves the inline floor from ~37k to ~5-8k.
4. **Standing context.** Everything imported into CLAUDE.md and the memory index is re-read on
   every driver turn; trim it.

## Re-run

```sh
S=<skill dir>/scripts
python3 $S/tally_cost.py --source claude-code --arms-auto --recent 500   # spend, arms
python3 $S/tally_cost.py --source claude-code --agent-overhead           # fixed context by type
python3 $S/tally_cost.py --source claude-code --lean-projection          # re-price on lean
```

Update `OVERHEAD` in `pick_agent.py` and `LEAN_FIXED` in `tally_cost.py` when the measured
fixed context drifts (a harness update, a new MCP server, a longer CLAUDE.md).
