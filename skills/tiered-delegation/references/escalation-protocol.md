---
name: escalation-protocol
description: Covers when a subagent should stop and promote a blocked question instead of guessing, the BLOCKED report format, peer-to-peer versus up-tier versus to-the-dev routing, and how to answer a promoted question. Read when a subtask hits something outside its brief.
metadata:
  type: reference
  skill: tiered-delegation
  updated: 2026-09-26
---

# Escalation protocol

A subagent that cannot finish inside its brief stops and promotes the question. It does not
guess, does not widen its scope, and does not invent a fallback.

## When to promote

- The work is too big for one agent. Promote it ("needs splitting"); never spawn a sub-subagent.
- The brief names a file, symbol or flag that does not exist.
- Two readings of the instruction would produce materially different work.
- The change would touch code outside the named scope.
- A gate fails and the fix is not obviously inside the brief.
- The work turns out to be a tier above what was assigned.

Not reasons to promote: the task is tedious, the file is large, a command needed a retry.

## The block format

Return this instead of a partial result, so the receiver can answer in one hop:

```markdown
BLOCKED
Subtask: <id and one-line goal>
Tried: <what was actually run or read, briefly>
Found: <the fact that broke the brief>
Question: <one question, answerable yes/no or by naming a thing>
Options: <a> / <b>  -- and which one the agent would pick if forced
Cost to unblock: <what becomes possible once answered>
```

Always include the "would pick if forced" line. Often it is right and the receiver can just
confirm, which is the cheapest possible resolution.

## Routing

- **Peer to peer:** `ListAgents` to see who is live, `SendMessage` to ask the agent that already
  holds the context. Use this for facts -- "which file did you find it in".
- **Up a tier:** when the answer is a decision, not a fact. Sprinter to workhorse, workhorse to
  architect.
- **To the dev:** only the architect escalates to a human, and only for intent, priority, or
  ship risk. It routes through the orchestrating session -- a subagent cannot reliably prompt
  the user itself. Batch questions; one interruption with three questions beats three
  interruptions.

## Answering a promoted question

- Answer the question and re-issue the brief with the gap closed. Do not just say "yes".
- If the same question arrives twice from different agents, the plan is wrong, not the agents --
  fix the plan's Known section.
- If the answer means the subtask was mis-tiered, re-issue it at the right tier rather than
  finishing it yourself at the orchestrator's tier.
