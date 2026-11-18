#!/usr/bin/env python3
"""Estimate inline-vs-delegate token cost for a task and emit a yes/no routing verdict.

Deterministic, harness agnostic, read-only. No LLM, no network. Given the files/globs a
task would touch (or a raw byte/token count), it estimates how many tokens land in the
orchestrator context if done INLINE (all payload read into the driving model) versus
DELEGATED (payload absorbed by N subagent contexts, only compact summaries returned),
then prints a routing verdict against a configurable overhead floor.

This scripts the "determine task fast: inline vs delegate" step so a model does not
re-derive it every prompt. Feed it the concrete targets; it does the arithmetic.

Usage:
  estimate_delegation.py --paths FILE_OR_DIR [FILE_OR_DIR ...]   # measure real bytes
  estimate_delegation.py --bytes N                               # payload size directly
  estimate_delegation.py --tokens N                              # payload tokens directly
  estimate_delegation.py --paths src/ --shards 5 --json          # machine-readable

Options:
  --paths P [P ...]   Files/dirs the task would read. Dirs are walked; use --ext to filter.
  --ext .cs .py       Extensions to include when walking dirs (default: all files).
  --bytes N           Payload size in bytes (alternative to --paths).
  --tokens N          Payload size in tokens (alternative to --paths/--bytes).
  --shards N          How many parallel subagents delegation would use (default: auto by size).
  --bytes-per-token F Approx bytes/token for the corpus (default 3.7; code ~3.5, prose ~4).
  --summary-tokens N  Tokens each subagent returns to the orchestrator (default 400).
  --brief-tokens N    Tokens of brief the orchestrator sends per subagent (default 250).
  --overhead-tokens N Fixed per-subagent context overhead: system prompt + tool schemas
                      (default 6000). Below this floor, delegation cannot win -> run inline.
  --json              Emit a JSON object instead of the human summary.

Exit codes:
  0  a verdict was produced ("delegate" or "inline")
  2  bad input (no payload given, missing path, bad number)

Output contract:
  stdout  human line "VERDICT: delegate|inline  (orchestrator inline=<t> delegated=<t> ...)"
          or, with --json, an object:
          {"verdict":"delegate|inline","payload_tokens":int,"shards":int,
           "orchestrator_inline_tokens":int,"orchestrator_delegated_tokens":int,
           "ratio":float,"reason":str}
  stderr  progress / diagnostics only

Method (transparent, no model calls):
  payload_tokens              = bytes / bytes_per_token
  orchestrator_inline_tokens  = payload_tokens            (all payload hits the driver)
  orchestrator_delegated      = shards*(brief+summary)    (only briefs+summaries hit driver)
  Delegation is worth it when the payload is big enough that the orchestrator saves
  context AND each shard's slice exceeds the overhead floor. Tiny tasks fall back to
  inline (the inline floor of the tiered-delegation rules).
"""

import argparse
import json
import os
import sys


def walk_bytes(paths, exts):
    total = 0
    files = 0
    for p in paths:
        if not os.path.exists(p):
            sys.stderr.write(f"error: path not found: {p}\n")
            sys.exit(2)
        if os.path.isfile(p):
            total += os.path.getsize(p)
            files += 1
            continue
        for root, _dirs, names in os.walk(p):
            for n in names:
                if n.startswith("._") or n == ".DS_Store":
                    continue  # macOS sidecars; on exFAT they shadow every real file
                if exts and not any(n.endswith(e) for e in exts):
                    continue
                fp = os.path.join(root, n)
                try:
                    total += os.path.getsize(fp)
                    files += 1
                except OSError:
                    pass
    return total, files


def auto_shards(payload_tokens):
    # One shard per ~50k-token slice, clamped to a sane 1..8 fan-out.
    if payload_tokens < 20000:
        return 1
    return max(2, min(8, round(payload_tokens / 50000) or 2))


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.split("\n")[0])
    ap.add_argument("--paths", nargs="+")
    ap.add_argument("--ext", nargs="*", default=[])
    ap.add_argument("--bytes", type=int, dest="raw_bytes")
    ap.add_argument("--tokens", type=int, dest="raw_tokens")
    ap.add_argument("--shards", type=int)
    ap.add_argument("--bytes-per-token", type=float, default=3.7)
    ap.add_argument("--summary-tokens", type=int, default=400)
    ap.add_argument("--brief-tokens", type=int, default=250)
    ap.add_argument("--overhead-tokens", type=int, default=6000)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    files = None
    if args.raw_tokens is not None:
        payload_tokens = args.raw_tokens
    elif args.raw_bytes is not None:
        payload_tokens = int(args.raw_bytes / args.bytes_per_token)
    elif args.paths:
        b, files = walk_bytes(args.paths, args.ext)
        payload_tokens = int(b / args.bytes_per_token)
    else:
        sys.stderr.write("error: give one of --paths, --bytes, --tokens\n")
        sys.exit(2)

    shards = args.shards or auto_shards(payload_tokens)
    inline = payload_tokens
    delegated = shards * (args.brief_tokens + args.summary_tokens)
    per_shard_slice = payload_tokens / shards if shards else payload_tokens
    ratio = (inline / delegated) if delegated else float("inf")

    # Inline floor: if each shard's slice does not clear the overhead, or the payload is
    # trivial, delegation cannot pay for itself -> inline.
    if payload_tokens < args.overhead_tokens or per_shard_slice < args.overhead_tokens:
        verdict = "inline"
        reason = (
            f"payload {payload_tokens} tok / per-shard {int(per_shard_slice)} tok is below the "
            f"{args.overhead_tokens}-tok overhead floor; subagent overhead would exceed the work"
        )
    else:
        verdict = "delegate"
        reason = (
            f"payload {payload_tokens} tok over {shards} shards keeps ~{inline - delegated} tok "
            f"of raw content out of the orchestrator context ({ratio:.1f}x less driver context)"
        )

    out = {
        "verdict": verdict,
        "payload_tokens": payload_tokens,
        "files": files,
        "shards": shards,
        "orchestrator_inline_tokens": inline,
        "orchestrator_delegated_tokens": delegated,
        "ratio": round(ratio, 2) if delegated else None,
        "reason": reason,
    }

    if args.json:
        print(json.dumps(out))
    else:
        print(
            f"VERDICT: {verdict}  "
            f"(orchestrator inline={inline} tok, delegated={delegated} tok over {shards} shards; "
            f"{ratio:.1f}x) -- {reason}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
