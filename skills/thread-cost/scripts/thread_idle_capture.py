#!/usr/bin/env python3
"""Idle thread: wake it once, before the prompt cache lapses, to capture a handoff and clear.

Part of the thread-cost skill (SKILL.md beside these scripts, or skills/thread-cost/). Two Claude
Code hook entry points:

  mark   UserPromptSubmit: record that the user just spoke (cancels any pending waiter). Hook
         wakes and task notifications also arrive here and are ignored, or the capture would
         re-arm itself and fire every idle period.
  wait   Stop, wired with "asyncRewake": true: sleep until the thread has been idle for
         --idle-min minutes; if the user has not spoken since and this is the newest waiter,
         print the capture instruction to stderr and exit 2, which wakes the model with it
         (the capture step from thread_hooks_common, then a /clear or /compact line
         for the user). Otherwise exit 0.

Default idle is 50 min, inside the 1h prompt-cache TTL: the capture turn reads the context warm
(0.1x input) instead of re-writing it cold (2x input), so on a 300K Opus thread the capture
costs cents, not dollars. Then the user runs /clear, and the old context is never re-sent.

It fires at most once per user message (the capture turn ends with another Stop but no new user
message, so the next waiter stays quiet; thread_cost_alert.py marks a long-thread capture the
same way), and skips threads under --min-cost or --min-ctx-k (cheap to resume; nothing heavy to
drop). Any hook failure exits 0 without output.

Usage:
  python3 thread_idle_capture.py mark                     # hook JSON on stdin
  python3 thread_idle_capture.py wait                     # hook JSON on stdin
  python3 thread_idle_capture.py check --session ID [--json]   # state, whether it would fire
  python3 thread_idle_capture.py --help

Options (each also an env var and a key in the thread-cost config file; env wins, then the file, then the default):
  --idle-min M      THREAD_IDLE_CAPTURE_MIN        / thread_idle_capture_min        (50)
  --min-cost USD    THREAD_IDLE_CAPTURE_MIN_COST   / thread_idle_capture_min_cost   (1)
  --min-ctx-k K     THREAD_IDLE_CAPTURE_MIN_CTX_K  / thread_idle_capture_min_ctx_k  (60)
  --poll S          THREAD_IDLE_CAPTURE_POLL_SECONDS                                (60)
  state             THREAD_HOOKS_STATE_DIR, else <state root>/idle-capture/ (thread_hooks_common.py)
  The hook timeout must exceed idle-min (wired at 3600 s for 50 min).

Output: check prints JSON (--json accepted); wait prints the instruction on stderr when it fires.
Exit codes: 0 ok / nothing to do, 2 wait fired (wakes the model) or check got bad input.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import thread_hooks_common as common  # noqa: E402

WHY = ("Idle thread: no user input for {mins:g} min; the prompt cache lapses at 60 min, after "
       "which any resume re-writes the whole context")


def state_path(session):
    return os.path.join(common.state_dir("idle-capture"), f"{session}.json")


def load(session):
    return common.load_json(state_path(session))


def save(session, state):
    common.save_json(state_path(session), state)


def should_fire(state, waiter_start):
    """The newest waiter fires only if the user has not spoken since it started and nothing
    fired since the user's last message."""
    if state.get("latest_waiter", 0) > waiter_start:
        return False
    last_prompt = state.get("last_prompt", 0)
    if last_prompt > waiter_start:
        return False
    return state.get("last_fire", 0) <= last_prompt


def worth_capturing(session, args):
    usage = common.session_usage(session, timeout=60)
    if usage is None:
        return False, None
    ctx = int((usage.get("last_request") or {}).get("context") or 0)
    ok = usage["cost"] >= args.min_cost and ctx >= args.min_ctx_k * 1000
    return ok, {"cost_usd": round(usage["cost"], 2), "live_context_k": round(ctx / 1000)}


def is_human_prompt(payload):
    """A hook wake (this script's own, or any background-task notification) also arrives as a
    UserPromptSubmit; marking it would re-arm the waiter and capture again every idle period."""
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        return True
    head = prompt.lstrip()[:200]
    return not (head.startswith("<task-notification>") or "Stop hook blocking error" in head
                or common.CAPTURE_MARK in prompt[:2000])


def run_mark(session, payload=None):
    if payload is not None and not is_human_prompt(payload):
        return 0
    state = load(session)
    state["last_prompt"] = time.time()
    save(session, state)
    return 0


def run_wait(session, args):
    start = time.time()
    state = load(session)
    state["latest_waiter"] = start
    save(session, state)
    deadline = start + args.idle_min * 60
    while time.time() < deadline:
        time.sleep(min(args.poll, max(1.0, deadline - time.time())))
        if not should_fire(load(session), start):
            return 0
    state = load(session)
    if not should_fire(state, start):
        return 0
    ok, _ = worth_capturing(session, args)
    if not ok:
        return 0
    state["last_fire"] = time.time()
    save(session, state)
    sys.stderr.write(common.capture_reason(WHY.format(mins=args.idle_min)) + "\n")
    return 2


def run_check(session, args):
    state = load(session)
    ok, usage = worth_capturing(session, args)
    print(json.dumps({
        "session": session, "idle_min": args.idle_min, "min_cost": args.min_cost,
        "min_ctx_k": args.min_ctx_k, "usage": usage, "worth_capturing": ok,
        "would_fire_for_waiter_started_now": should_fire(state, time.time()) and ok,
        "state_file": state_path(session), "state": state,
    }, indent=1))
    return 0


def main():
    c = common.cfg
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("mode", choices=["mark", "wait", "check"])
    p.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID"))
    p.add_argument("--idle-min", type=float, default=c("THREAD_IDLE_CAPTURE_MIN", "thread_idle_capture_min", 50))
    p.add_argument("--min-cost", type=float, default=c("THREAD_IDLE_CAPTURE_MIN_COST", "thread_idle_capture_min_cost", 1))
    p.add_argument("--min-ctx-k", type=float, default=c("THREAD_IDLE_CAPTURE_MIN_CTX_K", "thread_idle_capture_min_ctx_k", 60))
    p.add_argument("--poll", type=float, default=c("THREAD_IDLE_CAPTURE_POLL_SECONDS", "thread_idle_capture_poll_seconds", 60))
    p.add_argument("--json", action="store_true", help="check output is always JSON")
    try:
        args = p.parse_args()
    except SystemExit as e:
        # argparse exits 2 on bad input, which would wake the model from a Stop hook.
        sys.exit(e.code if ("check" in sys.argv[1:2] or "--help" in sys.argv or "-h" in sys.argv) else 0)
    if args.mode == "check":
        if not args.session:
            print("check needs --session or CLAUDE_CODE_SESSION_ID", file=sys.stderr)
            sys.exit(2)
        sys.exit(run_check(args.session, args))
    try:
        payload = json.load(sys.stdin)
        session = payload.get("session_id")
        if not session:
            sys.exit(0)
        sys.exit(run_mark(session, payload) if args.mode == "mark" else run_wait(session, args))
    except SystemExit:
        raise
    except Exception:  # a hook must never break the turn
        sys.exit(0)


if __name__ == "__main__":
    main()
