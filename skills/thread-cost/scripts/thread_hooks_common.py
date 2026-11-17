"""Shared helpers for the thread cost / idle-capture hooks (an imported library module, not a script).

Config lookup order for every knob: environment variable, then the key in the thread-cost config
file, then the default. The config file is $THREAD_COST_CONFIG, else
%APPDATA%\\thread-cost\\config.env on Windows, else ${XDG_CONFIG_HOME:-~/.config}/thread-cost/config.env:
KEY=value lines, `#` comments, an optional `export ` prefix, optional quotes, `~` expanded. State
lives outside every repo, in <state root>/<name>/, where the state root is the first set of:
$THREAD_HOOKS_STATE_DIR, config `thread_hooks_state_dir`, else the per-user state dir
(%LOCALAPPDATA%\\thread-hooks on Windows, else ${XDG_STATE_HOME:-~/.local/state}/thread-hooks).
Tests point THREAD_HOOKS_STATE_DIR at a temp dir.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
THREAD_USAGE = os.path.join(HERE, "thread_usage.py")



def config_path():
    """The thread-cost config file (it need not exist)."""
    explicit = os.environ.get("THREAD_COST_CONFIG")
    if explicit:
        return os.path.expanduser(explicit)
    if os.name == "nt" and os.environ.get("APPDATA"):
        return os.path.join(os.environ["APPDATA"], "thread-cost", "config.env")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "thread-cost", "config.env")


def load_config(path=None):
    """KEY=value pairs from the config file; {} when it is missing or unreadable."""
    out = {}
    try:
        with open(path or config_path(), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, val = line.split("=", 1)
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        if val.startswith("~"):
            val = os.path.expanduser(val)
        out[key.strip()] = val
    return out


_CONFIG = {}


def _config():
    path = config_path()
    if path not in _CONFIG:
        _CONFIG[path] = load_config(path)
    return _CONFIG[path]


def cfg_str(env_key, cfg_key, default=None):
    """String knob: env, then the config file, then default (None when unset everywhere)."""
    raw = os.environ.get(env_key) if env_key else None
    if raw in (None, "") and cfg_key:
        raw = _config().get(cfg_key)
    return default if raw in (None, "") else str(raw)


def cfg(env_key, cfg_key, default):
    """Float knob: env, then the config file, then default."""
    raw = cfg_str(env_key, cfg_key)
    try:
        return float(raw) if raw not in (None, "") else float(default)
    except ValueError:
        return float(default)


def cfg_flag(env_key, cfg_key, default):
    raw = cfg_str(env_key, cfg_key)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def state_root():
    base = cfg_str("THREAD_HOOKS_STATE_DIR", "thread_hooks_state_dir")
    if base:
        return os.path.expanduser(base)
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return os.path.join(os.environ["LOCALAPPDATA"], "thread-hooks")
    xdg = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(xdg, "thread-hooks")


def state_dir(name):
    return os.path.join(state_root(), name)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def session_usage(session, timeout=30):
    """thread_usage.py report total for a session, or None."""
    out = subprocess.run([sys.executable, THREAD_USAGE, "report", "--json", "--session", session],
                         capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0 or not out.stdout.strip():
        return None
    report = json.loads(out.stdout)
    total = report.get("total") or {}
    return {
        "harness": report.get("harness"),
        "cost": float(total.get("cost_usd") or 0.0),
        "requests": int(total.get("requests") or 0),
        "peak_context": total.get("peak_context"),
        "last_request": total.get("last_request"),
        "tokens": total.get("tokens") or {},
        "by_model": total.get("by_model") or {},
    }


# The capture step is the part of the instruction that says where and how this setup records its
# work. Override it with THREAD_CAPTURE_STEP / config `thread_capture_step` (one line of text), or
# THREAD_CAPTURE_STEP_FILE / config `thread_capture_step_file` (a UTF-8 text file; whitespace is
# collapsed). The default works anywhere.
CAPTURE_STEP_DEFAULT = (
    "Write a handoff for the work in this thread where the project keeps its notes (a handoff or "
    "notes doc in the repo, else one the user names): what is done, the current state, decisions "
    "made, open questions and the next steps, so a fresh thread can continue from it alone. "
    "Commit it if it lives in a repo"
)


def capture_step():
    text = cfg_str("THREAD_CAPTURE_STEP", "thread_capture_step")
    if not text:
        path = cfg_str("THREAD_CAPTURE_STEP_FILE", "thread_capture_step_file")
        if path:
            try:
                with open(os.path.expanduser(path), encoding="utf-8") as f:
                    text = " ".join(f.read().split())
            except OSError:
                text = None
    return (text or CAPTURE_STEP_DEFAULT).strip().rstrip(".")


# One capture instruction for both triggers (long thread and idle thread).
# CAPTURE_MARK is fixed text in it; thread_idle_capture.py uses it to tell its own wake from a user prompt.
CAPTURE_MARK = "Capture now so the thread can be cleared."
CAPTURE_INSTRUCTION = (
    "{why}. Capture now so the thread can be cleared. Start no new feature work. {step}. Make no "
    "outward-facing writes in this step (no PRs, store, ticket or chat posts). End with one line "
    "for the user: if the handoff holds everything needed to continue, tell them to run `/clear` "
    "(or start a new conversation where there is no `/clear`) and continue from the handoff, "
    "naming its path; if a clear would lose state that is not captured (a background agent or CI "
    "watch still running, an uncommitted worktree, an answer still owed), tell them to run "
    "`/compact` instead and say why. The model cannot run `/clear` or `/compact` itself. Re-ask "
    "any question from the previous reply that is still open."
)


HANDOFF = os.path.join(HERE, "thread_handoff.py")
_END_MARK = "End with one line for the user:"


# Harnesses with no /clear or /compact: the user starts a new conversation instead, and there is no
# SessionStart hook to resume it, so the handoff path is what the user pastes into the new one.
NO_CLEAR = {"antigravity"}
NEW_CONVERSATION_END = (
    "End with one line for the user: if the handoff holds everything needed to continue, write "
    "exactly \"Captured -> <doc>. Start a new conversation and open it with that path.\"; if "
    "starting over would lose state that is not captured (a background agent or CI watch still "
    "running, an uncommitted worktree, an answer still owed), say so and why, and keep going here "
    "instead. The model cannot start a new conversation itself. Re-ask any question from the "
    "previous reply that is still open.")


def clear_phrase(harness=None):
    """How the user drops the thread and continues, for the given harness."""
    return "in a new conversation" if harness in NO_CLEAR else "after `/clear`"


def capture_reason(why, cwd=None, harness=None):
    """CAPTURE_INSTRUCTION for `why`; with `cwd`, also tells the model to record the handoff so
    thread_handoff.py's SessionStart hook resumes from it after /clear or /compact. A harness in
    NO_CLEAR gets the new-conversation ending and no SessionStart step (it has no such hook)."""
    text = CAPTURE_INSTRUCTION.format(why=why, step=capture_step())
    if harness in NO_CLEAR:
        return text.split(_END_MARK)[0] + NEW_CONVERSATION_END
    if not cwd:
        return text
    step = (f"Then record the handoff so the next session continues from it automatically: "
            f"`python3 {HANDOFF} set --cwd {cwd} <absolute path of the handoff doc>`. ")
    end = ("End with one line for the user: if the handoff holds everything needed to continue, "
           "write exactly \"Captured -> <doc>. Type /clear (or /compact) and I continue from "
           "it.\"; if a clear would lose state that is not captured (a background agent or CI "
           "watch still running, an uncommitted worktree, an answer still owed), tell them to "
           "run `/compact` instead and say why. The model cannot run `/clear` or `/compact` "
           "itself. Re-ask any question from the previous reply that is still open.")
    head = text.split(_END_MARK)[0]
    return head + step + end
