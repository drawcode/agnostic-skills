#!/usr/bin/env python3
"""Pick the model tier for a subagent from what its brief asks for

Deterministic, read-only. For any spawn that is not already a pinned lean profile (a
general-purpose agent, a workflow agent() call, a harness without profiles), this reads the
brief, classifies it as sprinter / workhorse / architect by its verbs, and prints the model to
pass. Cheapest tier that fits: sprinter where the work is pure mechanics, workhorse by default,
architect only when the brief asks for scoping, root cause or a decision.

Models are named by family alias (haiku / sonnet / opus), never a dated id: the harness resolves
an alias to the newest model in that family, so the pick stays on the latest model without edits.
On a non-Claude harness, --harness any prints the role (small / mid / strongest) instead.

Signals are matched after negated clauses are dropped ("do not refactor" is not a refactor).
A judgment signal anywhere keeps the work off the sprinter tier: the small tier loses real
quality on ranking and summary fields (tiered-delegation A/B benchmark).

Usage:
  pick_model.py --task "grep every caller of Foo and count them"
  pick_model.py --task-file brief.md --needs web
  pick_model.py --does extract                  # skip classification, kind already known
  echo "$BRIEF" | pick_model.py --task -
  pick_model.py --task "..." --harness any --json

Options:
  --task TEXT        the brief, or - to read it from stdin
  --task-file PATH   the brief from a file
  --does KIND        known kind instead of a brief (the pick_agent.py kinds)
  --needs LIST       passed through to pick_agent.py (see pick_agent.py for the needs)
  --harness NAME     claude (default): family alias; any: role name
  --json             machine-readable output

Output contract:
  stdout  one line: model, tier, does, agent, signals, or with --json
          {"model","tier","does","agent","fixed_tokens","signals","reason"}
  agent is the leanest profile from pick_agent.py; when it is a pinned profile its own model
  applies, otherwise pass model explicitly on the spawn.

Exit codes:
  0  ok
  1  the check/operation failed
  2  bad input
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pick_agent  # noqa: E402

TIER_OF = {"run": "sprinter", "extract": "sprinter", "edit": "sprinter",
           "implement": "workhorse", "review": "workhorse", "summarize": "workhorse",
           "plan": "architect", "diagnose": "architect"}
ALIAS = {"sprinter": "haiku", "workhorse": "sonnet", "architect": "opus"}
ROLE = {"sprinter": "small", "workhorse": "mid", "architect": "strongest"}

# First match wins, top to bottom: a higher tier signal outranks any lower one.
SIGNALS = [
    ("diagnose", r"root[- ]?cause|diagnos\w*|investigat\w*|why (?:does|do|is|did|are)\b|"
                 r"what went wrong|regression hunt|bisect\w*"),
    ("plan", r"architect\w*|design (?:a|an|the)\b|trade-?offs?|decid\w*|ambiguous|"
             r"threat model|security review|plan (?:the|a|an)\b|approach for"),
    ("review", r"review\w*|audit\w*|critique|assess\w*|evaluat\w*|verify the (?:fix|change|design)"),
    ("summarize", r"summar\w*|rank\w*|prioriti[sz]\w*|most important|recommend\w*|"
                  r"compare|explain\w*|analy[sz]\w*|pick the best|which is better"),
    ("implement", r"implement\w*|refactor\w*|fix(?:es|ed|ing)?\b|debug\w*|add (?:a|an|the) feature|"
                  r"write (?:a|an|the) (?:test|doc|pr|description|function|class)\w*|draft\w*|"
                  r"translat\w*|port\b|migrat\w*"),
    ("edit", r"renam\w*|replac\w*|reformat\w*|format\w*|convert\w*|bump\w*|"
             r"create (?:the |a |an )?(?:file|folder|director\w*)|move\b|copy\b|fill in|"
             r"sort\b|dedup\w*|delete the line"),
    ("run", r"run (?:the |this )?(?:script|command|tests?|build)|execute\w*|re-?run\w*"),
    ("extract", r"grep\w*|find (?:all|every|each)|list (?:all|every|each|the)|count\w*|"
                r"inventor\w*|extract\w*|scrap\w*|collect\w*|locate\w*|enumerat\w*|"
                r"which files|search for|look ?up|read back|dump\w*|stat\b|wc\b"),
]
SIGNAL_RE = [(k, re.compile(r"\b(?:" + p + r")", re.I)) for k, p in SIGNALS]
# A negation covers its own clause only: it stops at punctuation or a joined instruction, so
# "do not refactor, then rank them" still ranks. "without" is a filter, not a negation.
NEGATION_RE = re.compile(r"\b(?:do not|don't|never|no need to|must not|avoid)\b"
                         r"(?:(?!\b(?:and|then|but)\b)[^.;:,\n])*", re.I)


def classify(text):
    """Return (does, signals) for a brief; no signal at all is workhorse work."""
    text = NEGATION_RE.sub(" ", text)
    hits = {}
    for kind, rx in SIGNAL_RE:
        found = sorted({m.group(0).lower() for m in rx.finditer(text)})
        if found:
            hits[kind] = found
    for kind, _ in SIGNAL_RE:
        if kind in hits:
            return kind, hits
    return "implement", hits


def pick(does, signals, needs, harness, reason):
    tier = TIER_OF[does]
    prof = pick_agent.pick(does, needs, None)
    model = ALIAS[tier] if harness == "claude" else ROLE[tier]
    flat = sorted({s for v in signals.values() for s in v})
    return dict(model=model, tier=tier, does=does, agent=prof["agent"],
                fixed_tokens=prof["fixed_tokens"], signals=flat, reason=reason)


def main():
    ap = argparse.ArgumentParser(description="Pick the model tier for a subagent from its brief")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--task", help="the brief, or - for stdin")
    src.add_argument("--task-file", type=Path)
    src.add_argument("--does", choices=sorted(TIER_OF))
    ap.add_argument("--needs", default="",
                    help="comma list: " + ", ".join(sorted(pick_agent.NEEDS)))
    ap.add_argument("--harness", default="claude", choices=["claude", "any"])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    needs = {n.strip() for n in a.needs.split(",") if n.strip()}
    bad = needs - pick_agent.NEEDS
    if bad:
        sys.stderr.write(f"error: unknown --needs {', '.join(sorted(bad))} "
                         f"(known: {', '.join(sorted(pick_agent.NEEDS))})\n")
        return 2

    if a.does:
        does, signals, reason = a.does, {}, "kind given"
    else:
        if a.task_file:
            try:
                text = a.task_file.read_text(encoding="utf-8")
            except OSError as e:
                sys.stderr.write(f"error: cannot read {a.task_file}: {e}\n")
                return 2
        else:
            text = sys.stdin.read() if a.task == "-" else a.task
        if not text.strip():
            sys.stderr.write("error: empty brief\n")
            return 2
        does, signals = classify(text)
        reason = (f"'{does}' outranks the other signals" if signals
                  else "no signal: workhorse default")

    r = pick(does, signals, needs, a.harness, reason)
    if a.json:
        print(json.dumps(r))
    else:
        sig = f"  signals: {', '.join(r['signals'])}" if r["signals"] else ""
        print(f"MODEL: {r['model']}  tier={r['tier']}  does={r['does']}  agent={r['agent']}  "
              f"fixed~{r['fixed_tokens']} tok  -- {r['reason']}{sig}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
