---
name: script-contract
description: The contract every deterministic script meets — language, header doc, --help, --json, exit codes, read-only default, indexing — and what check_scripts.py verifies.
---

# Script contract

| Rule | Why |
|---|---|
| Stdlib `python3`, runs from a bare terminal | Any harness, any model, any OS, CI |
| Node/JS only where python cannot run; bash/zsh only with a `# portability-exception: <reason>` header, raised with the owner | Shell dialects (bash vs zsh, GNU vs BSD, none on Windows) break portability |
| Header doc: first line says what it does; then inputs, method, output, exit codes | The header is the documentation; nothing relies on a script before it is documented |
| `--help` | A subagent learns the interface without reading the source |
| `--json` machine output; human output otherwise | Callers parse, not scrape |
| Exit `0` ok, `1` the check/operation failed, `2` bad input (missing arg, path, config key) | Callers branch on the code, not on text |
| Read-only by default; writing needs an explicit flag (`--write`) named in `--help` | A dry run is always safe to hand to a subagent |
| Diagnostics to stderr, results to stdout | `--json` stays parseable |
| No personal/machine values or secrets; read them via `homecfg.py` | Shareable, portable, no leaks |
| Small output: JSON or TSV plus one human line; bulky artifacts to a scratch dir, report the path | A lower-tier model can read it blind |
| Idempotent and safe to re-run; verify the effect (file exists, value changed), not the call | A step that cannot prove it happened makes a broken run look clean |
| A row in the nearest `README.md` `## Index` | Discoverable before anyone re-derives it |
| Lowercase snake_case file names | Consistent, sortable |

## Checked by `check_scripts.py`

`shebang`, `exec` (executable bit), `doc`, `lang` (shell needs a stated exception), `contract` (`--json`, documented exit codes, `--help`),
`extension` (python entrypoints are `.py`), `indexed`, `personal` (every `scan_sensitive.py` rule: home paths, emails, tokens, secret assignments, literal config values),
and with `--run-help`, that `--help` exits 0.

It is a lint, not a proof: it looks for the words, and cannot tell whether `--json` output is
correct. Test the script on a real input once before relying on it.
