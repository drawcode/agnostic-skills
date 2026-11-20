## Tiered model delegation

The constraint is token budget, not capability. Pick the model per unit of work, never per
session, and spend the savings on coverage (more subtasks, more verification), not a bigger model.

### Driver only, one level deep

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

### Tiers
| Tier | Role (Claude model) | Runs |
|---|---|---|
| architect | strongest reasoning model (opus; fable only when truly needed, it is not a cheap lane) | ambiguous scope, root cause, plan authoring, final review of a risky diff, arbitrating promoted questions. At most one per phase. |
| workhorse | solid mid model (sonnet) | the default: implementation against a known target, bounded-diff review, PR text, multi-file edits with named files, anything that ranks or summarizes |
| sprinter | small, fast model (haiku) | pure extraction and mechanics with context handed in: greps, inventories, log scraping, renames, schema fills, formatting, ids/counts/lists |

Default to the cheapest tier that can plausibly finish. If a subtask cannot drop a tier, its
brief is under-specified: sharpen the brief, do not upgrade the model.

### Lean profiles (the brief is the whole context)
Spawn the smallest profile that has what the work needs, never a generic agent by default:
`runner` (Bash, 4.8k fixed) · `scout` (read-only, 5.5k) · `sprinter` (edits, 6.3k) ·
`workhorse` (sonnet, 8.2k) · `architect` (opus, 16.8k), vs ~37.5k for `general-purpose`.
Pick: `python3 <skill dir>/scripts/pick_agent.py --does <kind> [--needs web,mcp]`.
Lean profiles skip CLAUDE.md, so the brief carries every file, command and project rule the
work needs. Fall back to a heavier agent only for a need none carries, still with the tier's model
passed explicitly (a generic agent without one inherits the driver's model). Downgrade any
mechanical part of a workhorse task to a sprinter.

### Per-prompt loop (run before doing the work)
1. Estimate inline vs delegate with a script, not by eye (payload tokens vs ~6k per-agent
   overhead). Below the floor: run inline. Above: delegate.
2. Delegate everything that decomposes into independent, well-specified chunks, each to the
   cheapest capable tier. Fan independent chunks out in one message. Size the fan-out to the task.
3. After a delegated run, tally estimated vs actual cost when the harness records usage and
   pulling the numbers is cheap.
4. Any step with a re-derivable correct answer (counts, inventories, greps, parsing, arithmetic,
   cost math) becomes a script, not model tokens. Scripts: stdlib python3 (shell only by stated exception), `--help`, `--json`,
   exit 0 ok / 2 bad input, read-only by default, documented before relied on.
5. Never trade correctness for a cheaper tier. Promote a question instead of guessing.
6. Keep briefs and scripts harness-agnostic: briefs name files, symbols and expected output
   shape, never a harness-specific tool; scripts run from a bare terminal.

### Frontier driver rule
When the driving model is frontier (opus, fable or above), all mechanical work leaves the
session, even outside a plan: CLI runs, greps, finds, parsing, file/folder creation, edits with a
known target, renames, formatting, log scraping. The driver keeps scoping, root cause, reasoned
wording, synthesis and final review.
- Batch the mechanical steps into one brief for one agent; never one agent per command.
- Only exception: a lone scout call (one ls, one grep) whose answer the driver needs before its
  next decision.
- On a sonnet or haiku driver, the plain inline floor applies instead.

### Inline floor
The cheapest tier for a one-liner is no agent. A single find/grep/ls/wc, a one-file read or a
stat runs inline. Every subagent pays a system prompt, tool schemas, a fresh context, the brief,
its reasoning and the marshalled result (~5-8k lean, ~37k generic). Terse briefs on trivial delegated work can be refused as
suspected prompt injection. Delegate only when the payload amortizes the overhead.

### Mini plan (every multi-step task opens with one)
    ## Plan: <task>
    Goal: <what done looks like>
    Known: <established facts, so no subtask re-derives them>
    ### Phase 1 -- <name>   [gate: a checkable fact, e.g. "compiles clean", "grep returns 0"]
    - [ ] 1.1 <subtask>  `sprinter`   context: <files, symbols, exact command>
    - [ ] 1.2 <subtask>  `sprinter`   context: <...>   (parallel with 1.1)
    - [ ] 1.3 <subtask>  `workhorse`  context: <...>   (needs 1.1)
    Open questions for the dev: <only what truly blocks>
- Every subtask has a tier and a context line, and names the script that performs it. If no
  script exists, writing it is that phase's first subtask (architect/workhorse work).
- Name files, symbols and lines in the subtask; a subtask that must find its target costs a tier more.
- Scout inline first when discovery is one grep, then fan out over what you found.
- Sequence by real dependency only. A brief longer than a paragraph is two subtasks.
- Choosing a tier: target named + change mechanical -> sprinter; target named + change needs
  judgment about surrounding code -> workhorse; the target itself is the question -> architect,
  whose output is a plan, not a diff.

### Skills and subagents
A skill body runs inline in the calling session, so invoking a skill never changes the model.
The lever is inside it: each self-contained skill step becomes a subagent call with an explicit
model and a complete brief. Sweeps and per-file passes fan out at sprinter tier; synthesis stays
with the driver. Choose the tier from skill + task, not the skill's size.

### Escalation: promote the question, never guess
    sprinter --+
               +--> workhorse --> architect --> dev (the user)
    workhorse -+
Promote when: a named file/symbol/flag does not exist; two readings give materially different
work; the change would leave the named scope; a gate fails with no in-brief fix; the work is a
tier above what was assigned. Not reasons: tedious, large file, a command needed a retry.
Return this block instead of a partial result:
    BLOCKED
    Subtask: <id and one-line goal>
    Tried: <what was run or read>
    Found: <the fact that broke the brief>
    Question: <one question, yes/no or name-a-thing>
    Options: <a> / <b> -- and which one you would pick if forced
    Cost to unblock: <what becomes possible once answered>
Routing: facts go peer-to-peer to the agent that holds the context; decisions go up a tier; only
the architect asks the human, only for intent, priority or ship risk, routed through the driving
session, batched. When answering, re-issue the brief with the gap closed. The same question from
two agents means the plan is wrong. A mis-tiered subtask is re-issued at the right tier, not
finished by the driver.

### Do not
- Spawn an agent for work smaller than its own overhead (but on a frontier driver, batch the
  rest of the mechanical work out).
- Run a mechanical pass at architect tier because the feature is important.
- Hand a vague goal to a big model to compensate.
- Let haiku write anything that ranks, summarizes or picks "the most important" item.
- Let a subagent spawn a subagent, or run this rule inside a subagent.
- Let a stuck agent improvise.

Estimator: `python3 <skill dir>/scripts/estimate_delegation.py --paths <files/dirs> [--ext .ts] [--json]`
