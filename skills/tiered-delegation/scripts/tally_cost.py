#!/usr/bin/env python3
"""Tally actual token usage and cost for recent agent sessions, and compare arms.

Deterministic, harness agnostic at the core, read-only. Reads whatever a harness records
about token/cost per session and prints a compact table + totals, so the "tally estimated
vs actual token/cost" step is a script, not LLM re-derivation.

The core is source-agnostic: it consumes a list of session records
  {id, title, agent, model, cost, tokens_input, tokens_output, tokens_cache_read}
and does the arithmetic + table + optional arm comparison. Each harness supplies those
records through a small adapter. One adapter ships here (opencode SQLite); add others the
same way (see ADAPTERS below). If a harness exposes no usage store, feed records via
--records-json and the core still runs.

Usage:
  tally_cost.py --recent 15                          # opencode: 15 newest sessions, table+totals
  tally_cost.py --ids ses_a ses_b ses_c              # sum specific sessions (e.g. one arm)
  tally_cost.py --delegated ses_x ses_y --inline ses_z --json   # compare two arms
  tally_cost.py --records-json records.json          # harness-neutral: bring your own records
  tally_cost.py --recent 10 --db /path/to/opencode.db

Options:
  --source opencode        Adapter to load records from (default: opencode).
  --db PATH                opencode adapter: path to opencode.db
                           (default: ~/.local/share/opencode/opencode.db).
  --recent N               List the N newest sessions.
  --ids ID [ID ...]        Restrict/sum to these session ids.
  --delegated ID [ID ...]  Session ids that made up the delegated arm of a comparison.
  --inline ID [ID ...]     Session ids (or the same session) for the inline arm.
  --records-json PATH      Read records from a JSON array instead of an adapter.
  --projects-dir PATH      claude-code adapter: transcripts root (default ~/.claude/projects).
  --project SUBSTR         claude-code adapter: only project dirs whose name contains SUBSTR.
  --prices-json PATH       claude-code adapter: override {"model": [in, out, cache_read]} $/1M.
  --arms-auto              claude-code: compare sessions that spawned subagents vs not
                           (per-prompt medians, subagent share, cold-start overhead).
  --agent-overhead         claude-code: median fixed context (first request minus brief)
                           per subagent type; narrow with --project / --ids SESSION.
  --lean-projection        claude-code: re-price every past subagent as if it ran on the lean
                           profile for its model (A), and also re-tiered opus->sonnet (B).
  --json                  Emit a JSON object instead of the human table.

Exit codes:
  0  a tally (and/or comparison) was produced
  2  bad input, missing db/adapter, or no records found

Output contract:
  stdout  a fixed-width table + a totals line, or with --json an object:
          {"sessions":[...], "totals":{...}, "comparison":{...}|null}
          Token fields are new (billable) tokens = input+output; cache_read reported separately.
  stderr  progress / diagnostics only

ADAPTERS (harness-agnostic extension point):
  opencode  -> reads table `session` (cols: id,title,agent,model,cost,tokens_input,
               tokens_output,tokens_cache_read,time_created) from a SQLite db.
  claude-code -> reads ~/.claude/projects/*/<session>.jsonl + <session>/subagents/*.jsonl,
               prices per-message usage (cache writes/reads priced separately); agent is
               "delegated" or "inline". Example:
               tally_cost.py --source claude-code --arms-auto --recent 100
  To add a harness: write load_<name>(args) -> list[dict] with the record shape above and
  register it in SOURCES. The rest of the script is identical across harnesses.
"""

import argparse
import json
import os
import sqlite3
import sys

REC_KEYS = ("id", "title", "agent", "model", "cost",
            "tokens_input", "tokens_output", "tokens_cache_read")


def load_opencode(args):
    db = args.db or os.path.expanduser("~/.local/share/opencode/opencode.db")
    if not os.path.exists(db):
        sys.stderr.write(f"error: opencode db not found: {db}\n")
        sys.exit(2)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cur = con.cursor()
    cols = "id,title,agent,model,cost,tokens_input,tokens_output,tokens_cache_read,time_created"
    if args.ids:
        q = f"SELECT {cols} FROM session WHERE id IN (%s)" % ",".join("?" * len(args.ids))
        rows = cur.execute(q, args.ids).fetchall()
    else:
        n = args.recent or 15
        rows = cur.execute(
            f"SELECT {cols} FROM session ORDER BY time_created DESC LIMIT ?", [n]
        ).fetchall()
    recs = []
    for r in rows:
        model = r[3]
        try:
            model = json.loads(model).get("id", model)
        except (TypeError, ValueError):
            pass
        recs.append(dict(id=r[0], title=(r[1] or "")[:38], agent=r[2], model=model,
                         cost=r[4] or 0, tokens_input=r[5] or 0, tokens_output=r[6] or 0,
                         tokens_cache_read=r[7] or 0))
    return recs


def load_records_json(args):
    with open(args.records_json) as f:
        data = json.load(f)
    return [{k: rec.get(k) for k in REC_KEYS} for rec in data]


# USD per 1M tokens: (input, output, cache_read). Cache writes bill at 1.25x input (5m TTL)
# or 2x input (1h TTL). Public list prices, Anthropic first-party API, cached 2026-10-06;
# override with --prices-json {"model-id": [in, out, cache_read], ...}. Unknown models cost 0
# and are listed on stderr.
CLAUDE_PRICES = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-mythos-5-1": (10.0, 50.0, 0.25),
    "claude-fable-5": (10.0, 50.0, 1.0),
    "claude-mythos-5": (10.0, 50.0, 1.0),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 0.50),
    "claude-opus-4-7": (5.0, 25.0, 0.50),
    "claude-opus-4-6": (5.0, 25.0, 0.50),
    "claude-opus-4": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30),
    "claude-sonnet-4": (3.0, 15.0, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
}


def _price(model, prices, unknown):
    m = (model or "").split("[")[0]
    for key in sorted(prices, key=len, reverse=True):  # longest prefix wins (5-5 before 5)
        if m.startswith(key):
            return prices[key]
    unknown.add(m)
    return (0.0, 0.0, 0.0)


def _read_claude_jsonl(path, prices, unknown):
    """One transcript -> usage totals. Assistant lines repeat per content block with the
    same message id, so usage is deduped by id (last line wins)."""
    by_msg, prompts, title, first_prompt = {}, 0, "", ""
    with open(path, errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = d.get("type")
            if t == "ai-title":
                title = d.get("aiTitle") or d.get("title") or title
            elif t == "user" and not d.get("isMeta") and not d.get("isSidechain"):
                c = (d.get("message") or {}).get("content")
                kinds = [x.get("type") for x in c] if isinstance(c, list) else ["text"]
                if "tool_result" not in kinds:
                    prompts += 1
                    if not first_prompt:
                        txt = c if isinstance(c, str) else " ".join(
                            x.get("text", "") for x in c if x.get("type") == "text")
                        first_prompt = " ".join(txt.split())
            elif t == "assistant":
                m = d.get("message") or {}
                if m.get("usage") and m.get("model") != "<synthetic>":
                    by_msg[m.get("id") or id(d)] = (m.get("model"), m["usage"])
    tot = dict(cost=0.0, inp=0, out=0, cread=0, cwrite=0, first_in=None, models={})
    for model, u in by_msg.values():
        p_in, p_out, p_read = _price(model, prices, unknown)
        cc = u.get("cache_creation") or {}
        w1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
        w5m = cc.get("ephemeral_5m_input_tokens", 0) or 0
        wall = u.get("cache_creation_input_tokens", 0) or 0
        if wall and not (w1h or w5m):
            w5m = wall
        inp, out = u.get("input_tokens", 0) or 0, u.get("output_tokens", 0) or 0
        cread = u.get("cache_read_input_tokens", 0) or 0
        cost = (inp * p_in + w5m * p_in * 1.25 + w1h * p_in * 2 + cread * p_read
                + out * p_out) / 1e6
        if tot["first_in"] is None:
            tot["first_in"] = inp + wall + cread
        tot["cost"] += cost
        tot["inp"] += inp + wall
        tot["out"] += out
        tot["cread"] += cread
        tot["cwrite"] += wall
        tot["models"][model] = tot["models"].get(model, 0) + cost
    tot["prompts"], tot["title"] = prompts, title or first_prompt
    return tot


def load_claude_code(args):
    """Claude Code transcripts: <projects>/<proj>/<session>.jsonl, subagents under
    <proj>/<session>/subagents/agent-*.jsonl. One record per driver session, subagent usage
    rolled in and also broken out (sub_cost, sub_count, sub_first_in)."""
    root = os.path.expanduser(args.projects_dir or "~/.claude/projects")
    if not os.path.isdir(root):
        sys.stderr.write(f"error: projects dir not found: {root}\n")
        sys.exit(2)
    prices = dict(CLAUDE_PRICES)
    if args.prices_json:
        with open(args.prices_json) as f:
            prices.update({k: tuple(v) for k, v in json.load(f).items()})
    sessions = []
    for proj in sorted(os.listdir(root)):
        if args.project and args.project not in proj:
            continue
        pdir = os.path.join(root, proj)
        if not os.path.isdir(pdir):
            continue
        for name in os.listdir(pdir):
            if name.endswith(".jsonl"):
                sid = name[:-6]
                if args.ids and sid not in args.ids:
                    continue
                path = os.path.join(pdir, name)
                sessions.append((os.path.getmtime(path), sid, path, os.path.join(pdir, sid)))
    sessions.sort(reverse=True)
    if args.recent and not args.ids:
        sessions = sessions[:args.recent]
    unknown, recs = set(), []
    for _, sid, path, sdir in sessions:
        main = _read_claude_jsonl(path, prices, unknown)
        subs = []
        subdir = os.path.join(sdir, "subagents")
        if os.path.isdir(subdir):
            subs = [_read_claude_jsonl(os.path.join(subdir, n), prices, unknown)
                    for n in sorted(os.listdir(subdir)) if n.endswith(".jsonl")]
        if not main["prompts"] and not main["cost"]:
            continue
        models = dict(main["models"])
        for s in subs:
            for m, c in s["models"].items():
                models[m] = models.get(m, 0) + c
        firsts = sorted(s["first_in"] for s in subs if s["first_in"] is not None)
        sub_cost = sum(s["cost"] for s in subs)
        recs.append(dict(
            id=sid, title=main["title"][:38],
            agent="delegated" if subs else "inline",
            model=max(models, key=models.get) if models else "",
            cost=round(main["cost"] + sub_cost, 4),
            tokens_input=main["inp"] + sum(s["inp"] for s in subs),
            tokens_output=main["out"] + sum(s["out"] for s in subs),
            tokens_cache_read=main["cread"] + sum(s["cread"] for s in subs),
            prompts=main["prompts"], sub_count=len(subs), sub_cost=round(sub_cost, 4),
            sub_first_in=firsts[len(firsts) // 2] if firsts else None,
            sub_models=sorted({m for s in subs for m in s["models"]}),
        ))
    if unknown:
        sys.stderr.write("warning: no price for models (costed at 0): "
                         + ", ".join(sorted(unknown)) + "\n")
    return recs


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return 0
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def arms_auto(recs):
    """Split claude-code records into sessions that spawned subagents vs ones that did not.
    Sums are meaningless across different workloads, so this reports per-prompt medians,
    the subagent share of delegated spend, and the median cold-start input of a subagent
    (its first request's full prompt = the per-agent overhead). Observational, not a
    controlled comparison: the two arms ran different tasks."""
    out = {}
    for arm in ("delegated", "inline"):
        rs = [r for r in recs if r.get("agent") == arm]
        per_prompt = [r["cost"] / r["prompts"] for r in rs if r.get("prompts")]
        out[arm] = dict(
            sessions=len(rs), cost=round(sum(r["cost"] for r in rs), 2),
            median_cost_per_session=round(_median([r["cost"] for r in rs]), 3),
            median_cost_per_prompt=round(_median(per_prompt), 3),
        )
    d = [r for r in recs if r.get("agent") == "delegated"]
    subs_total = sum(r["sub_cost"] for r in d)
    out["subagents"] = dict(
        count=sum(r["sub_count"] for r in d),
        cost=round(subs_total, 2),
        share_of_delegated_spend=round(subs_total / out["delegated"]["cost"], 3)
        if out["delegated"]["cost"] else 0,
        mean_cost_each=round(subs_total / max(1, sum(r["sub_count"] for r in d)), 3),
        median_cold_start_input_tokens=_median(
            [r["sub_first_in"] for r in d if r.get("sub_first_in")]),
        models=sorted({m for r in d for m in r["sub_models"]}),
    )
    return out


def agent_overhead(args):
    """Claude Code: per subagent type, the fixed context a subagent loads before its brief.
    = full input of its first request (fresh + cache write + cache read) minus the brief
    (~4 chars/token). Reads <session>/subagents/agent-*.jsonl + .meta.json (agentType)."""
    import glob
    root = os.path.expanduser(args.projects_dir or "~/.claude/projects")
    pattern = os.path.join(root, f"*{args.project or ''}*", args.ids[0] if args.ids and
                           len(args.ids) == 1 else "*", "subagents", "agent-*.jsonl")
    rows = {}
    for path in glob.glob(pattern):
        if args.ids and len(args.ids) > 1 and path.split(os.sep)[-3] not in args.ids:
            continue
        try:
            with open(path[:-6] + ".meta.json") as f:
                typ = json.load(f).get("agentType") or "?"
        except (OSError, ValueError):
            typ = "?"
        brief, first = None, None
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "user" and brief is None:
                    c = (d.get("message") or {}).get("content")
                    brief = len(c if isinstance(c, str) else json.dumps(c)) // 4
                m = d.get("message") or {}
                if d.get("type") == "assistant" and m.get("usage"):
                    u = m["usage"]
                    first = ((u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
                             + (u.get("cache_read_input_tokens") or 0), m.get("model"))
                    break
        if first:
            rows.setdefault(typ, []).append((first[0] - (brief or 0), first[1]))
    return {t: dict(n=len(r), fixed_median=round(_median([x for x, _ in r])),
                    models=sorted({m for _, m in r}))
            for t, r in sorted(rows.items(), key=lambda kv: _median([x for x, _ in kv[1]]))}


# Lean-profile fixed context by the model a historical subagent ran on (measured 2026-09-30):
# haiku -> scout, sonnet -> workhorse, opus/fable/mythos -> architect (keeps CLAUDE.md, the largest).
LEAN_FIXED = {"haiku": 5522, "sonnet": 8174, "opus": 16833, "fable": 16833, "mythos": 16833}
LEAN_TYPES = {"general-purpose", "Explore", "Plan", "claude"}  # types a lean profile replaces


def lean_projection(args):
    """Claude Code: re-price every historical subagent as if it had run on the lean profile for
    its model. Each request's prefix shrinks by (observed fixed context - lean fixed context):
    priced at that request's blended input rate on the first request, at the cache-read rate on
    later ones (assumes cache hits: conservative). Scenario B also re-prices opus/fable subagents
    at sonnet rates (the tier rule). Agent types a lean profile does not replace are unchanged."""
    import glob
    root = os.path.expanduser(args.projects_dir or "~/.claude/projects")
    prices = dict(CLAUDE_PRICES)
    unknown = set()
    sonnet = prices["claude-sonnet-5"]
    tot = dict(subagents=0, replaced=0, actual=0.0, lean=0.0, lean_retier=0.0)
    for path in glob.glob(os.path.join(root, f"*{args.project or ''}*", "*", "subagents",
                                       "agent-*.jsonl")):
        try:
            with open(path[:-6] + ".meta.json") as f:
                typ = json.load(f).get("agentType") or "?"
        except (OSError, ValueError):
            typ = "?"
        brief, by_msg = None, {}
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "user" and brief is None:
                    c = (d.get("message") or {}).get("content")
                    brief = len(c if isinstance(c, str) else json.dumps(c)) // 4
                m = d.get("message") or {}
                # Same dedupe as _read_claude_jsonl: last line per message id carries the final
                # usage (earlier lines of a streamed message hold partial output counts).
                if (d.get("type") == "assistant" and m.get("usage")
                        and m.get("model") != "<synthetic>"):
                    by_msg[m.get("id") or id(d)] = (m.get("model"), m["usage"])
        reqs = list(by_msg.values())
        if not reqs:
            continue
        tot["subagents"] += 1
        u0 = reqs[0][1]
        fixed_obs = sum(u0.get(k, 0) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                    "cache_read_input_tokens")) - (brief or 0)
        model = reqs[0][0] or ""
        fam = next((k for k in LEAN_FIXED if k in model), None)
        delta = max(0, fixed_obs - LEAN_FIXED[fam]) if (fam and typ in LEAN_TYPES) else 0
        if delta:
            tot["replaced"] += 1
        for i, (mod, u) in enumerate(reqs):
            p_in, p_out, p_read = _price(mod, prices, unknown)
            cc = u.get("cache_creation") or {}
            w1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
            w5m = cc.get("ephemeral_5m_input_tokens", 0) or 0
            wall = u.get("cache_creation_input_tokens", 0) or 0
            if wall and not (w1h or w5m):
                w5m = wall
            inp, out = u.get("input_tokens", 0) or 0, u.get("output_tokens", 0) or 0
            cread = u.get("cache_read_input_tokens", 0) or 0

            def cost(pi, po, pr, sub):
                in_cost = inp * pi + w5m * pi * 1.25 + w1h * pi * 2 + cread * pr
                in_tok = inp + wall + cread
                rate = (in_cost / in_tok) if (i == 0 and in_tok) else pr
                return (in_cost - min(sub, in_tok) * rate + out * po) / 1e6

            tot["actual"] += cost(p_in, p_out, p_read, 0)
            tot["lean"] += cost(p_in, p_out, p_read, delta)
            big = fam in ("opus", "fable", "mythos")
            tot["lean_retier"] += cost(*(sonnet if big else (p_in, p_out, p_read)), delta)
    if unknown:
        sys.stderr.write("warning: no price for models (costed at 0): "
                         + ", ".join(sorted(unknown)) + "\n")
    for k in ("actual", "lean", "lean_retier"):
        tot[k] = round(tot[k], 2)
    return tot


SOURCES = {"opencode": load_opencode, "claude-code": load_claude_code}


def summarize(recs):
    return dict(
        cost=round(sum(r["cost"] for r in recs), 4),
        new_tokens=sum(r["tokens_input"] + r["tokens_output"] for r in recs),
        input=sum(r["tokens_input"] for r in recs),
        output=sum(r["tokens_output"] for r in recs),
        cache_read=sum(r["tokens_cache_read"] for r in recs),
        sessions=len(recs),
    )


def pick(recs, ids):
    idset = set(ids)
    return [r for r in recs if r["id"] in idset]


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.split("\n")[0])
    ap.add_argument("--source", default="opencode", choices=sorted(SOURCES))
    ap.add_argument("--db")
    ap.add_argument("--recent", type=int)
    ap.add_argument("--ids", nargs="+")
    ap.add_argument("--delegated", nargs="+")
    ap.add_argument("--inline", nargs="+")
    ap.add_argument("--records-json")
    ap.add_argument("--projects-dir")
    ap.add_argument("--project")
    ap.add_argument("--prices-json")
    ap.add_argument("--arms-auto", action="store_true")
    ap.add_argument("--agent-overhead", action="store_true")
    ap.add_argument("--lean-projection", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.lean_projection:
        r = lean_projection(args)
        if not r["subagents"]:
            sys.stderr.write("error: no subagent transcripts found\n")
            return 2
        if args.json:
            print(json.dumps(r))
        else:
            a = r["actual"]
            print(f"subagents={r['subagents']} replaceable_by_lean={r['replaced']}")
            print(f"  actual                      ${a}")
            print(f"  A lean profiles, same model ${r['lean']}  (-{1 - r['lean'] / a:.0%})")
            print(f"  B lean + opus->sonnet       ${r['lean_retier']}  "
                  f"(-{1 - r['lean_retier'] / a:.0%})")
        return 0
    if args.agent_overhead:
        res = agent_overhead(args)
        if not res:
            sys.stderr.write("error: no subagent transcripts found\n")
            return 2
        if args.json:
            print(json.dumps(res))
        else:
            print(f"{'agent type':<24} {'n':>4} {'fixed_tok':>10}  models")
            for t, r in res.items():
                print(f"{t[:24]:<24} {r['n']:>4} {r['fixed_median']:>10}  {', '.join(r['models'])}")
        return 0

    # For an arm comparison we need both arms' ids available in the record pull.
    if (args.delegated or args.inline) and not args.ids:
        args.ids = list(dict.fromkeys((args.delegated or []) + (args.inline or [])))

    if args.records_json:
        recs = load_records_json(args)
    else:
        recs = SOURCES[args.source](args)

    if not recs:
        sys.stderr.write("error: no session records found\n")
        sys.exit(2)

    totals = summarize(recs)
    comparison = None
    if args.delegated and args.inline:
        d = summarize(pick(recs, args.delegated))
        i = summarize(pick(recs, args.inline))
        cheaper = "delegate" if d["cost"] < i["cost"] else "inline"
        comparison = {
            "delegated": d, "inline": i, "cheaper": cheaper,
            "cost_delta": round(i["cost"] - d["cost"], 4),
            "new_token_delta": i["new_tokens"] - d["new_tokens"],
        }
    if args.arms_auto:
        comparison = arms_auto(recs)

    if args.json:
        print(json.dumps({"sessions": recs, "totals": totals, "comparison": comparison}))
        return 0

    print(f"{'cost$':>8}  {'new_tok':>9}  {'cache_r':>9}  {'model':<26}  title")
    print("-" * 90)
    for r in recs:
        print(f"{r['cost']:>8.4f}  {r['tokens_input']+r['tokens_output']:>9}  "
              f"{r['tokens_cache_read']:>9}  {str(r['model'])[:26]:<26}  {r['title']}")
    print("-" * 90)
    print(f"TOTAL  ${totals['cost']:.4f}  new_tokens={totals['new_tokens']}  "
          f"cache_read={totals['cache_read']}  sessions={totals['sessions']}")
    if comparison and args.arms_auto:
        print()
        print("AUTO ARMS (observational: the arms ran different tasks)")
        for arm in ("delegated", "inline"):
            a = comparison[arm]
            print(f"  {arm:<9}  sessions={a['sessions']:<4} total=${a['cost']:<9} "
                  f"median/session=${a['median_cost_per_session']:<7} "
                  f"median/prompt=${a['median_cost_per_prompt']}")
        s = comparison["subagents"]
        print(f"  subagents  count={s['count']} cost=${s['cost']} "
              f"share_of_delegated={s['share_of_delegated_spend']:.0%} "
              f"mean_each=${s['mean_cost_each']} "
              f"median_cold_start_in={s['median_cold_start_input_tokens']} tok")
        print(f"             models={', '.join(s['models'])}")
    elif comparison:
        d, i = comparison["delegated"], comparison["inline"]
        print()
        print(f"ARM COMPARISON (cheaper: {comparison['cheaper']})")
        print(f"  delegated: ${d['cost']:.4f}  new_tokens={d['new_tokens']}")
        print(f"  inline:    ${i['cost']:.4f}  new_tokens={i['new_tokens']}")
        print(f"  delta:     ${comparison['cost_delta']:.4f}  "
              f"new_tokens={comparison['new_token_delta']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
