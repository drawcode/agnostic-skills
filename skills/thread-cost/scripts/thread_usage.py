#!/usr/bin/env python3
"""Time, tokens and estimated cost of an agent thread, per capture snapshot and cumulative.

Part of the thread-cost skill (SKILL.md beside these scripts, or skills/thread-cost/).

Reads the harness transcript (Claude Code: the session JSONL plus its subagents/) and reports
two windows: since the last snapshot of this session, and since the top of the thread. A
snapshot appends one row to a per-session ledger and stamps a Usage block into the docs it
captured, so later sessions can see what each piece of work cost.

Usage:
  thread_usage.py report                          # current session: since last snapshot + total
  thread_usage.py report --session ID --json
  thread_usage.py snapshot --doc notes/handoff-x.md [--doc ...] [--thread my-feature]
                           [--label "iter3 capture"] [--no-stamp] [--dry-run]
  thread_usage.py ledger [--thread T] [--doc PATH] [--json]   # sums across sessions
  thread_usage.py stamp --doc PATH                # re-render a doc's Usage block from the ledger
  thread_usage.py backfill [--harness H] [--dry-run]  # one row per past session not yet in the
                                                  # ledger; docs = .md under the docs root it edited; no stamps

Options:
  --session ID       session id (default: $CLAUDE_CODE_SESSION_ID, else newest transcript)
  --transcript PATH  read this JSONL instead of looking the session up
  --idle MIN         a human gap longer than this counts as idle, not active (default 5)
  --prices PATH      price table (default: model_prices.json beside this script)
  --ledger-dir DIR   ledger root (default: $THREAD_USAGE_LEDGER_DIR, config thread_usage_ledger_dir,
                     else <state root>/usage-ledger; see thread_hooks_common.py)
  docs root          relative --doc paths and backfill docs resolve against $THREAD_USAGE_DOCS_ROOT,
                     config thread_usage_docs_root, else the git top level of the cwd, else the cwd

Metrics (each window):
  wall      first to last event; active = agent busy + human gaps capped at --idle
  agent     time the main thread was working (model + tools), model = request latency sum
  bg_wait   time the main thread sat waiting on a background task notification
  tokens    input, cache write 5m/1h, cache read, output; new = all but cache read
  cost      API list price estimate (model_prices.json), main + subagents; a subscription
            plan bills differently, so read it as relative size, not an invoice
  also      prompts, requests, peak context, compactions, tool calls by name, files touched,
            subagents (model, description, cost), web searches, thinking time, effort mix

Ledger: <ledger-dir>/<session>.jsonl, one JSON row per snapshot:
  {v, snap, session, harness, title, thread, label, docs, from, to, delta, total, prices_as_of}
  Only one session writes each file, so parallel sessions never conflict.

Doc block: replaced between the begin/end marker lines (each alone on its line), appended
at the end of the doc the first time. Rows: last snapshot, thread to here, all snapshots of the doc.

Harnesses (--harness, default auto: from the session id shape, else the environment):
  claude-code  ~/.claude/projects/*/<session>.jsonl + <session>/subagents/; current = $CLAUDE_CODE_SESSION_ID
  opencode     ~/.local/share/opencode/opencode.db (ses_ ids); child sessions = subagents; also
               sums the harness-reported cost; current = $OPENCODE_SESSION_ID, else newest root
               session for this cwd
  codex        ~/.codex/sessions/**/rollout-*<thread>.jsonl; spawned threads from state_5.sqlite;
               current = $CODEX_THREAD_ID, else newest rollout
  antigravity  while Antigravity runs: its local server (GetCascadeTrajectory on 127.0.0.1) gives
               per-call tokens, real model, credits and step times. 2.x: the `agy --hub` server,
               found via ~/.gemini/antigravity*/daemon/hub_<port>.json (ports + CSRF token); 1.x:
               the language_server process args. Gemini priced from model_prices.json (dated
               intro rates via "changes"). Otherwise ~/.gemini/antigravity*/conversation_summaries.db
               (logs on disk are encrypted): wall time, steps, subagents; tokens and cost n/a.
               Subagents = child conversations. current = $ANTIGRAVITY_CONVERSATION_ID, else
               newest for this cwd. THREAD_USAGE_NO_LIVE=1 skips the live call
  To add one: load_<name>(session) -> {session, harness, title, events, agents}, register it in
  ADAPTERS and CURRENT.

--help prints the options; --json on report/ledger gives machine-readable output.

Exit: 0 ok, 2 bad input or transcript not found.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sqlite3
import ssl
import subprocess
import urllib.request
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PRICES = os.path.join(HERE, "model_prices.json")
if HERE not in sys.path:
    sys.path.insert(0, HERE)  # thread_hooks_common (config, state root)
BLOCK_BEGIN = "<!-- thread-usage:begin -->"
BLOCK_END = "<!-- thread-usage:end -->"
FILE_TOOLS = ("Edit", "Write", "NotebookEdit", "MultiEdit", "edit", "write", "patch", "apply_patch")
ASK_TOOLS = ("AskUserQuestion", "question", "request_user_input")
FILES_KEPT = 40
TOOLS_SHOWN = 8


def strip_prefix(s, prefix):
    """str.removeprefix for python 3.8 (a venv python3 can be older than the system one)."""
    return s[len(prefix):] if s.startswith(prefix) else s


# ---------- time helpers ----------

def parse_ts(s):
    if not s:
        return None
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.replace("Z", "+00:00"))
    s = re.sub(r"\.(\d{1,5})(?=[+-]|$)", lambda m: "." + m.group(1).ljust(6, "0"), s)
    try:
        return dt.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def iso(ts):
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_stamp(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def fmt_dur(s):
    s = int(round(s or 0))
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h{m % 60:02d}m"


def fmt_tok(n):
    n = n or 0
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def fmt_cost(c):
    return "n/a" if c is None else f"${c:,.2f}"


# ---------- pricing ----------

class Prices:
    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.as_of = data.get("as_of")
        self.sources = data.get("sources") or {}
        self.w5 = data.get("cache_write_5m_mult", 1.25)
        self.w1h = data.get("cache_write_1h_mult", 2.0)
        self.models = data["models"]
        self._keys = sorted(self.models, key=len, reverse=True)

    def lookup(self, model, ts=None):
        """Price entry for a model. An entry's "changes": [{"from": "YYYY-MM-DD", <rates>}] apply
        to requests on or after that date (ts = request epoch seconds; None = today)."""
        p = self._match(model)
        if not p or not p.get("changes"):
            return p
        day = dt.datetime.fromtimestamp(ts if ts is not None else time.time()).strftime("%Y-%m-%d")
        for ch in sorted(p["changes"], key=lambda c: c["from"]):
            if day >= ch["from"]:
                p = {**p, **{k: v for k, v in ch.items() if k != "from"}}
        return p

    def _match(self, model):
        if not model:
            return None
        m = re.sub(r"\[.*?\]$", "", model)
        m = re.sub(r"-\d{8}$", "", m)
        m = m.split("/")[-1]
        m = re.sub(r"^(?:[a-z]{2,6}\.)?anthropic\.", "", m)
        for k in self._keys:
            if m == k or (m.startswith(k + "-") and not self.models[k].get("exact")):
                return self.models[k]
        return None

    def cost(self, model, u, speed, ts=None):
        p = self.lookup(model, ts)
        if p is None:
            return None
        prompt = u["input"] + u["cache_read"] + u["cache_write_5m"] + u["cache_write_1h"]
        if p.get("long") and prompt > p.get("long_threshold", 1e18):
            p = {**p, **p["long"]}
        mult = p.get("fast", 1.0) if speed == "fast" else 1.0
        inp = p["input"]
        w5 = p.get("cache_write_5m", inp * p.get("cache_write_mult", self.w5))
        w1h = p.get("cache_write_1h", inp * p.get("cache_write_mult", self.w1h))
        usd = (u["input"] * inp
               + u["cache_write_5m"] * w5
               + u["cache_write_1h"] * w1h
               + u["cache_read"] * p["cache_read"]
               + u["output"] * p["output"]) / 1_000_000
        return usd * mult


# ---------- claude-code adapter ----------

def claude_root():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def find_transcript(session):
    base = os.path.join(claude_root(), "projects")
    if session:
        hits = glob.glob(os.path.join(base, "*", f"{session}.jsonl"))
        return hits[0] if hits else None
    hits = glob.glob(os.path.join(base, "*", "*.jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def read_jsonl(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def usage_of(u):
    cc = u.get("cache_creation") or {}
    w5 = cc.get("ephemeral_5m_input_tokens")
    w1h = cc.get("ephemeral_1h_input_tokens")
    total_w = u.get("cache_creation_input_tokens") or 0
    if w5 is None and w1h is None:
        w5, w1h = total_w, 0
    stu = u.get("server_tool_use") or {}
    return {
        "input": u.get("input_tokens") or 0,
        "cache_write_5m": w5 or 0,
        "cache_write_1h": w1h or 0,
        "cache_read": u.get("cache_read_input_tokens") or 0,
        "output": u.get("output_tokens") or 0,
        "web": (stu.get("web_search_requests") or 0) + (stu.get("web_fetch_requests") or 0),
    }


def human_kind(o):
    """Classify a user line: 'human', 'notify' (background/task message), or None (tool result)."""
    if o.get("toolUseResult") is not None or o.get("isMeta") or o.get("isCompactSummary"):
        return None
    msg = o.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list) and content and all(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    origin = (o.get("origin") or {}).get("kind") or o.get("turnOrigin")
    if origin and origin != "human":
        return "notify"
    text = content if isinstance(content, str) else " ".join(
        b.get("text", "") for b in content or [] if isinstance(b, dict))
    if "<task-notification>" in text:
        return "notify"
    return "human"


def load_stream(path, src):
    """One transcript file -> (events, title). Assistant blocks dedupe by message id."""
    events, title = [], None
    req_ts, msgs = {}, {}
    for o in read_jsonl(path):
        t = o.get("type")
        ts = parse_ts(o.get("timestamp"))
        if t == "ai-title":
            title = o.get("aiTitle") or title
            continue
        if t == "api-request" and o.get("id"):
            req_ts[o["id"]] = ts
            continue
        if ts is None:
            continue
        if t == "assistant":
            m = o.get("message") or {}
            mid = m.get("id") or o.get("uuid")
            rec = msgs.get(mid)
            if rec is None:
                rec = msgs[mid] = {
                    "ts": ts, "end": ts, "src": src, "kind": "request",
                    "model": m.get("model"), "usage": usage_of(m.get("usage") or {}),
                    "speed": (m.get("usage") or {}).get("speed"),
                    "req": o.get("requestRef"), "effort": o.get("effort"),
                    "thinking_ms": 0, "tools": [],
                }
            rec["end"] = max(rec["end"], ts)
            if m.get("usage"):
                rec["usage"] = usage_of(m["usage"])
            rec["thinking_ms"] += o.get("thinkingDurationMs") or 0
            for b in m.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    rec["tools"].append((b.get("name"), inp.get("file_path") or inp.get("notebook_path")))
            continue
        if t == "user":
            k = human_kind(o)
            events.append({"ts": ts, "src": src, "kind": k or "result"})
            continue
        if t == "system" and o.get("subtype") == "compact_boundary":
            cm = o.get("compactMetadata") or {}
            events.append({"ts": ts, "src": src, "kind": "compact", "pre": cm.get("preTokens"),
                           "post": cm.get("postTokens"), "trigger": cm.get("trigger")})
            continue
    for rec in msgs.values():
        if rec["model"] == "<synthetic>":
            continue
        rec["req_ts"] = req_ts.get(rec["req"])
        events.append(rec)
    events.sort(key=lambda e: e["ts"])
    return events, title


def load_claude_code(path):
    events, title = load_stream(path, "main")
    agents = {}
    sub_dir = os.path.join(os.path.splitext(path)[0], "subagents")
    for f in sorted(glob.glob(os.path.join(sub_dir, "*.jsonl"))):
        aid = strip_prefix(os.path.basename(f)[:-6], "agent-")
        meta = {}
        mp = f[:-6] + ".meta.json"
        if os.path.exists(mp):
            try:
                with open(mp, encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, json.JSONDecodeError):
                meta = {}
        ev, _ = load_stream(f, f"agent:{aid}")
        agents[aid] = {"desc": meta.get("description"), "type": meta.get("agentType"),
                       "tier": meta.get("model")}
        events.extend(ev)
    events.sort(key=lambda e: e["ts"])
    sid = os.path.basename(path)[:-6]
    return {"session": sid, "harness": "claude-code", "title": title, "events": events,
            "agents": agents, "path": path}


# ---------- opencode adapter ----------
# Reads ~/.local/share/opencode/opencode.db (read-only). One `message` row per user prompt or
# assistant step, with tokens, cost and created/completed times; `part` rows carry tool calls.
# Child sessions (session.parent_id) are subagents.

def opencode_db():
    return os.environ.get("OPENCODE_DB") or os.path.expanduser("~/.local/share/opencode/opencode.db")


def oc_connect():
    db = opencode_db()
    if not os.path.exists(db):
        return None
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def oc_session_events(con, sid, src):
    events, step_tools = [], {}
    for mid, data in con.execute("SELECT message_id, data FROM part WHERE session_id=? ORDER BY time_created", (sid,)):
        try:
            d = json.loads(data)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "tool":
            inp = (d.get("state") or {}).get("input") or {}
            step_tools.setdefault(mid, []).append((d.get("tool"), inp.get("filePath") or inp.get("file_path")))
    for mid, data in con.execute("SELECT id, data FROM message WHERE session_id=? ORDER BY time_created", (sid,)):
        try:
            d = json.loads(data)
        except json.JSONDecodeError:
            continue
        tm = d.get("time") or {}
        ts = (tm.get("created") or 0) / 1000
        if not ts:
            continue
        if d.get("role") == "user":
            events.append({"ts": ts, "src": src, "kind": "human" if src == "main" else "result"})
            continue
        if d.get("summary") is True or d.get("mode") == "compaction" or d.get("agent") == "compaction":
            events.append({"ts": ts, "src": src, "kind": "compact", "pre": None, "post": None, "trigger": "auto"})
        tk = d.get("tokens") or {}
        cache = tk.get("cache") or {}
        end = (tm.get("completed") or tm.get("created")) / 1000
        events.append({"ts": ts, "src": src, "kind": "result"})
        events.append({
            "ts": ts, "end": end, "src": src, "kind": "request", "model": d.get("modelID"),
            "usage": {"input": tk.get("input") or 0, "cache_write_5m": cache.get("write") or 0,
                      "cache_write_1h": 0, "cache_read": cache.get("read") or 0,
                      "output": (tk.get("output") or 0) + (tk.get("reasoning") or 0), "web": 0},
            "speed": None, "req_ts": ts, "effort": d.get("variant"), "thinking_ms": 0,
            "tools": step_tools.get(mid, []), "cost_reported": d.get("cost") or 0.0,
        })
    return events


def load_opencode(sid):
    con = oc_connect()
    if con is None:
        return None
    row = con.execute("SELECT id, title FROM session WHERE id=?", (sid,)).fetchone()
    if not row:
        return None
    events = oc_session_events(con, sid, "main")
    agents, todo = {}, [sid]
    while todo:
        parent = todo.pop()
        for cid, title, agent in con.execute("SELECT id, title, agent FROM session WHERE parent_id=?", (parent,)):
            agents[cid] = {"desc": title, "type": agent, "tier": None}
            events.extend(oc_session_events(con, cid, f"agent:{cid}"))
            todo.append(cid)
    events.sort(key=lambda e: e["ts"])
    return {"session": sid, "harness": "opencode", "title": row[1], "events": events,
            "agents": agents, "path": opencode_db()}


def opencode_current():
    con = oc_connect()
    if con is None:
        return None
    cwd = os.getcwd()
    rows = con.execute("SELECT id, directory FROM session WHERE parent_id IS NULL "
                       "ORDER BY time_updated DESC LIMIT 20").fetchall()
    for sid, d in rows:
        if d and (cwd == d or cwd.startswith(d.rstrip("/") + "/")):
            return sid
    return rows[0][0] if rows else None


# ---------- codex adapter ----------
# Reads ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<thread id>.jsonl. Lines are
# {timestamp, type, payload}: session_meta, turn_context (model, effort), response_item
# (message, function_call, custom_tool_call, function_call_output, ...), event_msg
# (user_message, token_count, ...), compacted. token_count carries running totals; each change in
# total_token_usage is one model response. OpenAI usage counts cached tokens inside input_tokens
# and reasoning inside output_tokens. Spawned threads come from state_5.sqlite thread_spawn_edges.

def codex_home():
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def codex_find(thread_id):
    hits = glob.glob(os.path.join(codex_home(), "sessions", "**", f"rollout-*{thread_id}.jsonl"), recursive=True)
    return hits[0] if hits else None


def codex_current():
    tid = os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")
    if tid:
        return tid
    hits = glob.glob(os.path.join(codex_home(), "sessions", "**", "rollout-*.jsonl"), recursive=True)
    if not hits:
        return None
    m = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27})\.jsonl$", max(hits, key=os.path.getmtime))
    return m.group(1) if m else None


PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.M)


def codex_tool(p):
    t = p.get("type")
    if t == "function_call":
        name, args = p.get("name"), p.get("arguments") or ""
    elif t == "custom_tool_call":
        name, args = p.get("name"), p.get("input") or ""
    elif t in ("local_shell_call", "web_search_call", "image_generation_call"):
        return [(t[:-5] if t.endswith("_call") else t, None)]
    else:
        return []
    files = PATCH_FILE_RE.findall(args) if isinstance(args, str) else []
    return [(name, f) for f in files] or [(name, None)]


def codex_stream(path, src):
    events, title, sid = [], None, None
    model = effort = None
    prev_total, last_input_ts, pending = None, None, []
    for o in read_jsonl(path):
        ts = parse_ts(o.get("timestamp"))
        t, p = o.get("type"), o.get("payload") or {}
        if t == "session_meta":
            sid = p.get("id") or p.get("session_id")
            continue
        if ts is None:
            continue
        if t == "turn_context":
            model, effort = p.get("model") or model, p.get("effort") or effort
            continue
        if t == "compacted" or (t == "event_msg" and p.get("type") == "context_compacted"):
            events.append({"ts": ts, "src": src, "kind": "compact", "pre": None, "post": None, "trigger": "auto"})
            continue
        if t == "response_item":
            pt = p.get("type")
            if pt in ("function_call_output", "custom_tool_call_output"):
                events.append({"ts": ts, "src": src, "kind": "result"})
                last_input_ts = ts
            else:
                pending.extend(codex_tool(p))
            continue
        if t != "event_msg":
            continue
        et = p.get("type")
        if et == "user_message":
            title = title or (p.get("message") or "").strip().splitlines()[0][:80] if p.get("message") else title
            events.append({"ts": ts, "src": src, "kind": "human" if src == "main" else "result"})
            last_input_ts = ts
        elif et == "token_count":
            info = p.get("info") or {}
            tot = info.get("total_token_usage")
            if not tot:
                continue
            if prev_total is None:
                delta = info.get("last_token_usage") or tot
            else:
                delta = {k: (tot.get(k) or 0) - (prev_total.get(k) or 0) for k in tot}
            prev_total = tot
            if not any(v for k, v in delta.items() if k != "total_tokens"):
                continue
            cached = delta.get("cached_input_tokens") or 0
            events.append({
                "ts": last_input_ts or ts, "end": ts, "src": src, "kind": "request", "model": model,
                "usage": {"input": max(0, (delta.get("input_tokens") or 0) - cached),
                          "cache_write_5m": delta.get("cache_write_input_tokens") or 0,
                          "cache_write_1h": 0, "cache_read": cached,
                          "output": delta.get("output_tokens") or 0, "web": 0},
                "speed": None, "req_ts": last_input_ts, "effort": effort, "thinking_ms": 0,
                "tools": pending,
            })
            pending = []
        else:
            events.append({"ts": ts, "src": src, "kind": "tick"})
    return events, title, sid


def load_codex(tid):
    path = codex_find(tid)
    if not path:
        return None
    events, title, sid = codex_stream(path, "main")
    agents = {}
    state = os.path.join(codex_home(), "state_5.sqlite")
    if os.path.exists(state):
        con = sqlite3.connect(f"file:{state}?mode=ro", uri=True)
        try:
            title = (con.execute("SELECT title FROM threads WHERE id=?", (tid,)).fetchone() or [title])[0] or title
            todo = [tid]
            while todo:
                parent = todo.pop()
                for (cid,) in con.execute("SELECT child_thread_id FROM thread_spawn_edges WHERE parent_thread_id=?", (parent,)):
                    cp = codex_find(cid)
                    if cp:
                        ev, ctitle, _ = codex_stream(cp, f"agent:{cid}")
                        events.extend(ev)
                        agents[cid] = {"desc": ctitle, "type": "spawn", "tier": None}
                    todo.append(cid)
        except sqlite3.Error:
            pass
    events.sort(key=lambda e: e["ts"])
    return {"session": sid or tid, "harness": "codex", "title": title, "events": events,
            "agents": agents, "path": path}


# ---------- antigravity adapter ----------
# Antigravity encrypts its conversation logs (~/.gemini/antigravity*/conversations/*.pb), so no
# per-request tokens are readable. conversation_summaries.db is plain: title, workspace_uris,
# step_count, last_modified_time, last_user_input_time, parent_conversation_id, and raw_summary
# (protobuf; field 7 = created time). This adapter reports wall time, a rough agent/human split,
# steps and subagents; tokens and cost are marked not recorded.

def antigravity_dbs():
    root = os.environ.get("ANTIGRAVITY_HOME") or os.path.expanduser("~/.gemini")
    return sorted(glob.glob(os.path.join(root, "antigravity*", "conversation_summaries.db")))


def pb_varint(b, i):
    r = shift = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << shift
        shift += 7
        if x < 0x80:
            return r, i


def pb_timestamp_field(b, want):
    """Seconds of a google.protobuf.Timestamp at top-level field `want`, else None."""
    i = 0
    try:
        while i < len(b):
            key, i = pb_varint(b, i)
            field, wire = key >> 3, key & 7
            if wire == 0:
                _, i = pb_varint(b, i)
            elif wire == 2:
                n, i = pb_varint(b, i)
                sub = b[i:i + n]
                i += n
                if field == want and sub[:1] == b"\x08":
                    return float(pb_varint(sub, 1)[0])
            elif wire == 1:
                i += 8
            elif wire == 5:
                i += 4
            else:
                return None
    except IndexError:
        return None
    return None


def sql_ts(v):
    return parse_ts(str(v).replace(" ", "T")) if v else None


def ag_rows(where, params):
    out = []
    for db in antigravity_dbs():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            con.execute("SELECT 1 FROM conversation_summaries LIMIT 1")
        except sqlite3.Error:
            # WAL database with no -shm file yet: read-only mode cannot create it
            con = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
        try:
            for r in con.execute("SELECT conversation_id, title, preview, step_count, last_modified_time, "
                                 "last_user_input_time, workspace_uris, parent_conversation_id, raw_summary, "
                                 f"agent_name FROM conversation_summaries WHERE {where}", params):
                out.append(dict(zip(("id", "title", "preview", "steps", "modified", "user_input", "workspaces",
                                     "parent", "raw", "agent"), r), db=db))
        except sqlite3.Error:
            pass
    best = {}
    for r in out:
        if r["id"] not in best or str(r["modified"]) > str(best[r["id"]]["modified"]):
            best[r["id"]] = r
    return list(best.values())


def ag_events(r, src):
    raw = r["raw"] if isinstance(r["raw"], (bytes, bytearray)) else b""
    created = pb_timestamp_field(raw, 7)
    user_in, modified = sql_ts(r["user_input"]), sql_ts(r["modified"])
    ev = []
    if created:
        ev.append({"ts": created, "src": src, "kind": "human"})
    if user_in and user_in != created:
        ev.append({"ts": user_in, "src": src, "kind": "human" if src == "main" else "result"})
    if modified:
        ev.append({"ts": modified, "src": src, "kind": "result"})
    return ev


AG_WRITE_TOOLS = ("write_to_file", "replace_file_content", "multi_replace_file_content", "edit_file")


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def ag_hub_servers():
    """Antigravity 2.x runs an `agy --hub` server that writes <root>/<app_data_dir>/daemon/hub_<port>.json
    ({pid, httpPort, httpsPort, csrfToken}). Same RPC service as the old language_server."""
    root = os.environ.get("ANTIGRAVITY_HOME") or os.path.expanduser("~/.gemini")
    out = []
    for f in sorted(glob.glob(os.path.join(root, "antigravity*", "daemon", "hub_*.json"))):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if not d.get("csrfToken") or not pid_alive(d.get("pid")):
            continue
        bases = [f"http://127.0.0.1:{d['httpPort']}"] if d.get("httpPort") else []
        bases += [f"https://127.0.0.1:{d['httpsPort']}"] if d.get("httpsPort") else []
        if bases:
            out.append((os.path.basename(os.path.dirname(os.path.dirname(f))), d["csrfToken"], bases))
    return out


def ag_language_servers():
    """Running Antigravity servers: [(app_data_dir, csrf token, [base urls])]. From the hub daemon
    files first, else from the process arguments of a `language_server` (1.x) or `agy` (2.x)
    process plus its listening ports. The token is only ever sent to 127.0.0.1."""
    out = ag_hub_servers()
    seen = {t for _, t, _ in out}
    try:
        ps = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return out
    for line in ps.splitlines():
        if "--csrf_token" not in line or not ("language_server" in line or "/agy " in line):
            continue
        pid = line.split(None, 1)[0]
        tok = re.search(r"--csrf_token[ =](\S+)", line)
        app = re.search(r"--app_data_dir[ =](\S+)", line)
        if not tok or tok.group(1) in seen:
            continue
        try:
            ls = subprocess.run(["lsof", "-nP", "-a", "-p", pid, "-iTCP", "-sTCP:LISTEN"],
                                capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        ports = re.findall(r"127\.0\.0\.1:(\d+)", ls)
        bases = [f"{sch}://127.0.0.1:{p}" for p in ports for sch in ("https", "http")]
        if bases:
            out.append((app.group(1) if app else "", tok.group(1), bases))
    return out


def ag_rpc(method, body, app_dir):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    servers = sorted(ag_language_servers(), key=lambda x: x[0] != app_dir)
    for _, tok, bases in servers:
        for base in bases:
            req = urllib.request.Request(
                f"{base}/exa.language_server_pb.LanguageServerService/{method}",
                data=json.dumps(body).encode(), method="POST",
                headers={"Content-Type": "application/json", "Connect-Protocol-Version": "1",
                         "X-Codeium-Csrf-Token": tok})
            try:
                with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                    data = json.loads(resp.read())
            except (OSError, ValueError):
                continue
            if data:
                return data
    return None


def dur_s(v):
    m = re.match(r"([\d.]+)s$", str(v or ""))
    return float(m.group(1)) if m else 0.0


def ag_live_events(traj, src, models=None):
    """Events from a GetCascadeTrajectory response: one request per generatorMetadata entry."""
    models = models or {}
    steps = traj.get("steps") or []
    events = []
    for st in steps:
        md = st.get("metadata") or {}
        ts = parse_ts(md.get("createdAt"))
        if ts is None:
            continue
        kind = st.get("type", "")
        if kind.endswith("USER_INPUT"):
            events.append({"ts": ts, "src": src, "kind": "human" if src == "main" else "result"})
        elif md.get("toolCall"):
            done = parse_ts(md.get("completedAt")) or ts
            events.append({"ts": done, "src": src, "kind": "result"})
    for g in traj.get("generatorMetadata") or []:
        cm = g.get("chatModel") or {}
        u = cm.get("usage") or {}
        idx = [i for i in g.get("stepIndices") or [] if i < len(steps)]
        mds = [(steps[i].get("metadata") or {}) for i in idx]
        start = min((parse_ts(m.get("createdAt")) for m in mds if m.get("createdAt")), default=None)
        if start is None:
            continue
        end = max((parse_ts(m.get("finishedGeneratingAt") or m.get("createdAt")) for m in mds), default=start)
        end = max(end, start + dur_s(cm.get("timeToFirstToken")) + dur_s(cm.get("streamingDuration")))
        tools = []
        for m in mds:
            tc = m.get("toolCall") or {}
            if not tc.get("name"):
                continue
            fp = None
            if tc["name"] in AG_WRITE_TOOLS:
                try:
                    a = json.loads(tc.get("argumentsJson") or "{}")
                    fp = a.get("TargetFile") or a.get("AbsolutePath") or a.get("FilePath")
                except ValueError:
                    fp = None
            tools.append((tc["name"], fp))
        credits = {}
        for c in cm.get("consumedCredits") or []:
            credits[c.get("creditType") or "?"] = credits.get(c.get("creditType") or "?", 0) + float(c.get("creditAmount") or 0)
        placeholder = u.get("model") or cm.get("model")
        mapped, effort = models.get(placeholder, (None, None))
        events.append({
            "ts": start, "end": end, "src": src, "kind": "request",
            "model": cm.get("responseModel") or mapped or placeholder,
            "usage": {"input": int(u.get("inputTokens") or 0), "cache_write_5m": int(u.get("cacheWriteTokens") or 0),
                      "cache_write_1h": 0, "cache_read": int(u.get("cacheReadTokens") or 0),
                      "output": int(u.get("outputTokens") or 0) + int(u.get("thinkingOutputTokens") or 0), "web": 0},
            "speed": None, "req_ts": start, "effort": effort, "thinking_ms": 0, "tools": tools,
            "credits": credits, "provider": u.get("apiProvider"),
        })
    return events


def ag_model_map(app_dir):
    """MODEL_PLACEHOLDER_Mnnn -> (model id, effort) from the server's model config labels,
    e.g. "Claude Opus 5.5 (Medium)" -> ("claude-opus-5-5", "medium")."""
    data = ag_rpc("GetCascadeModelConfigData", {}, app_dir) or {}
    out = {}

    def walk(o):
        if isinstance(o, dict):
            mid = (o.get("modelOrAlias") or {}).get("model")
            if mid and o.get("label"):
                m = re.match(r"(.*?)\s*(?:\((\w+)\))?$", o["label"])
                out[mid] = (re.sub(r"[\s.]+", "-", m.group(1).strip().lower()), (m.group(2) or "").lower() or None)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    return out


def ag_fetch(cid, app_dir):
    if os.environ.get("THREAD_USAGE_NO_LIVE"):
        return None
    data = ag_rpc("GetCascadeTrajectory", {"cascadeId": cid}, app_dir)
    return (data or {}).get("trajectory")


def load_antigravity(cid):
    rows = ag_rows("conversation_id=?", (cid,))
    if not rows:
        return None
    r = rows[0]
    app_dir = os.path.basename(os.path.dirname(r["db"]))
    traj = ag_fetch(cid, app_dir)
    if traj:
        models = ag_model_map(app_dir)
        events, agents, todo = ag_live_events(traj, "main", models), {}, [cid]
        while todo:
            parent = todo.pop()
            for c in ag_rows("parent_conversation_id=?", (parent,)):
                agents[c["id"]] = {"desc": c["title"] or c["preview"], "type": c["agent"] or "subagent", "tier": None}
                ct = ag_fetch(c["id"], app_dir)
                events.extend(ag_live_events(ct, f"agent:{c['id']}", models) if ct else ag_events(c, f"agent:{c['id']}"))
                todo.append(c["id"])
        events.sort(key=lambda e: e["ts"])
        return {"session": cid, "harness": "antigravity", "title": r["title"] or r["preview"], "events": events,
                "agents": agents, "path": "language server (live)"}
    events, agents, steps, todo = ag_events(r, "main"), {}, {"main": r["steps"] or 0}, [cid]
    while todo:
        parent = todo.pop()
        for c in ag_rows("parent_conversation_id=?", (parent,)):
            agents[c["id"]] = {"desc": c["title"] or c["preview"], "type": c["agent"] or "subagent", "tier": None}
            events.extend(ag_events(c, f"agent:{c['id']}"))
            steps[c["id"]] = c["steps"] or 0
            todo.append(c["id"])
    events.sort(key=lambda e: e["ts"])
    return {"session": cid, "harness": "antigravity", "title": r["title"] or r["preview"], "events": events,
            "agents": agents, "path": r["db"], "tokens_recorded": False, "steps": steps}


def antigravity_current():
    cid = os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
    if cid:
        return cid
    cwd = os.getcwd()
    rows = sorted(ag_rows("(parent_conversation_id IS NULL OR parent_conversation_id='')", ()),
                  key=lambda r: str(r["modified"]), reverse=True)
    for r in rows:
        for uri in json.loads(r["workspaces"] or "[]"):
            d = strip_prefix(uri, "file://").rstrip("/")
            if d and (cwd == d or cwd.startswith(d + "/")):
                return r["id"]
    return rows[0]["id"] if rows else None


def load_claude_session(sid):
    path = find_transcript(sid)
    return load_claude_code(path) if path else None


ADAPTERS = {"claude-code": load_claude_session, "opencode": load_opencode, "codex": load_codex,
            "antigravity": load_antigravity}
CURRENT = {"claude-code": lambda: os.environ.get("CLAUDE_CODE_SESSION_ID"),
           "opencode": lambda: os.environ.get("OPENCODE_SESSION_ID") or opencode_current(),
           "codex": codex_current, "antigravity": antigravity_current}


def detect_harness():
    """Which harness is running this script, from its environment."""
    if os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDECODE"):
        return "claude-code"
    if any(k.startswith("CODEX_") for k in os.environ) and not os.environ.get("OPENCODE"):
        return "codex"
    if os.environ.get("OPENCODE") or any(k.startswith("OPENCODE_") for k in os.environ):
        return "opencode"
    if any(k.startswith("ANTIGRAVITY") for k in os.environ) or "antigravity" in os.environ.get("TERM_PROGRAM", "").lower():
        return "antigravity"
    return None


def harness_for_session(sid):
    if sid.startswith("ses_"):
        return "opencode"
    if find_transcript(sid):
        return "claude-code"
    if codex_find(sid):
        return "codex"
    if ag_rows("conversation_id=?", (sid,)):
        return "antigravity"
    return None


# ---------- metrics ----------

def empty_tokens():
    return {"input": 0, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0, "output": 0}


def add_tokens(acc, u):
    for k in acc:
        acc[k] += u.get(k, 0)


def finish_tokens(t):
    t["new"] = t["input"] + t["cache_write_5m"] + t["cache_write_1h"] + t["output"]
    t["processed"] = t["new"] + t["cache_read"]
    return t


def main_time(main_events, idle_s):
    """Split main-thread time: gap before a human prompt (or an AskUserQuestion answer) is human
    time capped at idle_s, gap before a task notification is background wait, the rest is agent."""
    pts = sorted((((e["end"] if e["kind"] == "request" else e["ts"]), e) for e in main_events
                  if e["kind"] in ("request", "result", "human", "notify", "compact")), key=lambda p: p[0])
    agent = human = bg = 0.0
    asked = False
    for (ta, a), (tb, b) in zip(pts, pts[1:]):
        gap = max(0.0, tb - ta)
        if a["kind"] == "request":
            asked = any(n in ASK_TOOLS for n, _ in a.get("tools") or [])
        if b["kind"] == "human" or (b["kind"] == "result" and asked):
            human += min(gap, idle_s)
        elif b["kind"] == "notify":
            bg += gap
        else:
            agent += gap
    return agent, human, bg


def finish_last_request(last_req, prices):
    """Newest main-thread request: when it ended, how big its context was, and the extra cost of
    re-writing that context to a cold 1h cache instead of reading it warm."""
    if not last_req:
        return None
    p = prices.lookup(last_req["model"], last_req.get("end"))
    rewrite = None
    if p:
        w1h = p.get("cache_write_1h", p["input"] * p.get("cache_write_mult", prices.w1h))
        rewrite = round(last_req["context"] * (w1h - p["cache_read"]) / 1_000_000, 4)
    return {"end": last_req["end"], "context": last_req["context"], "rewrite_extra_usd": rewrite}


def compute(data, prices, t_from, t_to, idle_s):
    """Metrics for events with t_from < ts <= t_to (t_from None = top of thread)."""
    lo = -1e18 if t_from is None else t_from
    ev = [e for e in data["events"] if lo < e["ts"] <= t_to]
    main = [e for e in ev if e["src"] == "main"]
    agent_s, human_s, bg_s = main_time(main, idle_s)
    tokens, by_model, tools, files = empty_tokens(), {}, {}, []
    cost, unpriced, peak, model_s, think_ms, web, reported = 0.0, 0, 0, 0.0, 0, 0, 0.0
    efforts, subs, compactions, credits = {}, {}, [], {}
    last_req = None
    for e in ev:
        if e["kind"] == "compact":
            compactions.append({"pre": e.get("pre"), "post": e.get("post"), "trigger": e.get("trigger")})
        if e["kind"] != "request":
            continue
        u = e["usage"]
        add_tokens(tokens, u)
        for ck, cv in (e.get("credits") or {}).items():
            credits[ck] = credits.get(ck, 0) + cv
        web += u.get("web", 0)
        c = prices.cost(e["model"], u, e.get("speed"), e.get("ts"))
        reported += e.get("cost_reported") or 0.0
        if c is None and e.get("cost_reported"):
            c = e["cost_reported"]
        if c is None:
            unpriced += 1
        else:
            cost += c
        mm = by_model.setdefault(short_model(e["model"]), {"requests": 0, "cost_usd": 0.0, **empty_tokens()})
        mm["requests"] += 1
        add_tokens(mm, u)
        mm["cost_usd"] += c or 0.0
        prompt = u["input"] + u["cache_read"] + u["cache_write_5m"] + u["cache_write_1h"]
        think_ms += e.get("thinking_ms") or 0
        if e["src"] == "main":
            peak = max(peak, prompt)
            if last_req is None or e["end"] >= last_req["end"]:
                last_req = {"end": e["end"], "context": prompt, "model": e["model"]}
            if e.get("req_ts"):
                model_s += max(0.0, e["end"] - e["req_ts"])
            if e.get("effort"):
                efforts[e["effort"]] = efforts.get(e["effort"], 0) + 1
        else:
            aid = e["src"].split(":", 1)[1]
            s = subs.setdefault(aid, {"id": aid, "model": e["model"], "requests": 0, "cost_usd": 0.0,
                                      "tokens_new": 0, "first": e["ts"], "last": e["end"],
                                      **{k: data["agents"].get(aid, {}).get(k) for k in ("desc", "type")}})
            s["requests"] += 1
            s["cost_usd"] += c or 0.0
            s["tokens_new"] += u["input"] + u["cache_write_5m"] + u["cache_write_1h"] + u["output"]
            s["last"] = max(s["last"], e["end"])
        for name, fp in e.get("tools") or []:
            tools[name] = tools.get(name, 0) + 1
            if name in FILE_TOOLS and fp and fp not in files:
                files.append(fp)
    sub_list = sorted(subs.values(), key=lambda s: -s["cost_usd"])
    for s in sub_list:
        s["active_s"] = round(s.pop("last") - s.pop("first"), 1)
        s["cost_usd"] = round(s["cost_usd"], 4)
    sub_by_model = {}
    for s in sub_list:
        b = sub_by_model.setdefault(s["model"] or "?", {"count": 0, "cost_usd": 0.0})
        b["count"] += 1
        b["cost_usd"] = round(b["cost_usd"] + s["cost_usd"], 4)
    for mm in by_model.values():
        mm["cost_usd"] = round(mm["cost_usd"], 4)
    first = min((e["ts"] for e in ev), default=t_to)
    last = max((e.get("end", e["ts"]) for e in ev), default=t_to)
    return {
        "wall_s": round(last - first, 1),
        "active_s": round(agent_s + human_s, 1),
        "agent_s": round(agent_s, 1),
        "human_s": round(human_s, 1),
        "bg_wait_s": round(bg_s, 1),
        "model_s": round(model_s, 1),
        "thinking_s": round(think_ms / 1000, 1),
        "prompts": sum(1 for e in main if e["kind"] == "human"),
        "requests": sum(1 for e in ev if e["kind"] == "request"),
        "requests_main": sum(1 for e in main if e["kind"] == "request"),
        "tokens": finish_tokens(tokens),
        "cost_usd": round(cost, 4),
        "cost_reported_usd": round(reported, 4),
        "credits": credits,
        "unpriced_requests": unpriced,
        "peak_context": peak,
        "last_request": finish_last_request(last_req, prices),
        "by_model": by_model,
        "efforts": efforts,
        "web_requests": web,
        "compactions": compactions,
        "tools": dict(sorted(tools.items(), key=lambda kv: -kv[1])),
        "files_touched": len(files),
        "files": files[:FILES_KEPT],
        "subagents": {"count": len(sub_list), "cost_usd": round(sum(s["cost_usd"] for s in sub_list), 4),
                      "agent_s": round(sum(s["active_s"] for s in sub_list), 1),
                      "by_model": sub_by_model, "list": sub_list},
    }


# ---------- ledger ----------

def ledger_path(ledger_dir, session):
    return os.path.join(ledger_dir, f"{session}.jsonl")


def read_ledger(path):
    return list(read_jsonl(path)) if os.path.exists(path) else []


def all_rows(ledger_dir):
    rows = []
    for f in sorted(glob.glob(os.path.join(ledger_dir, "*.jsonl"))):
        rows.extend(read_ledger(f))
    rows.sort(key=lambda r: r.get("to") or "")
    return rows


_DOCS_ROOT = []


def docs_root():
    """Where relative doc paths resolve: env, config file, git top level of the cwd, else the cwd."""
    if not _DOCS_ROOT:
        import thread_hooks_common as common
        root = common.cfg_str("THREAD_USAGE_DOCS_ROOT", "thread_usage_docs_root")
        if not root:
            try:
                out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                                     text=True, timeout=10)
                root = out.stdout.strip() if out.returncode == 0 else None
            except (OSError, subprocess.SubprocessError):
                root = None
        _DOCS_ROOT.append(os.path.realpath(os.path.expanduser(root or os.getcwd())))
    return _DOCS_ROOT[0]


def default_ledger():
    import thread_hooks_common as common
    d = common.cfg_str("THREAD_USAGE_LEDGER_DIR", "thread_usage_ledger_dir")
    return os.path.expanduser(d) if d else common.state_dir("usage-ledger")


def rel_to_root(p):
    full, root = os.path.realpath(p), docs_root()
    return os.path.relpath(full, root) if full.startswith(root + os.sep) else p


def rel_doc(p):
    return rel_to_root(os.path.abspath(p))


def sum_rows(rows):
    acc = {"snapshots": len(rows), "sessions": len({r["session"] for r in rows}), "active_s": 0.0,
           "wall_s": 0.0, "prompts": 0, "requests": 0, "cost_usd": 0.0, "tokens_new": 0,
           "cache_read": 0, "subagents": 0}
    for r in rows:
        d = r["delta"]
        acc["active_s"] += d["active_s"]
        acc["wall_s"] += d["wall_s"]
        acc["prompts"] += d["prompts"]
        acc["requests"] += d["requests"]
        acc["cost_usd"] += d["cost_usd"]
        acc["tokens_new"] += d["tokens"]["new"]
        acc["cache_read"] += d["tokens"]["cache_read"]
        acc["subagents"] += d["subagents"]["count"]
    acc["cost_usd"] = round(acc["cost_usd"], 4)
    return acc


# ---------- rendering ----------

def short_model(m):
    m = (m or "?").split("/")[-1]
    m = re.sub(r"^(?:[a-z]{2,6}\.)?anthropic\.", "", m)
    return strip_prefix(m, "claude-")


def row_cells(m):
    t = m["tokens"]
    if m.get("tokens_recorded") is False:
        na = "n/a"
        return [na, fmt_dur(m["wall_s"]), na, f"{m.get('steps', 0)} st", na, na, na, na]
    return [fmt_dur(m["active_s"]), fmt_dur(m["wall_s"]), str(m["prompts"]), str(m["requests"]),
            fmt_tok(t["new"]), fmt_tok(t["cache_read"]), fmt_tok(m["peak_context"]), fmt_cost(m["cost_usd"])]


def model_line(m):
    parts = [f"{short_model(k)} {fmt_cost(v['cost_usd'])} ({v['requests']} req)"
             for k, v in sorted(m["by_model"].items(), key=lambda kv: -kv[1]["cost_usd"])]
    return ", ".join(parts) or "none"


def detail_lines(m):
    if m.get("tokens_recorded") is False:
        out = [f"steps: {m.get('steps', 0)}; tokens and cost not recorded locally by this harness"]
        if m["subagents"]["count"]:
            out.append(f"subagents: {m['subagents']['count']}")
        out.append(f"time: wall {fmt_dur(m['wall_s'])}; last input to last change {fmt_dur(m['agent_s'])} "
                   "(only start, last input and last change are recorded)")
        return out
    out = [f"models: {model_line(m)}"]
    s = m["subagents"]
    if s["count"]:
        mix = ", ".join(f"{short_model(k)} x{v['count']}" for k, v in s["by_model"].items())
        out.append(f"subagents: {s['count']} ({mix}) {fmt_cost(s['cost_usd'])}, {fmt_dur(s['agent_s'])} agent time")
    out.append(f"time: agent {fmt_dur(m['agent_s'])} (model {fmt_dur(m['model_s'])}), "
               f"human {fmt_dur(m['human_s'])}, bg wait {fmt_dur(m['bg_wait_s'])}")
    if m["tools"]:
        out.append("tools: " + ", ".join(f"{k} {v}" for k, v in list(m["tools"].items())[:TOOLS_SHOWN]))
    if m["compactions"]:
        out.append("compactions: " + ", ".join(f"{fmt_tok(c['pre'])}->{fmt_tok(c['post'])}" for c in m["compactions"]))
    if m["files_touched"]:
        out.append(f"files touched: {m['files_touched']}")
    if m.get("credits"):
        out.append("credits used: " + ", ".join(f"{k} {v:g}" for k, v in m["credits"].items()))
    if m.get("cost_reported_usd"):
        out.append(f"harness-reported cost: {fmt_cost(m['cost_reported_usd'])} (provider rates; est. uses list prices)")
    if m["unpriced_requests"]:
        out.append(f"unpriced requests: {m['unpriced_requests']} (model missing from model_prices.json)")
    return out


HEAD = ["active", "wall", "prompts", "reqs", "new tok", "cache rd", "peak ctx", "est $"]


def print_report(data, rows, delta, total, delta_label):
    print(f"thread_usage  {data['harness']}  session {data['session'][:8]}  \"{data['title'] or ''}\"")
    w = max(len(delta_label), 20)
    print(f"{'window':<{w}}  " + "  ".join(f"{h:>8}" for h in HEAD))
    for label, m in ((delta_label, delta), ("thread (since start)", total)):
        print(f"{label:<{w}}  " + "  ".join(f"{c:>8}" for c in row_cells(m)))
    print()
    for line in detail_lines(total):
        print("  " + line)
    if rows:
        print(f"  snapshots in this session: {len(rows)}, last {rows[-1].get('to')}")


def md_row(label, m):
    return "| " + " | ".join([label, *row_cells(m)]) + " |"


def agg_row(label, a):
    return (f"| {label} | {fmt_dur(a['active_s'])} | {fmt_dur(a['wall_s'])} | {a['prompts']} | "
            f"{a['requests']} | {fmt_tok(a['tokens_new'])} | {fmt_tok(a['cache_read'])} | - | "
            f"{fmt_cost(a['cost_usd'])} |")


def render_block(doc, rows_for_doc, last, prices_as_of, thread_rows=()):
    d, t = last["delta"], last["total"]
    since = f"since #{last['snap'] - 1}" if last["snap"] > 1 else "since start"
    when = local_stamp(parse_ts(last["to"]))
    agg = sum_rows(rows_for_doc)
    lines = [
        BLOCK_BEGIN,
        "## Usage",
        "",
        f"_Agent time, tokens and cost estimated at API list prices ({prices_as_of}) by "
        f"`thread_usage.py`; replaced on each capture._",
        "",
        "| Window | Active | Wall | Prompts | Requests | New tokens | Cache read | Peak ctx | Est. cost |",
        "|---|---|---|---|---|---|---|---|---|",
        md_row(f"Last snapshot #{last['snap']} ({when}, {since})", d),
        md_row(f"Thread to here (session `{last['session'][:8]}`, since start)", t),
        agg_row(f"This doc, all {agg['snapshots']} snapshot(s) in {agg['sessions']} session(s)", agg),
    ]
    th = sum_rows(thread_rows) if thread_rows else None
    if th and th["sessions"] > 1:
        lines.append(agg_row(f"Thread `{last['thread']}`, all {th['sessions']} sessions", th))
    lines.append("")
    lines += [f"- {x}" for x in detail_lines(t)]
    if last.get("thread"):
        lines.append(f"- thread: `{last['thread']}`")
    lines.append(BLOCK_END)
    return "\n".join(lines)


def stamp_doc(path, block):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    pat = re.compile(r"^" + re.escape(BLOCK_BEGIN) + r"[ \t]*$.*?^" + re.escape(BLOCK_END) + r"[ \t]*$",
                     re.S | re.M)
    if pat.search(text):
        new = pat.sub(lambda _: block, text, count=1)
    else:
        new = text.rstrip("\n") + "\n\n" + block + "\n"
    if new != text:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
    return new != text


def stamp_from_ledger(doc, ledger_dir, prices_as_of):
    rd = rel_doc(doc)
    every = all_rows(ledger_dir)
    rows = [r for r in every if rd in (r.get("docs") or [])]
    if not rows:
        return False
    last = rows[-1]
    thread_rows = [r for r in every if last.get("thread") and r.get("thread") == last["thread"]]
    return stamp_doc(doc, render_block(rd, rows, last, last.get("prices_as_of") or prices_as_of, thread_rows))


# ---------- commands ----------

def fail(msg):
    print(f"thread_usage: {msg}", file=sys.stderr)
    raise SystemExit(2)


def load(args):
    if args.transcript:
        if not os.path.exists(args.transcript):
            fail(f"transcript not found: {args.transcript}")
        if args.harness == "codex" or os.path.basename(args.transcript).startswith("rollout-"):
            ev, title, sid = codex_stream(args.transcript, "main")
            return {"session": sid or os.path.basename(args.transcript), "harness": "codex", "title": title,
                    "events": ev, "agents": {}, "path": args.transcript}
        return load_claude_code(args.transcript)
    harness = args.harness if args.harness != "auto" else None
    sid = args.session
    if sid and not harness:
        harness = harness_for_session(sid)
    harness = harness or detect_harness()
    if not harness:
        fail("cannot tell which harness this is; pass --harness claude-code|opencode|codex|antigravity")
    sid = sid or CURRENT[harness]()
    if not sid:
        fail(f"no current {harness} session found; pass --session")
    data = ADAPTERS[harness](sid)
    if data is None:
        fail(f"{harness} session not found: {sid}")
    return data


def windows(args, data, prices):
    rows = read_ledger(ledger_path(args.ledger_dir, data["session"]))
    now = time.time()
    t_from = parse_ts(rows[-1]["to"]) if rows else None
    idle = args.idle * 60
    delta = compute(data, prices, t_from, now, idle)
    total = compute(data, prices, None, now, idle)
    if data.get("tokens_recorded") is False:
        steps = sum(data.get("steps", {}).values())
        prev = (rows[-1].get("total") or {}).get("steps") or 0 if rows else 0
        for m, n in ((total, steps), (delta, max(0, steps - prev))):
            m["tokens_recorded"] = False
            m["steps"] = n
            m["cost_usd"] = 0.0
            m["prompts"] = 0
        lo = -1e18 if t_from is None else t_from
        for m, a in ((total, -1e18), (delta, lo)):
            ids = {e["src"] for e in data["events"] if e["src"] != "main" and a < e["ts"] <= now}
            m["subagents"]["count"] = len(ids)
    label = f"since snapshot #{len(rows)}" if rows else "since start"
    return rows, now, t_from, delta, total, label


def cmd_report(args, prices):
    data = load(args)
    rows, now, t_from, delta, total, label = windows(args, data, prices)
    if args.json:
        print(json.dumps({"session": data["session"], "harness": data.get("harness"), "title": data["title"], "from": iso(t_from),
                          "to": iso(now), "delta": delta, "total": total, "snapshots": len(rows)}, indent=1))
    else:
        print_report(data, rows, delta, total, label)
    return 0


def cmd_snapshot(args, prices):
    data = load(args)
    rows, now, t_from, delta, total, label = windows(args, data, prices)
    docs = [rel_doc(d) for d in args.doc or []]
    missing = [d for d in args.doc or [] if not os.path.exists(d) and not os.path.exists(os.path.join(docs_root(), d))]
    if missing:
        print(f"thread_usage: doc not found: {', '.join(missing)}", file=sys.stderr)
        return 2
    prev_thread = rows[-1].get("thread") if rows else None
    row = {"v": 1, "snap": len(rows) + 1, "session": data["session"], "harness": data["harness"],
           "title": data["title"], "thread": args.thread or prev_thread or data["title"],
           "label": args.label, "docs": docs, "from": iso(t_from), "to": iso(now),
           "delta": delta, "total": total, "prices_as_of": prices.as_of}
    print_report(data, rows, delta, total, label)
    if args.dry_run:
        print("\n(dry run: ledger and docs unchanged)")
        return 0
    lp = ledger_path(args.ledger_dir, data["session"])
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    with open(lp, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")
    touched = [rel_to_root(lp)]
    if not args.no_stamp:
        for d in docs:
            full = d if os.path.isabs(d) else os.path.join(docs_root(), d)
            if stamp_from_ledger(full, args.ledger_dir, prices.as_of):
                touched.append(d)
    print(f"\nsnapshot #{row['snap']} written; commit: {' '.join(touched)}")
    return 0


def past_sessions(harness):
    """(harness, session id) for every root session a harness has on disk."""
    if harness in (None, "claude-code"):
        for f in glob.glob(os.path.join(claude_root(), "projects", "*", "*.jsonl")):
            yield "claude-code", os.path.basename(f)[:-6]
    if harness in (None, "opencode"):
        con = oc_connect()
        if con is not None:
            for (sid,) in con.execute("SELECT id FROM session WHERE parent_id IS NULL"):
                yield "opencode", sid
    if harness in (None, "codex"):
        for f in glob.glob(os.path.join(codex_home(), "sessions", "**", "rollout-*.jsonl"), recursive=True):
            m = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27})\.jsonl$", f)
            if m:
                yield "codex", m.group(1)
    if harness in (None, "antigravity"):
        for r in ag_rows("(parent_conversation_id IS NULL OR parent_conversation_id='')", ()):
            yield "antigravity", r["id"]


def root_docs(files):
    root = docs_root()
    out = set()
    for f in files:
        full = os.path.realpath(f)
        if full.startswith(root + os.sep) and full.endswith(".md"):
            out.add(os.path.relpath(full, root))
    return sorted(out)


def thread_of(title):
    """Group a series: "Search filters iter 12" -> "Search filters"."""
    t = re.sub(r"[\s,:(-]*\b(?:iter(?:ation)?|it)\s*#?\d+\)?\s*$", "", (title or "").strip(), flags=re.I)
    return t.strip() or title


def cmd_backfill(args, prices):
    """One ledger row per past session that has none yet: the whole session as one snapshot,
    docs = .md files under the docs root the session edited through its edit/write tools. Docs are not stamped."""
    harness = None if args.harness == "auto" else args.harness
    now, idle = time.time(), args.idle * 60
    done = {os.path.basename(f)[:-6] for f in glob.glob(os.path.join(args.ledger_dir, "*.jsonl"))}
    written, totals = 0, {}
    for h, sid in past_sessions(harness):
        if sid in done:
            continue
        data = ADAPTERS[h](sid)
        if not data:
            continue
        m = compute(data, prices, None, now, idle)
        if not m["requests"] and data.get("tokens_recorded") is not False:
            continue
        last = max((e.get("end", e["ts"]) for e in data["events"]), default=now)
        row = {"v": 1, "snap": 1, "backfill": True, "session": data["session"], "harness": h,
               "title": data["title"], "thread": thread_of(data["title"]) or sid, "label": "backfill",
               "docs": root_docs(m["files"]), "from": None, "to": iso(last), "delta": m, "total": m,
               "prices_as_of": prices.as_of}
        t = totals.setdefault(h, [0, 0.0, 0.0, 0])
        t[0] += 1
        t[1] += m["cost_usd"]
        t[2] += m["active_s"]
        t[3] += bool(row["docs"])
        written += 1
        if not args.dry_run:
            os.makedirs(args.ledger_dir, exist_ok=True)
            with open(ledger_path(args.ledger_dir, data["session"]), "w", encoding="utf-8") as f:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
    for h, (n, c, a, wd) in totals.items():
        print(f"{h:12} {n:4} sessions  est {fmt_cost(c):>10}  active {fmt_dur(a):>8}  with docs {wd}")
    print(f"{'(dry run) ' if args.dry_run else ''}{written} session rows {'would be ' if args.dry_run else ''}written to "
          f"{args.ledger_dir}")
    return 0


def cmd_stamp(args, prices):
    for d in args.doc or []:
        full = d if os.path.isabs(d) or os.path.exists(d) else os.path.join(docs_root(), d)
        changed = stamp_from_ledger(full, args.ledger_dir, prices.as_of)
        print(f"{'stamped' if changed else 'unchanged'}  {rel_doc(full)}")
    return 0


def cmd_ledger(args, prices):
    rows = all_rows(args.ledger_dir)
    if args.thread:
        rows = [r for r in rows if r.get("thread") == args.thread]
    if args.doc:
        rd = rel_doc(args.doc[0])
        rows = [r for r in rows if rd in (r.get("docs") or [])]
    groups = {}
    for r in rows:
        groups.setdefault(r.get("thread") or "?", []).append(r)
    out = {k: sum_rows(v) for k, v in groups.items()}
    if args.json:
        print(json.dumps({"threads": out, "total": sum_rows(rows)}, indent=1))
        return 0
    print(f"{'thread':<40} {'snaps':>5} {'sess':>4} {'active':>7} {'reqs':>5} {'new tok':>8} {'est $':>9}")
    for k, a in sorted(out.items(), key=lambda kv: -kv[1]["cost_usd"]):
        print(f"{k[:40]:<40} {a['snapshots']:>5} {a['sessions']:>4} {fmt_dur(a['active_s']):>7} "
              f"{a['requests']:>5} {fmt_tok(a['tokens_new']):>8} {fmt_cost(a['cost_usd']):>9}")
    t = sum_rows(rows)
    print(f"{'total':<40} {t['snapshots']:>5} {t['sessions']:>4} {fmt_dur(t['active_s']):>7} "
          f"{t['requests']:>5} {fmt_tok(t['tokens_new']):>8} {fmt_cost(t['cost_usd']):>9}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="thread_usage.py", description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="exit codes: 0 ok, 2 bad input")
    ap.add_argument("command", choices=["report", "snapshot", "ledger", "stamp", "backfill"])
    ap.add_argument("--session")
    ap.add_argument("--harness", default="auto", choices=["auto", "claude-code", "opencode", "codex", "antigravity"],
                    help="default: from the session id shape, else the environment")
    ap.add_argument("--transcript")
    ap.add_argument("--doc", action="append", help="doc this snapshot captured (repeatable)")
    ap.add_argument("--thread", help="thread/task label for grouping across sessions, e.g. my-feature")
    ap.add_argument("--label", help="free text for this snapshot, e.g. 'iter3 capture'")
    ap.add_argument("--idle", type=float, default=5.0, help="idle cap in minutes (default 5)")
    ap.add_argument("--prices", default=DEFAULT_PRICES)
    ap.add_argument("--ledger-dir", default=None)
    ap.add_argument("--no-stamp", action="store_true", help="snapshot: skip the doc Usage block")
    ap.add_argument("--dry-run", action="store_true", help="snapshot: compute and print only")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    args.ledger_dir = os.path.expanduser(args.ledger_dir) if args.ledger_dir else default_ledger()
    prices = Prices(args.prices)
    return {"report": cmd_report, "snapshot": cmd_snapshot, "ledger": cmd_ledger,
            "stamp": cmd_stamp, "backfill": cmd_backfill}[args.command](args, prices)


if __name__ == "__main__":
    sys.exit(main())
