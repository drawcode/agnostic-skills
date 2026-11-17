#!/usr/bin/env python3
"""Install, check or remove the thread-cost hooks for Claude Code and Antigravity.

The hooks call the scripts in the folder holding this file, by absolute path, so install the
skill folder where it will stay before running this. Read-only unless --write or --uninstall.

  Claude Code   <claude home>/settings.json (default $CLAUDE_CONFIG_DIR, else ~/.claude):
                UserPromptSubmit  thread_idle_capture.py mark, thread_cost_alert.py
                Stop              thread_cost_alert.py, thread_idle_capture.py wait (asyncRewake)
                SessionStart      thread_handoff.py hook (matcher clear|compact)
                Existing hooks are kept; a thread-cost hook found with another path, timeout or
                flag is updated in place.
  Antigravity   <gemini home>/config/hooks.json (default ~/.gemini): one named hook "thread-cost"
                with PreInvocation and Stop through thread_hook_antigravity.py. Checked only when
                the gemini home exists, unless named with --harness.

Each file is backed up beside the original (timestamped) before its first change.

Usage:
  python3 thread_hooks_install.py                    # report
  python3 thread_hooks_install.py --write            # add or fix what is missing
  python3 thread_hooks_install.py --uninstall        # report what removal would take out
  python3 thread_hooks_install.py --uninstall --write   # remove the thread-cost hooks only
  python3 thread_hooks_install.py --harness claude-code --no-idle --write

Options:
  --harness NAME     claude-code | antigravity; repeatable (default: Claude Code, plus
                     Antigravity when its home exists)
  --claude-home DIR  Claude Code user dir
  --gemini-home DIR  Antigravity user dir
  --python CMD       interpreter in the hook commands (default: python3 when on PATH, else this
                     interpreter's full path)
  --no-idle          leave out the idle-wake hook (Stop, holds a background process up to 1h)
  --json             {"ok": bool, "items": [{"item", "state", "detail"}]}

Output: one line per item, OK | MISSING | WRONG | FIXED | PRESENT | REMOVED | BLOCKED, then a summary.
Exit codes: 0 everything wired (or removed), 1 something missing or blocked, 2 bad input.
"""
import argparse
import json
import os
import shlex
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AG_HOOK_NAME = "thread-cost"

CLAUDE_HOOKS = [
    {"event": "UserPromptSubmit", "script": "thread_idle_capture.py", "args": "mark", "timeout": 10, "idle": True},
    {"event": "UserPromptSubmit", "script": "thread_cost_alert.py", "args": "", "timeout": 30},
    {"event": "Stop", "script": "thread_cost_alert.py", "args": "", "timeout": 30},
    {"event": "Stop", "script": "thread_idle_capture.py", "args": "wait", "timeout": 3600, "asyncRewake": True, "idle": True},
    {"event": "SessionStart", "matcher": "clear|compact", "script": "thread_handoff.py", "args": "hook", "timeout": 10},
]
AG_HOOKS = {
    "PreInvocation": {"script": "thread_hook_antigravity.py", "args": "prompt", "timeout": 30},
    "Stop": {"script": "thread_hook_antigravity.py", "args": "stop", "timeout": 30},
}
OURS = {"thread_idle_capture.py", "thread_cost_alert.py", "thread_handoff.py", "thread_hook_antigravity.py"}


def quote(path):
    return f'"{path}"' if any(c in path for c in " \t'&()") else path


def command_for(py, spec):
    cmd = f"{py} {quote(os.path.join(HERE, spec['script']))}"
    return cmd + (f" {spec['args']}" if spec.get("args") else "")


def split(cmd):
    try:
        return shlex.split(cmd, posix=os.name != "nt")
    except ValueError:
        return cmd.split()


def script_of(cmd):
    """(basename, args) of the thread-cost script a command runs, else (None, None)."""
    tokens = split(cmd or "")
    for i, t in enumerate(tokens):
        name = os.path.basename(t.strip('"'))
        if name in OURS:
            return name, tokens[i + 1:]
    return None, None


def backup(path):
    if os.path.exists(path):
        shutil.copy2(path, f"{path}.bak-thread-cost-{time.strftime('%Y%m%d-%H%M%S')}")


def read_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return json.loads(text) if text.strip() else {}


def write_json(path, data):
    backup(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def claude_items(settings, py, specs, write, uninstall):
    try:
        data = read_json(settings)
    except (OSError, ValueError) as e:
        return [("claude settings.json", "BLOCKED", f"cannot read {settings}: {e}")]
    hooks = data.setdefault("hooks", {})
    items, changed = [], False
    if uninstall:
        for event, groups in list(hooks.items()):
            for g in groups:
                keep = [h for h in g.get("hooks", []) if script_of(h.get("command"))[0] is None]
                for h in g.get("hooks", []):
                    if h not in keep:
                        items.append((f"claude {event} {script_of(h['command'])[0]}", "REMOVED" if write else "PRESENT",
                                      h["command"]))
                if len(keep) != len(g.get("hooks", [])):
                    g["hooks"], changed = keep, True
            hooks[event] = [g for g in groups if g.get("hooks")]
            if not hooks[event]:
                del hooks[event]
        if not hooks:
            data.pop("hooks", None)
        if changed and write:
            write_json(settings, data)
        return items or [("claude settings.json", "OK", "no thread-cost hooks")]
    for spec in specs:
        event, matcher = spec["event"], spec.get("matcher", "")
        name = f"claude {event}{' [' + matcher + ']' if matcher else ''} {spec['script']} {spec['args']}".rstrip()
        want = {"type": "command", "command": command_for(py, spec), "timeout": spec["timeout"]}
        if spec.get("asyncRewake"):
            want["asyncRewake"] = True
        groups = hooks.setdefault(event, [])
        found = None
        for g in groups:
            if (g.get("matcher") or "") != matcher:
                continue
            for h in g.get("hooks", []):
                if script_of(h.get("command")) == (spec["script"], split(spec["args"])):
                    found = h
        if found is None:
            group = next((g for g in groups if (g.get("matcher") or "") == matcher), None)
            if group is None:
                group = {"matcher": matcher, "hooks": []} if matcher else {"hooks": []}
                groups.append(group)
            group.setdefault("hooks", []).append(want)
            items.append((name, "MISSING", want["command"]))
            changed = True
        elif {k: found.get(k) for k in want} != want:
            found.clear()
            found.update(want)
            items.append((name, "WRONG", f"path, timeout or flag differ; want {want['command']}"))
            changed = True
        else:
            items.append((name, "OK", found["command"]))
    if changed and write:
        write_json(settings, data)
        items = [(i, "FIXED" if s in ("MISSING", "WRONG") else s, d) for i, s, d in items]
    return items


def antigravity_items(root, py, write, uninstall):
    path = os.path.join(root, "config", "hooks.json")
    try:
        hooks = read_json(path)
    except (OSError, ValueError) as e:
        return [("antigravity hooks.json", "BLOCKED", f"cannot read {path}: {e}")]
    if uninstall:
        if AG_HOOK_NAME not in hooks:
            return [("antigravity hooks.json", "OK", f"no {AG_HOOK_NAME} hook")]
        del hooks[AG_HOOK_NAME]
        if write:
            write_json(path, hooks)
        return [("antigravity hooks.json", "REMOVED" if write else "PRESENT", AG_HOOK_NAME)]
    want = {ev: [{"type": "command", "command": command_for(py, s), "timeout": s["timeout"]}]
            for ev, s in AG_HOOKS.items()}
    if hooks.get(AG_HOOK_NAME) == want:
        return [("antigravity hooks.json", "OK", AG_HOOK_NAME)]
    state = "MISSING" if AG_HOOK_NAME not in hooks else "WRONG"
    hooks[AG_HOOK_NAME] = want
    if write:
        write_json(path, hooks)
        state = "FIXED"
    return [("antigravity hooks.json", state, f"{AG_HOOK_NAME}: {', '.join(want)}")]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--harness", action="append", choices=["claude-code", "antigravity"])
    ap.add_argument("--claude-home")
    ap.add_argument("--gemini-home")
    ap.add_argument("--python")
    ap.add_argument("--no-idle", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    missing = [s for s in OURS if not os.path.isfile(os.path.join(HERE, s))]
    if missing:
        sys.stderr.write(f"error: {', '.join(sorted(missing))} not beside this script in {HERE}\n")
        return 2
    home = os.path.expanduser("~")
    claude_home = os.path.expanduser(a.claude_home or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude"))
    gemini_home = os.path.expanduser(a.gemini_home or os.path.join(home, ".gemini"))
    py = a.python or ("python3" if shutil.which("python3") else quote(sys.executable))
    harnesses = a.harness or ["claude-code"] + (["antigravity"] if os.path.isdir(gemini_home) else [])
    specs = [s for s in CLAUDE_HOOKS if not (a.no_idle and s.get("idle"))]

    items = []
    if "claude-code" in harnesses:
        items += claude_items(os.path.join(claude_home, "settings.json"), py, specs, a.write, a.uninstall)
    if "antigravity" in harnesses:
        items += antigravity_items(gemini_home, py, a.write, a.uninstall)

    good = ("OK", "FIXED", "REMOVED")
    ok = all(s in good for _, s, _ in items)
    if a.json:
        print(json.dumps({"ok": ok, "items": [dict(item=i, state=s, detail=d) for i, s, d in items]}))
    else:
        for i, s, d in items:
            print(f"{s:8} {i:58} {d}")
        verb = "removed" if a.uninstall else "wired"
        print(verb if ok else f"NOT {verb}: run with" + (" --uninstall" if a.uninstall else "") + " --write")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
