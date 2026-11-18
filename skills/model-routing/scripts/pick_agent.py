#!/usr/bin/env python3
"""Pick the leanest subagent profile for a unit of work

Deterministic, read-only. The driver says what the work IS and what it NEEDS beyond local
files and a shell; this maps that to the subagent profile that loads the least fixed context
and still has what the work needs. Every tool, MCP server, skill and CLAUDE.md a subagent does
not need is context it pays for before reading its brief, so the smallest profile that fits wins.

Built-in profiles: runner, scout, sprinter, workhorse, architect, general-purpose. A workspace
adds its own profiles and needs from a JSON file; the first that exists wins:
  1. $TIERED_DELEGATION_PROFILES (a path)
  2. pick_agent.profiles.json beside this script (the script's real path, symlinks resolved)
  3. ~/.config/tiered-delegation/profiles.json
Schema (all keys optional):
  {"profiles": {"<agent>": {"model": "haiku", "fixed_tokens": 5400}},
   "needs": {"<need>": {"light": "<agent>", "light_reason": "...",
                        "heavy": "<agent>", "heavy_reason": "..."}}}
A need with an entry routes to its own agent: light when the kind maps to runner or scout,
heavy otherwise (e.g. an "editor" need naming a runner-style and a fuller editor profile).
A malformed file prints a warning to stderr and the built-ins are used.
Fixed-context numbers are measured, not guessed: re-measure with
  tally_cost.py --source claude-code --agent-overhead
and update OVERHEAD below when they drift.

Usage:
  pick_agent.py --does run                       # a named script/command -> runner
  pick_agent.py --does extract                   # greps, counts, inventories -> scout
  pick_agent.py --does edit                      # mechanical edit, target named -> sprinter
  pick_agent.py --does implement                 # judgment about surrounding code -> workhorse
  pick_agent.py --does plan                      # scope / root cause / risky review -> architect
  pick_agent.py --does extract --needs web       # anything a lean profile lacks -> fallback
  pick_agent.py --does implement --payload-tokens 3000 --json

Options:
  --does KIND            run | extract | edit | implement | review | summarize | plan | diagnose
  --needs LIST           comma list of capabilities beyond Bash/Read/Edit/Write/Grep/Glob:
                         web, mcp, skills, notebook, house-rules,
                         plus any need the profiles file defines
                         (house-rules = the full CLAUDE.md must be in context, not a digest)
  --payload-tokens N     task payload (brief + what it must read); adds an inline/delegate verdict
  --json                 machine-readable output

Output contract:
  stdout  one line: agent, model, fixed overhead, reason (+ verdict), or with --json
          {"agent","model","fixed_tokens","reason","verdict"|null}
  Fallbacks name an agent type that may not exist in every repo; the reason says what to use
  instead.

Exit codes:
  0  ok
  1  the check/operation failed
  2  bad input
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Median fixed context (tokens) of the first request, brief excluded. Measured 2026-09-30.
OVERHEAD = {
    "runner": 4830, "scout": 5522, "sprinter": 6277, "workhorse": 8174,
    "architect": 16833, "general-purpose": 37551,
}
MODEL = {
    "runner": "haiku", "scout": "haiku", "sprinter": "haiku", "workhorse": "sonnet",
    "architect": "opus",
}
DOES = {
    "run": "runner", "extract": "scout", "edit": "sprinter",
    "implement": "workhorse", "review": "workhorse", "summarize": "workhorse",
    "plan": "architect", "diagnose": "architect",
}
NEEDS = {"web", "mcp", "skills", "notebook", "house-rules"}
TIER_MODEL = {"runner": "haiku", "scout": "haiku", "sprinter": "haiku",
              "workhorse": "sonnet", "architect": "opus"}
ROUTED = {}  # need -> {"light","light_reason","heavy","heavy_reason"} from the profiles file
NEED_KEYS = ("light", "light_reason", "heavy", "heavy_reason")


def profiles_path():
    """First existing profiles file: env, beside the script, user config; else None."""
    env = os.environ.get("TIERED_DELEGATION_PROFILES")
    cands = [Path(env)] if env else []
    cands.append(Path(__file__).resolve().parent / "pick_agent.profiles.json")
    cands.append(Path.home() / ".config" / "tiered-delegation" / "profiles.json")
    for c in cands:
        if c.is_file():
            return c
    return None


def load_profiles(path):
    """Merge the file's profiles and needs over the built-ins; on any error warn and keep them."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("top level must be an object")
        profiles, needs = data.get("profiles", {}), data.get("needs", {})
        if not isinstance(profiles, dict) or not isinstance(needs, dict):
            raise ValueError("profiles and needs must be objects")
        new_o, new_m, new_r = {}, {}, {}
        for name, p in profiles.items():
            if (not isinstance(p, dict) or not isinstance(p.get("model"), str)
                    or isinstance(p.get("fixed_tokens"), bool)
                    or not isinstance(p.get("fixed_tokens"), int)):
                raise ValueError(f"profile {name!r} needs a string model and an int fixed_tokens")
            new_o[name], new_m[name] = p["fixed_tokens"], p["model"]
        for name, n in needs.items():
            if not isinstance(n, dict) or not all(isinstance(n.get(k), str) for k in NEED_KEYS):
                raise ValueError(f"need {name!r} needs string {', '.join(NEED_KEYS)}")
            for k in ("light", "heavy"):
                if n[k] not in OVERHEAD and n[k] not in new_o:
                    raise ValueError(f"need {name!r} names unknown agent {n[k]!r}")
            new_r[name] = {k: n[k] for k in NEED_KEYS}
    except (OSError, ValueError) as e:  # JSONDecodeError is a ValueError
        sys.stderr.write(f"warning: ignoring profiles file {path}: {e}\n")
        return
    OVERHEAD.update(new_o)
    MODEL.update(new_m)
    ROUTED.update(new_r)
    NEEDS.update(new_r)


_path = profiles_path()
if _path:
    load_profiles(_path)


def pick(does, needs, payload):
    base = DOES[does]
    routed = sorted(needs & set(ROUTED))
    if routed:
        r = ROUTED[routed[0]]
        side = "light" if base in ("runner", "scout") else "heavy"
        agent, reason = r[side], r[side + "_reason"]
        model = MODEL[agent]
    elif needs & {"web", "mcp", "skills", "notebook"}:
        agent = "general-purpose"
        model = TIER_MODEL[base]
        reason = (f"needs {', '.join(sorted(needs & {'web', 'mcp', 'skills', 'notebook'}))}, "
                  f"which no lean profile carries; pass model={model} (the {base} tier)")
    elif "house-rules" in needs and base != "architect":
        agent = "general-purpose"
        model = TIER_MODEL[base]
        reason = (f"needs the full CLAUDE.md; lean profiles omit it. Prefer quoting the one rule "
                  f"into a {base} brief; else general-purpose with model={model}")
    else:
        agent, model = base, MODEL[base]
        reason = f"'{does}' fits {agent}: smallest profile with the tools it needs"
    fixed = OVERHEAD[agent]
    verdict = None
    if payload is not None:
        verdict = "delegate" if payload > fixed else "inline"
    return dict(agent=agent, model=model, fixed_tokens=fixed, reason=reason, verdict=verdict)


def main():
    ap = argparse.ArgumentParser(description="Pick the leanest subagent profile for a unit of work")
    ap.add_argument("--does", required=True, choices=sorted(DOES))
    ap.add_argument("--needs", default="", help="comma list: " + ", ".join(sorted(NEEDS)))
    ap.add_argument("--payload-tokens", type=int)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    needs = {n.strip() for n in a.needs.split(",") if n.strip()}
    bad = needs - NEEDS
    if bad:
        sys.stderr.write(f"error: unknown --needs {', '.join(sorted(bad))} "
                         f"(known: {', '.join(sorted(NEEDS))})\n")
        return 2
    if a.payload_tokens is not None and a.payload_tokens < 0:
        sys.stderr.write("error: --payload-tokens must be >= 0\n")
        return 2

    r = pick(a.does, needs, a.payload_tokens)
    if a.json:
        print(json.dumps(r))
    else:
        v = f"  VERDICT: {r['verdict']}" if r["verdict"] else ""
        print(f"AGENT: {r['agent']}  model={r['model']}  fixed~{r['fixed_tokens']} tok  "
              f"-- {r['reason']}{v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
