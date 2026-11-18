---
name: context-per-prompt-delegation-loop
description: The per-prompt routine (estimate inline-vs-delegate, delegate to the cheapest capable tier, tally estimated vs actual cost, script deterministic steps) and the two read-only scripts behind it -- estimate_delegation.py and tally_cost.py -- with their argument, exit-code and output contracts and how each harness reaches them.
metadata:
  type: reference
  skill: tiered-delegation
  updated: 2026-09-26
---

# Per-prompt delegation loop

> Driver only, one level deep: a spawned subagent does not run this loop and never spawns
> subagents of its own. See `SKILL.md` -> "Driver only, one level deep".

> **Summary:** A standing routine run before doing the work on any prompt: estimate inline vs
> delegate with a script, delegate what fits to the cheapest capable tier, tally what it actually
> cost when that is cheap, and turn any deterministic step into a script rather than model tokens.
> **Use when:** starting any non-trivial prompt, deciding whether to fan out to subagents, or
> reconciling what a delegated run cost.
> **Area:** agent practice

The *why* is [context-tiered-delegation-protocol.md](context-tiered-delegation-protocol.md). This
doc is *which command*.

## The loop

1. **Estimate.** Run `estimate_delegation.py` on the files and dirs the task touches. It prints
   `VERDICT: delegate|inline` from the inline-floor arithmetic -- no model call.
2. **Delegate what fits, to the tier that fits.** On `delegate`, fan independent, well-briefed
   chunks out in parallel to the cheapest capable tier; keep only synthesis in the orchestrator.
   Size the fan-out to the task.
3. **Tally, when cheap.** After a delegated run, reconcile with `tally_cost.py` if the harness
   records usage.
4. **Script deterministic steps.** Counts, inventories, greps, parsing, arithmetic and cost math
   are scripts, not tokens. New ones go beside [../scripts/](../scripts/), or in the project's own scripts folder, with `--help`,
   `--json`, exit `0` ok / `2` bad input, read-only by default, and a documented contract.
5. **Improve without cutting quality.** Spend savings on coverage; sharpen a brief rather than
   downgrade a model; promote a question rather than guess.
6. **Stay harness-agnostic.** Briefs name files, symbols and the expected output shape, never a
   harness-specific tool. Scripts run from a bare terminal.

## scripts/estimate_delegation.py

Deterministic, read-only, stdlib `python3` only. No model, no network. Estimates
orchestrator-context tokens for inline vs delegated execution and emits a routing verdict against
an overhead floor. Skips macOS `._` sidecars and `.DS_Store` when walking a dir.

- **Args:** `--paths P ...` (files/dirs; `--ext .ts .py` filters a dir walk) **or** `--bytes N`
  **or** `--tokens N`; `--shards N` (default auto, one per ~50k tokens, 1..8),
  `--bytes-per-token F` (default 3.7; code ~3.5, prose ~4), `--summary-tokens` (400),
  `--brief-tokens` (250), `--overhead-tokens` (6000), `--json`.
- **Exit:** `0` verdict produced; `2` bad input or missing path.
- **Output:** `VERDICT: delegate|inline (...)`, or with `--json`
  `{verdict,payload_tokens,files,shards,orchestrator_inline_tokens,orchestrator_delegated_tokens,ratio,reason}`.
- **Rule:** `inline` when the payload, or each shard's slice of it, is under the overhead floor.

```
# from the repo root
python3 <skill dir>/scripts/estimate_delegation.py --paths src --ext .ts
python3 <skill dir>/scripts/estimate_delegation.py --tokens 2000 --json    # -> inline
```

## scripts/tally_cost.py

Deterministic, read-only. A harness-agnostic core over pluggable adapters. Sums real token and
cost per session, and compares a delegated arm to an inline arm.

- **Args:** `--source opencode` (default), `--db PATH`, `--recent N` | `--ids ID ...`,
  `--delegated ID ...` with `--inline ID ...` for an arm comparison, `--records-json PATH`
  (bring your own records), `--json`.
- **Exit:** `0` tally produced; `2` bad input or no records.
- **Output:** a fixed-width table and a `TOTAL` line, or with `--json`
  `{sessions,totals,comparison}`. Token fields are new (billable) tokens = input + output;
  cache reads are reported separately.
- **Adapters:** one ships -- `opencode`, reading the SQLite `session` table. Add a harness by
  writing `load_<name>(args) -> list[record]` with keys
  `{id,title,agent,model,cost,tokens_input,tokens_output,tokens_cache_read}` and registering it
  in `SOURCES`. A harness with no usage store feeds `--records-json`.

There is **no Claude Code adapter yet**. Claude Code keeps per-turn `usage` in its session
transcripts rather than a cost table, so tallying a Claude Code run today means building the
record list yourself and passing `--records-json`.

## Tooling per harness

| Capability | Terminal (preferred) | opencode | Claude Code / Codex / other |
|---|---|---|---|
| Estimate inline vs delegate | `python3 <skill dir>/scripts/estimate_delegation.py --paths ...` | `bash` tool, same script | shell tool, same script |
| Tally actual cost | `python3 <skill dir>/scripts/tally_cost.py --recent N` | `bash` tool, same script | shell tool; `--records-json` |
| Spawn a tiered subagent | n/a -- a harness feature | `Task` tool, tier via a `subagent_type` pinned to a model | `Agent` tool with explicit `model`, or the harness's subagent feature |

A subagent with no pinned model runs at the driver's rate, which throws away most of the saving.

## Measured basis

Same-task comparison on a ~150-file, ~1.8 MB code survey, subagents still at
the frontier rate: delegated **$1.95** (259k new tokens, 0.81M processed, full coverage) vs inline
**$3.19** (358k new tokens, 2.18M processed, capped on one file). Delegation won with no cheap-tier
discount at all; pinning the subagents to the small tier projects to ~$0.30-0.50. A trivial
`wc -l` went the other way -- free inline, a wasted agent context delegated. That is the inline
floor.
