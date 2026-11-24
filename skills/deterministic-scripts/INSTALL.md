# Deterministic scripts kit

Moves repeatable, re-derivable steps out of model tokens into scripts, keeps personal and
machine values in one home config file that scripts read, and ships the tools to scaffold,
lint and guard them.

## What is in it

| Path | What it is |
|---|---|
| `SKILL.md` | The rules as a skill |
| `prompt-deterministic-scripts.md` | The same rules as one paste-ready block, for harnesses without skills |
| `references/script-contract.md` | The script contract and what the linter checks |
| `references/config-convention.md` | Home config file: location, format, key naming, extra scan patterns |
| `scripts/homecfg.py` | Reads the home config (`get`, `keys`, `path`, `check`, `where`); python import too |
| `scripts/new_script.py` | Scaffolds a script that already meets the contract and indexes it |
| `scripts/check_scripts.py` | Lints scripts against the contract |
| `scripts/repeat_scan.py` | Finds repeated commands in a transcript or shell history |
| `scripts/scan_sensitive.py`, `scrub_sensitive.py` | Find and fix personal values and secrets |
| `scripts/install_hooks.py`, `scripts/hooks/pre-commit` | The commit guard |
| `scripts/test_scripting.py` | Self-test |

Scripts need only stdlib `python3` (3.9+), touch no network, take `--help`, and exit `0` ok /
`1` failed / `2` bad input. Only the scripts that say `--write` write. The one shell file,
`hooks/pre-commit`, is a git hook and carries its `portability-exception`.

## Install per harness

Keep the kit in one folder and point each harness at it; never copy it per harness.

| Harness | Rules | Skill |
|---|---|---|
| Claude Code | `CLAUDE.md` `@`-imports `prompt-deterministic-scripts.md` (or `AGENTS.md` names it) | link or copy the folder to `.claude/skills/deterministic-scripts` (project) or `~/.claude/skills/deterministic-scripts` (user) |
| opencode / Codex CLI | `AGENTS.md` names or includes the prompt block | its skills dir, as a link |
| Cursor / IDE agents | always-apply project rule pointing at the prompt block | none |
| Raw API / own loop | orchestrator system prompt | none |

In the prompt block, `<scripts>` is the path of this kit's `scripts/` folder in that repo.
Check it runs: `python3 <scripts>/test_scripting.py`.

## Home config (once per machine, never in a repo)

```sh
python3 <scripts>/homecfg.py path          # where it will be read
mkdir -p ~/.config/deterministic-scripts
touch ~/.config/deterministic-scripts/config.env
chmod 600 ~/.config/deterministic-scripts/config.env
```

On Windows the folder is `%APPDATA%\deterministic-scripts`. Override with
`DETERMINISTIC_SCRIPTS_CONFIG` (a file) or `DETERMINISTIC_SCRIPTS_HOME` (a folder). Put
`KEY=value` lines in it (see `references/config-convention.md`); a script asks for one with
`python3 <scripts>/homecfg.py get <key>` or `homecfg.get("<key>")`. Personal and machine
values come from this file, read by a script, and are never typed into a prompt, a script, a
skill or a commit.

Extra things the scanner should block (your own ticket ids, hostnames, project code names) go
in the same folder: a `patterns.txt` with `REGEX`, `NAME :: REGEX` or `literal: VALUE` lines, or
`pattern_*` keys in `config.env`, or the `DETERMINISTIC_SCRIPTS_PATTERNS` /
`DETERMINISTIC_SCRIPTS_LITERALS` env vars. `homecfg.py where` lists what is read.

## Keep the harness away from auth, configs, PII and secrets

An AI harness sees everything in its prompt, its tool output and the files it opens. The
defence is to never put the value there:

1. **The script holds the access.** Credentials, device ids, accounts and personal details
   live in the home config (or a keychain / credentials file it points to), outside every repo.
2. **The agent asks, the script answers.** The agent names a key or runs a script; the script
   reads the config and does the work, returning only the result (`ok`, a count, a path), not
   the secret. Scripts never echo a secret to stdout.
3. **Nothing is pasted.** A secret, token, personal value or machine path is never typed into
   a prompt, a script, a skill, a brief or a commit message. Prose says `<config:key>` or `~/...`.
4. **The commit blocks leaks.** `hooks/pre-commit` scans every line a commit adds, for home
   paths, emails, private keys, tokens, `password=`/`secret=` assignments, `.env`-style secrets,
   and every value in the config. A hit stops the commit; fix it with
   `scrub_sensitive.py <dir> --write` or move the value to the config.
5. **Audit anytime.** `scan_sensitive.py --tracked` scans what is already committed. History
   already pushed is not rewritten by any of this: rotate a secret that has leaked.

Hits print redacted (first 3 characters), so the scan output does not leak what it found.

## The commit guard (once per clone)

`install_hooks.py` reports first and changes nothing until `--write`:

```sh
python3 <scripts>/install_hooks.py <repo>                    # report: installed or not (exit 0 / 1)
python3 <scripts>/install_hooks.py <repo> --write            # set core.hooksPath (relative)
python3 <scripts>/install_hooks.py <repo> --uninstall        # report what removal would do
python3 <scripts>/install_hooks.py <repo> --uninstall --write # unset core.hooksPath
python3 <scripts>/scan_sensitive.py --tracked                # audit what is committed
python3 <scripts>/scrub_sensitive.py <dir> --write           # fix: ~/... and <config:key>
```

It refuses (exit 2) when the repo already has its own hooks or another `core.hooksPath`, so it
never silently disables them; merge by hand. `core.hooksPath` is per-clone git config, so each
new clone runs the `--write` once. `.sensitive-allow` at the repo root lists PUBLIC or FAKE
values only (`REGEX` or `GLOB :: REGEX`).

## Uninstall

`install_hooks.py <repo> --uninstall --write` in each repo, then delete the kit folder, the
harness link and the pasted block. The home config is yours; nothing else is written.
