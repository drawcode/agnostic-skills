## Deterministic scripts

Model tokens are for thinking; anything with a re-derivable answer is a script.

Every prompt, before doing the work:
1. Spot the deterministic, recurring steps (counts, inventories, greps, parsing, conversion,
   version/build reads, device/build recipes, checks). Judgment stays with the model.
2. Reuse: check the scripts index (`README.md` `## Index`) and run what exists.
3. Write one when the step will recur. Scaffold on-contract with
   `new_script.py --dir <area> --name <snake_case> --summary "<one line>" [--config] --write`,
   then lint with `check_scripts.py <area> --run-help`. `repeat_scan.py <transcript>` finds the
   repeated commands worth scripting.
4. Delegated briefs name the script and its exact command line.

Where: generic scripts in the shared scripts folder, project-only ones in that project's area.
Skills link to scripts, never copy them.

Contract: stdlib python3 (shell only with a stated `portability-exception`, raised with the
owner; node only where python cannot run); header doc; `--help`; `--json`; exit 0 ok / 1 failed /
2 bad input; read-only unless `--write`; indexed in the nearest README.

No personal, machine or secret values in scripts, prompts, skills, briefs, flows or commits.
A script reads them at run time from one home config file
(`~/.config/deterministic-scripts/config.env`, or `DETERMINISTIC_SCRIPTS_CONFIG`) with
`homecfg.py get <key>`; ask the script for the value, never paste it. Secrets stay in their own
files; the config holds the path.

In prose write `<config:key>` for such a value and `~/...` for a home path. Enforced by
`scan_sensitive.py` (the home config is the deny list), fixed by `scrub_sensitive.py --write`,
and blocked at commit by `hooks/pre-commit` (`install_hooks.py --write`). `.sensitive-allow`
takes public or fake values only.

Subagents run scripts; they write a new one only when their brief says so.

Scripts: `<scripts>` = the kit's scripts folder (`homecfg.py`, `new_script.py`, `check_scripts.py`,
`repeat_scan.py`, `scan_sensitive.py`, `scrub_sensitive.py`, `install_hooks.py`, `test_scripting.py`).
