---
name: context-claude-code-subagent-model
description: How Claude Code resolves a subagent's model (call > frontmatter > CLAUDE_CODE_SUBAGENT_MODEL > parent), the two env vars not to set, why a hook cannot rewrite the spawn's model, how this was verified from the shipped binary and how to re-verify after an upgrade, and the classifier lesson behind the negation rule.
metadata:
  type: report
  skill: model-routing
  verified_against: Claude Code 2.1.314 (VS Code extension native binary)
  updated: 2026-11-01
---

# Claude Code subagent model: resolution, knobs, evidence

> **Summary:** A spawn's model comes from the call's `model`, then the agent file's `model`,
> then `CLAUDE_CODE_SUBAGENT_MODEL`, then the parent's model. Setting that env var to the
> workhorse alias moves every un-modelled spawn off the frontier model. Pinned profiles and
> explicit `opus` calls are unaffected. `_FORCE` and the workflow variant override explicit
> picks, so don't set them. A user hook cannot rewrite the spawn's input: the call is refused.

## Resolution order

| # | Source | Notes |
|---|---|---|
| 1 | `model` on the Agent/Task call | `inherit` means the parent's model |
| 2 | `model:` in the agent's frontmatter | the lean profiles pin haiku / sonnet / opus here |
| 3 | `CLAUDE_CODE_SUBAGENT_MODEL` (env, or `env` in user `settings.json`) | the harness default, set to `sonnet` |
| 4 | the parent session's model | what a generic agent got before: Opus on a frontier driver |

The `fork` subagent type always inherits the parent, whatever is set. A family alias passes
through an `availableModels` allowlist: one that is not allowed steps down to the newest
allowed model in the family, or inherits.

## Env vars not to set

- `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` -- drops the call's and the frontmatter's `model`, so an
  explicit architect (`opus`) spawn silently runs on the default.
- `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` -- when set, every workflow `agent()` model is ignored
  ("Workflow agent model ... ignored").

## Why not a hook

A PreToolUse hook can return `updatedInput`, but in some session modes a hook from the user's
own settings that changes a tool call's input gets the call **denied**, with the message "A hook
from the user's own Claude Code files tried to change this tool call's input ... the call was
not made". A deny-only hook works. But it makes a frontier driver write the whole brief again,
which costs more output tokens than the downgrade saves. The env default plus `pick_model.py`
are the enforcement.

## How it was verified, and re-verifying after an upgrade

The docs did not cover precedence, so it was read from the shipped bundle: a stdlib scan of
the native binary for the env names, printing the code around them (function `de(...)`:
`if FORCE: e = undefined; if e === "inherit": parent; if e: e; else env-default; else parent`).
After a Claude Code upgrade, re-run the scan and compare against the table above:

```
python3 - <<'EOF'
import re, pathlib
ext = sorted(pathlib.Path.home().glob(".vscode/extensions/anthropic.claude-code-*"))[-1]
b = (ext / "resources/native-binary/claude").read_bytes()   # or the CLI binary on PATH
for m in list(re.finditer(rb"CLAUDE_CODE_SUBAGENT_MODEL_FORCE\)", b))[:2]:
    print(b[m.start() - 300:m.end() + 400].decode("utf-8", "replace"), "\n====")
EOF
```

Then update `verified_against` here and the precedence line in `../SKILL.md`.

## Classifier lesson

`pick_model.py` first treated "without" and a whole sentence after "do not" as negated. That
erased "count them" and, worse, a trailing "rank them", which sent judgment work to haiku. A
negation now covers only its own clause and stops at and / then / but or punctuation. The
general rule: a classifier error that **drops** a signal is worse than one that adds one,
because dropped judgment signals route down a tier. Keep a test brief for each such case
(`scripts/test_pick_model.py`).
