#!/usr/bin/env python3
"""Antigravity lifecycle-hook adapter for the thread cost hooks (cost line, alerts, capture-then-clear).

Antigravity hooks (hooks.json in ~/.gemini/config/) send camelCase JSON on stdin and expect
`injectSteps` / `decision` JSON on stdout. This wraps thread_cost_alert.py, which speaks the Claude
Code hook shape: it maps the payload in, runs it, and maps the output back.

  prompt   PreInvocation: once per user message (repeat model calls of one turn are skipped), the
           previous turn's cost line, $5 alerts and cold-cache note as an ephemeralMessage
  stop     Stop: when the loop ends normally (NO_TOOL_CALL), a capture block becomes
           {"decision": "continue", "reason": <capture instruction>}, which re-enters the loop once;
           on-screen-only notes (systemMessage) have no Antigravity channel and are dropped

Not available there: the idle-wake capture (hooks are synchronous), the SessionStart handoff
resume (no clear/compact source), and the growth capture (no readable transcript). Cost needs the
live local server (the 2.x `agy --hub`, or the 1.x language server; see the antigravity
notes in thread_usage.py --help); with no tokens, nothing fires. The hook runs whatever `python3` is on Antigravity's
PATH, so these scripts stay python 3.8-safe (no dict `|`, no str.removeprefix). A new
conversation replaces /clear.

Usage:
  python3 thread_hook_antigravity.py prompt|stop [--dry-run]    # hook JSON on stdin
  python3 thread_hook_antigravity.py --help
Output: hook JSON on stdout (`{}` when nothing to say; --json is accepted and is the only format). Any failure prints `{}` and exits 0.
Exit codes: 0 always.
"""
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import thread_hooks_common as common  # noqa: E402

ALERT = os.path.join(HERE, "thread_cost_alert.py")


def run_alert(claude_payload, dry):
    cmd = [sys.executable, ALERT] + (["--dry-run"] if dry else [])
    out = subprocess.run(cmd, input=json.dumps(claude_payload), capture_output=True, text=True, timeout=60)
    try:
        return json.loads(out.stdout) if out.stdout.strip() else {}
    except ValueError:
        return {}


def convert(mode, payload, dry):
    cid = payload.get("conversationId")
    if not cid:
        return {}
    cwd = (payload.get("workspacePaths") or [os.getcwd()])[0]
    if mode == "prompt":
        key = hashlib.sha1(f"{payload.get('lastUserInput')}|{payload.get('initialNumSteps')}".encode()).hexdigest()
        path = os.path.join(common.state_dir("antigravity"), f"{cid}.json")
        state = common.load_json(path)
        if state.get("last_prompt") == key:
            return {}
        if not dry:
            state["last_prompt"] = key
            common.save_json(path, state)
        out = run_alert({"session_id": cid, "hook_event_name": "UserPromptSubmit", "cwd": cwd}, dry)
        text = (out.get("hookSpecificOutput") or {}).get("additionalContext")
        return {"injectSteps": [{"ephemeralMessage": text}]} if text else {}
    if payload.get("terminationReason", "NO_TOOL_CALL") != "NO_TOOL_CALL":
        return {}
    out = run_alert({"session_id": cid, "hook_event_name": "Stop", "cwd": cwd}, dry)
    if out.get("decision") == "block":
        return {"decision": "continue", "reason": out.get("reason", "")}
    return {}


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help") or args[0] not in ("prompt", "stop"):
        print(__doc__)
        return 0
    try:
        out = convert(args[0], json.load(sys.stdin), "--dry-run" in args)
    except Exception:  # a hook must never break the turn
        out = {}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
