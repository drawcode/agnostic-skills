---
name: mini-plan-template
description: Covers the mini-plan markdown template for a multi-phase task -- goal, known facts, phase gates, per-subtask tier and context tags, and rules for choosing sprinter, workhorse or architect. Read before starting delegated work.
metadata:
  type: reference
  skill: tiered-delegation
  updated: 2026-09-26
---

# Mini plan template

Write this before starting the work, and keep it updated as phases close. On repo work it lands
in the iteration handoff doc alongside the findings.

```markdown
## Plan: <task>

Goal: <one line -- what "done" looks like>
Known: <what is already established, so no subtask re-derives it>

### Phase 1 -- <name>       [gate: what must be true to move on]
- [ ] 1.1 <subtask>   `sprinter`   context: <files, symbols, command>
- [ ] 1.2 <subtask>   `sprinter`   context: <...>            (parallel with 1.1)
- [ ] 1.3 <subtask>   `workhorse`  context: <...>            (needs 1.1)

### Phase 2 -- <name>
- [ ] 2.1 <subtask>   `workhorse`  context: <...>
- [ ] 2.2 review the diff   `architect`   context: the phase 2 diff + the gate name

Open questions for the dev: <only what genuinely blocks; empty is the good case>
```

## Tagging rules

- Every subtask carries a tier and a context line. A subtask with no context line is not ready
  to delegate.
- Mark parallelism explicitly. Anything unmarked is assumed sequential.
- A phase gate is a fact that must hold, not a vibe: "compiles clean", "15/15 green",
  "the grep returns zero hits".
- If a subtask needs more than roughly a paragraph of brief, it is really two subtasks.

## Choosing the tier, in order

1. Is the target named and the change mechanical? -> `sprinter`.
2. Is the target named but the change needs judgement about the surrounding code? -> `workhorse`.
3. Is the target itself the question? -> `architect`, and its output is a plan, not a diff.
- Every subtask brief ends with: `You are a subagent: do this work yourself. Do not spawn
  subagents or apply delegation rules.` Delegation is one level deep; only the driver fans out.
