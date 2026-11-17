#!/usr/bin/env python3
"""Thread cost hooks: per-turn cost line, $5 alerts, and capture-then-clear on long threads.

Part of the thread-cost skill (SKILL.md beside these scripts, or skills/thread-cost/). Reads the hook JSON on stdin for
`session_id`, `hook_event_name` and `stop_hook_active`; cost comes from thread_usage.py (API
list-price estimate, main thread + subagents; read it as relative size, not an invoice).

  UserPromptSubmit -> additionalContext for the model:
      - the previous turn's cost, requests, model mix, tokens in/out and live context, plus the
        thread total, to end the reply with as one line (turn_line; off with
        thread_turn_cost_line=0), e.g.
        _Last turn ~$0.81 (10 requests, opus-5-5; 1.2M in / 4K out), context ~76K, thread ~$1.31 (est.)_
        No model-written line in a session's first reply (the hook runs before that turn costs
        anything); the first Stop shows `First turn ~$...` on screen and seeds a zero baseline.
      - the cost alert once per step ($5, $10, ...), opening with money-with-wings emojis;
      - a cold-cache note when the last request ended over cache-ttl-min ago on a context over
        idle-min-ctx-k (the resume re-writes the whole context at the 1h cache-write rate).
  Stop             -> systemMessage for the user: the cost alert once per step; one note when the
                      live context passes ctx-warn-k.
                   -> capture block: once per capture step (capture-at $15, then every $15) or
                      when the live context passes ctx-capture-k (300K, then every ctx-step-k),
                      blocks the stop with the capture instruction (thread_hooks_common: the
                      capture step, configurable), ending in a /clear (or /compact, and why) line for the user. Never
                      blocks while `stop_hook_active`. Marks the idle-capture state so
                      thread_idle_capture.py does not capture the same thread again. Context
                      levels reset when the context shrinks (after /compact or /clear).

  Stop             -> context-growth capture (--ctx-growth-k, default 40K): the same capture block when
                      the live context passes the session baseline (the first main-thread request's
                      context since the last /compact, read from the transcript; a fresh session
                      starts near 59K) plus 40K, then every further 40K. A compact re-baselines and
                      re-arms. 0 disables. The 300K absolute capture stays as a backstop. The
                      capture also tells the model to record the handoff with thread_handoff.py,
                      whose SessionStart hook resumes from it after /clear or /compact. Skipped for
                      subagents (payload `agent_id`/`agent_type`, or a transcript under /subagents/).

Any failure prints nothing and exits 0, so a hook can never break a turn.

Usage:
  python3 thread_cost_alert.py [hook] [--dry-run] [options]   # hook JSON on stdin
  python3 thread_cost_alert.py check --session ID [--json]    # what would fire; writes nothing
  python3 thread_cost_alert.py status [--session ID]          # harness-neutral: for a harness with no hooks
  python3 thread_cost_alert.py replay --session ID            # per-request context, where growth fires
  python3 thread_cost_alert.py --help

Options (each also an env var and a key in the thread-cost config file; env wins, then the file, then the default):
  --threshold USD     THREAD_COST_ALERT_THRESHOLD / thread_cost_alert_usd      (5)
  --step USD          THREAD_COST_ALERT_STEP      / thread_cost_alert_step_usd (5)
  --capture-at USD    THREAD_COST_CAPTURE_AT      / thread_cost_capture_usd    (15)
  --capture-step USD  THREAD_COST_CAPTURE_STEP    / thread_cost_capture_step_usd (15)
  --ctx-warn-k K      THREAD_CTX_WARN_K           / thread_ctx_warn_k          (200)
  --ctx-capture-k K   THREAD_CTX_CAPTURE_K        / thread_ctx_capture_k       (300)
  --ctx-step-k K      THREAD_CTX_STEP_K           / thread_ctx_step_k          (100)
  --ctx-growth-k K    THREAD_CTX_GROWTH_K         / thread_ctx_growth_k        (40; 0 = off)
  --cache-ttl-min M   THREAD_CACHE_TTL_MIN        / thread_cache_ttl_min       (60)
  --idle-min-ctx-k K  THREAD_IDLE_MIN_CTX_K       / thread_idle_min_ctx_k      (100)
  turn cost line      THREAD_TURN_COST_LINE       / thread_turn_cost_line      (on)
  state               THREAD_HOOKS_STATE_DIR, else <state root>/cost-alert/ (thread_hooks_common.py)

status mode (any harness): the session defaults to the current one of the harness running it
(thread_usage.py detection: Claude Code, opencode, Codex, Antigravity). Prints one line (cost, requests,
live context), then "OK" or "CAPTURE NOW: ..." with the same capture instruction a Stop hook gives, once
per threshold step (state: <state>/status-<session>.json, separate from the hook state; --dry-run
records nothing). Run it at iteration boundaries where the harness has no lifecycle hooks.

Output: hook mode prints a hook JSON object or nothing; check prints JSON (--json is accepted
and is the only format).
Exit codes: 0 ok (hook mode always), 1 check found no usage (or replay no transcript) for the session, 2 bad input (check).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import thread_hooks_common as common  # noqa: E402

MONEY = "\U0001F4B8"  # money with wings


def level_for(value, at, step):
    if value < at:
        return 0
    return 1 + int((value - at) // step)


def avg_context_k(usage):
    tokens, requests = usage["tokens"], usage["requests"]
    processed = tokens.get("processed") if isinstance(tokens, dict) else None
    if not processed or not requests:
        return None
    return round(processed / requests / 1000)


def ctx_note(usage):
    avg = avg_context_k(usage)
    return f", ~{avg}K tokens of context per request" if avg else ""


def live_context(usage):
    last = usage.get("last_request") or {}
    return int(last.get("context") or 0)


def request_contexts(path):
    """Main-thread request contexts since the last /compact, oldest first (deduped by message id)."""
    segments = context_segments(path)
    return segments[-1] if segments else []


def context_segments(path):
    """Main-thread request contexts per compact segment (deduped by message id)."""
    ctxs, index, segments = [], {}, []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"assistant"' not in line and "compact_boundary" not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") == "system" and o.get("subtype") == "compact_boundary":
                    segments.append(ctxs)
                    ctxs, index = [], {}
                    continue
                if o.get("type") != "assistant" or o.get("isSidechain"):
                    continue
                m = o.get("message") or {}
                u = m.get("usage") or {}
                if m.get("model") == "<synthetic>" or not u:
                    continue
                ctx = ((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                       + (u.get("cache_creation_input_tokens") or 0))
                key = m.get("id") or o.get("uuid")
                if key in index:
                    ctxs[index[key]] = ctx
                else:
                    index[key] = len(ctxs)
                    ctxs.append(ctx)
    except OSError:
        return []
    segments.append(ctxs)
    return segments


def transcript_for(session, payload_path=None):
    if payload_path and os.path.exists(payload_path):
        return payload_path
    try:
        import thread_usage
        return thread_usage.find_transcript(session)
    except Exception:
        return None


def is_subagent(payload):
    if payload.get("agent_id") or payload.get("agent_type"):
        return True
    return "/subagents/" in str(payload.get("transcript_path") or "")


def money_banner(level, crossed):
    """One emoji per step up to $15, then loud (caps, more of them) from the 4th step."""
    if level < 4:
        return MONEY * max(level, 1)
    wings = MONEY * min(level + 2, 10)
    return f"{wings} THREAD COST PAST ${crossed:.0f} {wings}"


def alert_message(usage, crossed, level=1):
    return (f"{money_banner(level, crossed)} Thread cost ~${usage['cost']:.2f} (est.), past "
            f"${crossed:.0f}{ctx_note(usage)}. Each request re-reads the whole conversation, so "
            "cost per step keeps rising. Capture a handoff and continue from it in a "
            "new thread at the next natural break.")


def idle_gap_note(usage, now, args):
    last = usage.get("last_request")
    if not last:
        return None
    gap_min = (now - float(last["end"])) / 60
    ctx = int(last.get("context") or 0)
    if gap_min < args.cache_ttl_min or ctx < args.idle_min_ctx_k * 1000:
        return None
    extra = last.get("rewrite_extra_usd")
    extra_txt = f", about ${extra:.2f} more than a warm read" if extra else ""
    hours = gap_min / 60
    gap_txt = f"{hours:.1f}h" if hours >= 1 else f"{gap_min:.0f}m"
    return (f"Idle gap: {gap_txt} since the last request, so the prompt cache has expired and this "
            f"request re-writes ~{round(ctx / 1000)}K tokens of context{extra_txt}. Tell the user in "
            "one line at the start of the reply. If the thread is at an iteration boundary or the "
            f"prompt starts new work, suggest continuing from the handoff {common.clear_phrase(usage.get('harness'))} instead.")


def fmt_tok(n):
    n = n or 0
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def short_model(name):
    return re.sub(r"^claude-", "", name or "?")


def snapshot(usage):
    """What the next prompt's turn line diffs against."""
    tokens = usage["tokens"] if isinstance(usage["tokens"], dict) else {}
    return {"cost": usage["cost"], "requests": usage["requests"],
            "processed": int(tokens.get("processed") or 0),
            "output": int(tokens.get("output") or 0),
            "by_model": {m: {"requests": int(v.get("requests") or 0),
                             "cost": float(v.get("cost_usd") or 0.0)}
                         for m, v in (usage.get("by_model") or {}).items()}}


def model_mix(now, before):
    """Models used since the previous prompt, costliest first: `opus-5-5` or `opus-5-5 $0.70 x8, ...`."""
    rows = []
    for m, v in now.items():
        b = before.get(m, {})
        reqs = v["requests"] - int(b.get("requests", 0))
        if reqs > 0:
            rows.append((max(0.0, v["cost"] - float(b.get("cost", 0.0))), reqs, short_model(m)))
    rows.sort(reverse=True)
    if len(rows) == 1:
        return rows[0][2]
    return ", ".join(f"{m} ${c:.2f} x{r}" for c, r, m in rows)


def turn_line(usage, previous):
    """Cost since the previous prompt, or None when there is no baseline yet.

    `_Last turn ~$0.81 (10 requests, opus-5-5; 1.2M in / 4K out), context ~62K, thread ~$1.31 (est.)_`
    Older state without token fields drops the token and model parts for one turn.
    """
    if not previous:
        return None
    now = snapshot(usage)
    cost = max(0.0, now["cost"] - float(previous.get("cost", 0.0)))
    requests = max(0, now["requests"] - int(previous.get("requests", 0)))
    detail = [f"{requests} requests"]
    if "by_model" in previous:
        mix = model_mix(now["by_model"], previous["by_model"])
        if mix:
            detail[0] += f", {mix}"
    if "processed" in previous:
        tin = max(0, now["processed"] - int(previous["processed"]))
        tout = max(0, now["output"] - int(previous.get("output", 0)))
        detail.append(f"{fmt_tok(tin)} in / {fmt_tok(tout)} out")
    ctx = live_context(usage)
    ctx_txt = f", context ~{fmt_tok(ctx)}" if ctx else ""
    return (f"_Last turn ~${cost:.2f} ({'; '.join(detail)}){ctx_txt}, "
            f"thread ~${usage['cost']:.2f} (est.)_")


def mark_idle_capture_done(session):
    """Tell thread_idle_capture.py a capture already ran after the user's last message."""
    try:
        import thread_idle_capture as idle
        state = idle.load(session)
        state["last_fire"] = time.time()
        idle.save(session, state)
    except Exception:
        pass


def prompt_output(args, usage, state, now=None):
    parts = []
    gap = idle_gap_note(usage, now if now is not None else time.time(), args)
    if gap:
        parts.append(gap)
    line = turn_line(usage, state.get("prompt_total")) if args.turn_line else None
    if line:
        parts.append(f"Cost of the previous turn, for the user: end this reply with this line, "
                     f"exactly, as its last line: {line}")
    state["prompt_total"] = snapshot(usage)
    level = level_for(usage["cost"], args.threshold, args.step)
    if level and state.get("UserPromptSubmit", 0) < level:
        crossed = args.threshold + (level - 1) * args.step
        parts.append(alert_message(usage, crossed, level) + " Tell the user this at the start of "
                     "the reply, opening with the money emojis exactly as shown (keep the caps when "
                     "present), then name the natural point to bounce (capture, handoff, "
                     + ("new conversation" if usage.get("harness") in common.NO_CLEAR else "/clear") + ").")
        state["UserPromptSubmit"] = level
    if not parts:
        return None
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                   "additionalContext": "\n".join(parts)}}


ZERO_BASELINE = {"cost": 0.0, "requests": 0, "processed": 0, "output": 0, "by_model": {}}


def add_message(out, text):
    first = out.get("systemMessage")
    out["systemMessage"] = f"{first}\n{text}" if first else text


def stop_output(args, usage, state, stop_hook_active):
    out = {}
    # A fresh session's first prompt runs before its transcript exists, so no baseline was saved.
    # Show the first turn's line on screen and start the next diff from zero.
    if "prompt_total" not in state:
        line = turn_line(usage, ZERO_BASELINE) if args.turn_line else None
        if line:
            add_message(out, line.strip("_").replace("Last turn", "First turn", 1))
        state["prompt_total"] = dict(ZERO_BASELINE)
    level = level_for(usage["cost"], args.threshold, args.step)
    if level and state.get("Stop", 0) < level:
        add_message(out, alert_message(usage, args.threshold + (level - 1) * args.step, level))
        state["Stop"] = level
    ctx = live_context(usage)
    warn_at, cap_at, step = args.ctx_warn_k * 1000, args.ctx_capture_k * 1000, args.ctx_step_k * 1000
    if ctx < warn_at:
        state["ctx_warn"] = 0
    if ctx < cap_at:
        state["ctx_capture"] = 0
    if ctx >= warn_at and not state.get("ctx_warn"):
        add_message(out, f"Context is now ~{round(ctx / 1000)}K tokens; every request re-reads it. "
                         "The prompt cache lasts 1h, so if you will be away longer, capture and "
                         f"continue from a handoff {common.clear_phrase(usage.get('harness'))}.")
        state["ctx_warn"] = 1
    elif ctx >= warn_at:
        state["ctx_warn"] = 1
    capture = level_for(usage["cost"], args.capture_at, args.capture_step)
    ctx_cap = level_for(ctx, cap_at, step) if ctx >= cap_at else 0
    growth_step = args.ctx_growth_k * 1000
    baseline = usage.get("ctx_baseline") or 0
    growth = 0
    if growth_step > 0 and baseline:
        growth = int(max(0, ctx - baseline) // growth_step)
        if growth < state.get("ctx_growth", 0) and ctx < baseline + growth_step:
            state["ctx_growth"] = 0  # context fell back (a /compact): re-arm
    why = None
    if capture and state.get("capture", 0) < capture:
        crossed = args.capture_at + (capture - 1) * args.capture_step
        why = f"Long thread: ~${usage['cost']:.2f} (est.){ctx_note(usage)}, past ${crossed:.0f}"
    elif ctx_cap and state.get("ctx_capture", 0) < ctx_cap:
        why = (f"Long thread: context ~{round(ctx / 1000)}K tokens, past {round(cap_at / 1000)}K; "
               f"~${usage['cost']:.2f} (est.) so far")
    elif growth and state.get("ctx_growth", 0) < growth:
        why = (f"Long thread: context ~{round(ctx / 1000)}K tokens, {round((ctx - baseline) / 1000)}K "
               f"past this session's ~{round(baseline / 1000)}K start; ~${usage['cost']:.2f} (est.) so far")
    if why and not stop_hook_active:
        out["decision"] = "block"
        out["reason"] = common.capture_reason(why, getattr(args, "session_cwd", None) or os.getcwd(),
                                             harness=usage.get("harness"))
        state["capture"] = max(capture, state.get("capture", 0))
        state["ctx_capture"] = max(ctx_cap, state.get("ctx_capture", 0))
        state["ctx_growth"] = max(growth, state.get("ctx_growth", 0))
        state["captured_at"] = time.time()
    return out or None


def state_path(session):
    return os.path.join(common.state_dir("cost-alert"), f"{session}.json")


def run_hook(args):
    payload = json.load(sys.stdin)
    session = payload.get("session_id")
    event = payload.get("hook_event_name") or "Stop"
    if not session or event not in ("Stop", "UserPromptSubmit"):
        return
    usage = common.session_usage(session)
    if usage is None:
        return
    if event == "Stop" and args.ctx_growth_k > 0 and not is_subagent(payload):
        tpath = transcript_for(session, payload.get("transcript_path"))
        ctxs = request_contexts(tpath) if tpath else []
        usage["ctx_baseline"] = ctxs[0] if ctxs else 0
    path = state_path(session)
    state = common.load_json(path)
    if event == "UserPromptSubmit":
        out = prompt_output(args, usage, state)
    else:
        args.session_cwd = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
        out = stop_output(args, usage, state, bool(payload.get("stop_hook_active")))
    if not args.dry_run:
        common.save_json(path, state)
        if out and out.get("decision") == "block":
            mark_idle_capture_done(session)
    if out:
        print(json.dumps(out))


def run_check(args):
    usage = common.session_usage(args.session)
    if usage is None:
        print(json.dumps({"session": args.session, "error": "no usage report"}))
        return 1
    state = common.load_json(state_path(args.session))
    ctx = live_context(usage)
    tpath = transcript_for(args.session)
    ctxs = request_contexts(tpath) if tpath else []
    baseline = ctxs[0] if ctxs else 0
    capture = level_for(usage["cost"], args.capture_at, args.capture_step)
    print(json.dumps({
        "session": args.session, "cost_usd": round(usage["cost"], 2),
        "requests": usage["requests"], "avg_context_k": avg_context_k(usage),
        "live_context_k": round(ctx / 1000),
        "alert_level": level_for(usage["cost"], args.threshold, args.step),
        "capture_level": capture,
        "ctx_capture_level": level_for(ctx, args.ctx_capture_k * 1000, args.ctx_step_k * 1000),
        "ctx_baseline_k": round(baseline / 1000),
        "ctx_growth_level": (int(max(0, ctx - baseline) // (args.ctx_growth_k * 1000))
                             if args.ctx_growth_k > 0 and baseline else 0),
        "capture_would_fire": bool(capture and state.get("capture", 0) < capture),
        "idle_gap_note": idle_gap_note(usage, time.time(), args),
        "next_turn_line": turn_line(usage, state.get("prompt_total")),
        "state_file": state_path(args.session), "state": state,
    }, indent=1))
    return 0


def run_replay(args):
    path = transcript_for(args.session)
    if not path:
        print("no transcript")
        return 1
    step = args.ctx_growth_k * 1000
    for n, ctxs in enumerate(context_segments(path)):
        if not ctxs:
            continue
        base, level, fired = ctxs[0], 0, []
        for i, c in enumerate(ctxs):
            growth = int(max(0, c - base) // step) if step > 0 else 0
            if growth < level and c < base + step:
                level = 0
            if growth > level:
                fired.append(f"request {i + 1} ctx {round(c / 1000)}K (+{round((c - base) / 1000)}K, level {growth})")
                level = growth
        print(f"segment {n}: {len(ctxs)} requests, baseline {round(base / 1000)}K, "
              f"peak {round(max(ctxs) / 1000)}K, growth {args.ctx_growth_k:g}K")
        print("  would fire at: " + ("; ".join(fired) if fired else "never"))
    return 0


def current_session():
    """Current session of the harness running this (thread_usage.py's detection), or None."""
    out = subprocess.run([sys.executable, common.THREAD_USAGE, "report", "--json"],
                         capture_output=True, text=True, timeout=60)
    try:
        return json.loads(out.stdout).get("session") if out.returncode == 0 else None
    except ValueError:
        return None


def run_status(args):
    session = args.session or current_session()
    if not session:
        print("status: no current session found; pass --session", file=sys.stderr)
        return 2
    usage = common.session_usage(session)
    if usage is None:
        print(f"status: no usage report for session {session}", file=sys.stderr)
        return 1
    path = os.path.join(common.state_dir("cost-alert"), f"status-{session}.json")
    state = common.load_json(path)
    tpath = transcript_for(session)
    ctxs = request_contexts(tpath) if tpath and args.ctx_growth_k > 0 else []
    usage["ctx_baseline"] = ctxs[0] if ctxs else 0
    args.session_cwd = os.getcwd()
    out = stop_output(args, usage, state, False) or {}
    if not args.dry_run:
        common.save_json(path, state)
    ctx = live_context(usage)
    print(f"session {session[:8]}: ~${usage['cost']:.2f} (est.), {usage['requests']} requests, "
          f"context {round(ctx / 1000)}K")
    if out.get("systemMessage"):
        print(out["systemMessage"])
    print(f"CAPTURE NOW: {out['reason']}" if out.get("decision") == "block" else "OK")
    return 0


def main():
    c = common.cfg
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("mode", nargs="?", choices=["hook", "check", "replay", "status"], default="hook")
    p.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID"))
    p.add_argument("--threshold", type=float, default=c("THREAD_COST_ALERT_THRESHOLD", "thread_cost_alert_usd", 5))
    p.add_argument("--step", type=float, default=c("THREAD_COST_ALERT_STEP", "thread_cost_alert_step_usd", 5))
    p.add_argument("--capture-at", type=float, default=c("THREAD_COST_CAPTURE_AT", "thread_cost_capture_usd", 15))
    p.add_argument("--capture-step", type=float, default=c("THREAD_COST_CAPTURE_STEP", "thread_cost_capture_step_usd", 15))
    p.add_argument("--ctx-warn-k", type=float, default=c("THREAD_CTX_WARN_K", "thread_ctx_warn_k", 200))
    p.add_argument("--ctx-capture-k", type=float, default=c("THREAD_CTX_CAPTURE_K", "thread_ctx_capture_k", 300))
    p.add_argument("--ctx-step-k", type=float, default=c("THREAD_CTX_STEP_K", "thread_ctx_step_k", 100))
    p.add_argument("--ctx-growth-k", type=float, default=c("THREAD_CTX_GROWTH_K", "thread_ctx_growth_k", 40))
    p.add_argument("--cache-ttl-min", type=float, default=c("THREAD_CACHE_TTL_MIN", "thread_cache_ttl_min", 60))
    p.add_argument("--idle-min-ctx-k", type=float, default=c("THREAD_IDLE_MIN_CTX_K", "thread_idle_min_ctx_k", 100))
    p.add_argument("--json", action="store_true", help="check output is always JSON")
    p.add_argument("--dry-run", action="store_true", help="hook mode: print, record nothing")
    try:
        args = p.parse_args()
    except SystemExit as e:
        # argparse exits 2 on bad input, which would block a Stop hook; only check fails loudly.
        sys.exit(e.code if ("check" in sys.argv[1:2] or "--help" in sys.argv or "-h" in sys.argv) else 0)
    args.turn_line = common.cfg_flag("THREAD_TURN_COST_LINE", "thread_turn_cost_line", True)
    if args.mode == "status":
        sys.exit(run_status(args))
    if args.mode == "replay":
        if not args.session:
            print("replay needs --session or CLAUDE_CODE_SESSION_ID", file=sys.stderr)
            sys.exit(2)
        sys.exit(run_replay(args))
    if args.mode == "check":
        if not args.session:
            print("check needs --session or CLAUDE_CODE_SESSION_ID", file=sys.stderr)
            sys.exit(2)
        sys.exit(run_check(args))
    try:
        run_hook(args)
    except Exception:  # a hook must never break the turn
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
