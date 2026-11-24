---
name: deterministic-scripts
description: Use on every prompt, before doing the work -- spot the steps that are deterministic and will recur (counts, inventories, greps, parsing, format conversion, version/build reads, device and build recipes, checks) and run or write a script for them instead of spending model tokens. Covers where scripts live, the script contract (--help, --json, exit codes), keeping personal/machine values and secrets out of scripts, prompts and flows (a script reads them from a home config file), a pre-commit scan that blocks leaks, scaffolding a new script, linting scripts, and finding repeated commands in a transcript. Also use when a task repeats something done before, when a subagent needs a consistent answer, or when asked to make work cheaper or more repeatable.
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
---

# Deterministic scripts

Model tokens are for thinking. Anything with a re-derivable correct answer is a script: the
main thread stays on the reasoning, and a smaller-model subagent calling the script gets the
same answer every time instead of re-deriving it.

`<scripts>` below is this kit's `scripts/` folder (next to this file).

## Per-prompt check

Before doing the work:

1. **Spot** the deterministic steps: counts, inventories, greps and sweeps, parsing and
   extraction, format conversion, version and build-number reads, device/build/deploy recipes,
   cost math, anything a guard checks. Judgment, wording and root cause stay with the model.
2. **Reuse** first: look in the scripts index (a `README.md` with a `## Index` table) for one
   that already does it. Run it; do not re-derive its answer by hand.
3. **Write** one when the step will recur: done by hand twice means the second time writes the
   script. Scaffold it so it starts on-contract:
   `python3 <scripts>/new_script.py --dir <area> --name <snake_case> --summary "<one line>" [--config] --write`
   Python is the default and the norm. Shell only when it cannot be python (sourced file, git
   hook entry); then `--lang bash --reason "<why>"`, and tell the owner.
4. **Lint** before relying on it: `python3 <scripts>/check_scripts.py <area> --run-help`
5. **Hand it down**: a delegated brief names the script and its exact command line, so the
   subagent runs it rather than improvising.

## Thread -> scripts

At the end of a thread, or when the same thing has been typed twice:

1. **Find the repeats.** `python3 <scripts>/repeat_scan.py <transcript or history> [--json]`
   groups commands by shape (paths, shas, numbers, strings normalized away) and ranks them by
   count and chain length. It reads Claude Code and Codex JSONL transcripts, shell history, or
   one command per line; with no path it tells you where each harness keeps its transcripts.
2. **Pick candidates**: anything run 2+ times, any multi-step chain, any wait or poll, anything
   whose result had to be checked by eye. Not one-off exploration.
3. **Scaffold, lint, document** as above. The owning doc names the script by path with its
   arguments, exit codes and output: never an undocumented script, never a documented
   procedure left unscripted.

## Where scripts go

- Generic and portable: one shared scripts folder, `<scripts root>/<area>/`.
- Genuinely one project's: that project's area. Write generic first; a script is
  project-specific only when the project cannot be a parameter.
- A skill's scripts live in one place; the skill links to them, never a copy.
- Every script gets a row in the nearest `README.md` `## Index` (the scaffolder adds it).

The contract (language, header doc, `--help`, `--json`, exit codes, read-only default, small
output a lower tier can read blind, verify the effect and fail loudly): `references/script-contract.md`.

## No personal values or secrets in scripts, prompts or skills

Names, emails, ids, serials, hostnames, absolute home paths, key paths, tokens, passwords:
never in a script, prompt, skill, brief, flow or commit. They live in one home config file,
and a script reads the one it needs at run time:

- Python: `homecfg.get("device_serial")` (raises `homecfg.Missing` -> exit 2)
- Shell: `python3 <scripts>/homecfg.py get device_serial`

The agent never needs the value itself. It names the key, runs the script, and reads the
script's result. In prose (docs, handoffs, plans) write `<config:key>` where the value would
go and `~/...` for a home path. Secrets stay in their own files or a keychain; the config
holds the path, never the secret.

Enforced, not advised:
- `scan_sensitive.py` finds home paths, emails, private keys, tokens (ghp_, github_pat_, sk-,
  AKIA, xox*, Bearer), `password=`/`secret=` assignments, `.env`-style KEY=value secrets,
  device ids, **every value in the home config**, and any extra pattern or literal you add
  (config, env, `patterns.txt`). The config file is the deny list: a value kept there once
  is blocked everywhere else.
- `scrub_sensitive.py --write` rewrites what it can to `~/...` and `<config:key>`.
- `hooks/pre-commit` (installed with `install_hooks.py --write`) blocks any commit that ADDS
  such a value or stages a script that breaks the contract. It is a git hook, so it holds for
  every tool and person, not one harness.
- `.sensitive-allow` at a repo root takes PUBLIC or FAKE values only (`GLOB :: REGEX` scopes
  a line to matching files, e.g. test fixtures).

Config file, patterns and the "keep the harness away from secrets" flow: `INSTALL.md`,
`references/config-convention.md`.

## Subagents

A subagent **runs** the scripts its brief names and any it finds in the index. It **writes** a
new script only when its brief says to; otherwise it returns the repeated step as a suggestion
in its result, and the driving session decides.

## In plans and reviews

- Every phase or subtask in a plan names the script that does it. If none exists, writing and
  documenting it is that phase's first subtask.
- When a flow changes, fix the script and its doc in the same pass. A stale script is a finding.

## Do not

- Re-derive by hand what a listed script answers.
- Hard-code a person, device, machine or account value "just for now". Add the key to the config.
- Paste a secret, token or personal value into a prompt, script, skill or commit.
- Copy a script into a skill or a project. Link it.
- Rely on a script that `check_scripts.py` fails.
- Bypass the hook with `--no-verify` for a real value. Move it to the config instead.
- Allowlist a personal, device or account value. The allowlist is for public and fake ones.
