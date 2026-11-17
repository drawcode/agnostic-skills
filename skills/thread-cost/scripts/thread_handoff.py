#!/usr/bin/env python3
"""Record a next-iter handoff and resume from it after /clear or /compact.

  set <path>   store {path, cwd, time, consumed:false} for this cwd (the capture step runs this; `--cwd` may sit anywhere)
  hook         SessionStart hook: stdin JSON has `source` (startup|resume|clear|compact). Only for
               clear or compact, when a pointer exists for the same cwd, is under --max-age-hours
               (default 6) old and not consumed, prints additionalContext telling the new session
               to read the handoff first, and marks it consumed. Otherwise prints nothing.
  show         print the stored pointers
  clear        delete the pointer for this cwd (--all for every cwd)
  --selftest   run the built-in checks

Pointers live under thread_hooks_common.state_dir("handoff") (THREAD_HOOKS_STATE_DIR overrides
the state root). Part of the thread-cost skill.
hook and set swallow every error and exit 0 so a hook can never break a session.
Output: set prints the pointer file path; show prints one JSON object per line (--json is
accepted and is the only format); hook prints a hook JSON object or nothing.
Exit codes: 0 always (hook and set swallow errors), 1 for a failed --selftest, 2 never.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import thread_hooks_common as common  # noqa: E402

def state_dir():
    return common.state_dir("handoff")

MAX_AGE_HOURS = 6.0


def pointer_path(cwd, base=None):
    key = hashlib.sha1(os.path.realpath(cwd).encode()).hexdigest()[:16]
    return os.path.join(base or state_dir(), f"{key}.json")


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def set_pointer(doc, cwd, base=None, now=None):
    path = pointer_path(cwd, base)
    write(path, {"path": os.path.abspath(doc), "cwd": os.path.realpath(cwd),
                 "time": now if now is not None else time.time(), "consumed": False})
    return path


def hook_output(payload, cwd, base=None, now=None, max_age_hours=MAX_AGE_HOURS):
    if payload.get("source") not in ("clear", "compact"):
        return None
    path = pointer_path(payload.get("cwd") or cwd, base)
    ptr = read(path)
    if not ptr or ptr.get("consumed"):
        return None
    if (now if now is not None else time.time()) - float(ptr.get("time", 0)) > max_age_hours * 3600:
        return None
    ptr["consumed"] = True
    write(path, ptr)
    return {"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": f"Continue from the handoff {ptr['path']}: read it first, then resume "
                             "its Next steps. Do not re-derive what it records."}}


def selftest():
    with tempfile.TemporaryDirectory() as d:
        cwd = os.path.join(d, "proj")
        os.makedirs(cwd)
        t0 = 1_000_000.0
        set_pointer("/x/iter-3.md", cwd, d, t0)
        checks = []
        checks.append(("startup ignored", hook_output({"source": "startup"}, cwd, d, t0 + 5) is None))
        checks.append(("other cwd ignored", hook_output({"source": "clear", "cwd": d}, cwd, d, t0 + 5) is None))
        checks.append(("stale ignored", hook_output({"source": "clear"}, cwd, d, t0 + 7 * 3600) is None))
        out = hook_output({"source": "clear"}, cwd, d, t0 + 60)
        ctx = (out or {}).get("hookSpecificOutput", {}).get("additionalContext", "")
        checks.append(("clear fires", "/x/iter-3.md" in ctx))
        checks.append(("consumed once", hook_output({"source": "compact"}, cwd, d, t0 + 61) is None))
        set_pointer("/x/iter-4.md", cwd, d, t0 + 100)
        checks.append(("compact fires after reset", hook_output({"source": "compact"}, cwd, d, t0 + 101) is not None))
    bad = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(("ok   " if ok else "FAIL ") + n)
    return 0 if not bad else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("mode", nargs="?", choices=["set", "hook", "show", "clear"], default="hook")
    parser.add_argument("path", nargs="?")
    parser.add_argument("--json", action="store_true", help="show output is always JSON")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--cwd", default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    parser.add_argument("--max-age-hours", type=float, default=MAX_AGE_HOURS)
    parser.add_argument("--selftest", action="store_true")
    try:
        args = parser.parse_intermixed_args()
    except SystemExit as e:
        return e.code if "--help" in sys.argv or "-h" in sys.argv else 0
    try:
        if args.selftest:
            return selftest()
        if args.mode == "set":
            if not args.path:
                print("set needs the handoff path")
                return 0
            print(set_pointer(args.path, args.cwd))
        elif args.mode == "hook":
            try:
                payload = json.load(sys.stdin)
            except ValueError:
                return 0
            if os.environ.get("CLAUDE_PROJECT_DIR"):
                payload["cwd"] = os.environ["CLAUDE_PROJECT_DIR"]
            out = hook_output(payload, args.cwd, max_age_hours=args.max_age_hours)
            if out:
                print(json.dumps(out))
        elif args.mode == "show":
            base = state_dir()
            for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
                if name.endswith(".json"):
                    print(json.dumps(read(os.path.join(base, name))))
        elif args.mode == "clear":
            base = state_dir()
            targets = ([os.path.join(base, n) for n in os.listdir(base) if n.endswith(".json")]
                       if args.all and os.path.isdir(base) else [pointer_path(args.cwd)])
            for t in targets:
                if os.path.exists(t):
                    os.remove(t)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
