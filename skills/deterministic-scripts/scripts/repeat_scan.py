#!/usr/bin/env python3
"""Find repeated shell commands in an agent thread or shell history -- candidates to script.

Reads commands from transcripts or history files, splits compound commands into steps
(on `&&`, `||`, `;`, `|` and newlines, outside quotes), normalizes each step (paths, shas,
numbers, quoted strings, UUIDs and URLs become placeholders), and groups equal forms. A form
that repeats is a candidate for a script; a repeated multi-step chain is a stronger one.

Run (<skill> is where the skill is installed):
  python3 <skill>/scripts/repeat_scan.py PATH [PATH ...] [--format auto|claude-code|codex|shell-history|lines]
                                         [--min-count 2] [--top 20] [--json]

PATH is a file or a directory (searched recursively for *.jsonl). There is no default: pass
where your harness keeps its history. Typical places:
  Claude Code   <home>/.claude/projects/<project>/<session>.jsonl
  Codex         <home>/.codex/sessions/<year>/<month>/<day>/rollout-*.jsonl
  zsh / bash    <home>/.zsh_history, <home>/.bash_history
  anything else a text file with one command per line (--format lines)

Formats:
  claude-code    JSONL; assistant messages whose content has tool_use blocks named Bash
  codex          JSONL rollouts; response_item entries with payload.type function_call
                 (name shell / exec_command / local_shell) or local_shell_call
  shell-history  zsh extended history (`: <ts>:<dur>;<cmd>`) or plain bash history
  lines          one command per line
  auto           per file: JSON lines are detected as claude-code or codex, `: <ts>:<dur>;`
                 lines as shell-history, anything else as lines

Score = count x steps (1 for a single step, N for an N-step chain): rough "commands saved".
Trivial steps (`cd`, `echo`, `export`, `set`, `true`) are ignored when grouping steps.

Exit codes: 0 scan done (even with zero candidates); 2 bad input (no PATH, unreadable PATH,
unknown format).
Output: --json -> {"sources","commands","steps","candidates":[...],"chains":[...]};
otherwise a compact table. Each entry: count, score, program, normalized form, first example
(200 chars). Progress and errors on stderr.
"""
import argparse
import collections
import json
import re
import sys
from pathlib import Path

FORMATS = ("auto", "claude-code", "codex", "shell-history", "lines")
TRIVIAL = {"cd", "echo", "export", "set", "true", "false", "pwd", "clear", ":", "exit"}
# programs that only reshape a pipe's output; not counted as steps of their own after a `|`
FILTERS = {"head", "tail", "grep", "wc", "sort", "uniq", "cut", "tr", "awk", "sed", "cat", "tee", "xargs", "less", "jq"}
SHELL_NAMES = {"bash", "sh", "zsh", "fish", "pwsh", "powershell", "cmd", "cmd.exe"}
ZSH_RE = re.compile(r"^: \d+:\d+;(.*)$")

NORMALIZERS = [
    (re.compile(r"\bhttps?://\S+"), "<url>"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<uuid>"),
    (re.compile(r"\"(?:[^\"\\]|\\.)*\"|'[^']*'"), "<str>"),
    (re.compile(r"(?<![\w/.-])(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![\w-])"), "<sha>"),
    (re.compile(r"(?<![\w<])(?:~|\.{1,2})?/[^\s<>|;&]*|(?<![\w<])[A-Za-z]:\\[^\s]*"), "<path>"),
    (re.compile(r"(?<![\w<-])\d+(?:\.\d+)*(?![\w>])"), "<n>"),
]


def split_steps(cmd):
    """Split on && || ; | and newlines outside quotes into (separator-before, step) pairs. A heredoc marker (`<<EOF`) ends the command; its body is dropped."""
    cmd = cmd.replace("\r", "")
    steps, buf, quote, i, n, sep = [], [], None, 0, len(cmd), ""
    while i < n:
        ch = cmd[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < n:
                buf.append(cmd[i + 1]); i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch; buf.append(ch)
        elif ch == "\\" and i + 1 < n:
            buf.append(ch); buf.append(cmd[i + 1]); i += 1
        elif cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            # heredoc: keep the rest of this line, drop the body
            break
        elif ch in "\n;|" or cmd.startswith("&&", i):
            steps.append((sep, "".join(buf)))
            buf = []
            sep = "|" if ch == "|" and not cmd.startswith("||", i) else "&&"
            if cmd.startswith("&&", i) or cmd.startswith("||", i):
                i += 1
        else:
            buf.append(ch)
        i += 1
    steps.append((sep, "".join(buf)))
    return [(sp, t.strip()) for sp, t in steps if t.strip() and not t.strip().startswith("#")]


def normalize(step):
    out = step
    for rx, rep in NORMALIZERS:
        out = rx.sub(rep, out)
    return re.sub(r"\s+", " ", out).strip()


def program(step):
    words = step.split()
    while words and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]) or words[0] in ("sudo", "time", "env")):
        words.pop(0)
    if not words:
        return ""
    prog = Path(words[0].strip("\"'")).name or words[0]
    return prog


# ---- adapters: each yields command strings -------------------------------------------------

def _as_command(cmd):
    """A command given as a list ["bash", "-lc", "..."] or a string -> one string."""
    if isinstance(cmd, list):
        parts = [str(c) for c in cmd]
        if len(parts) >= 3 and Path(parts[0]).name in SHELL_NAMES and parts[1] in ("-c", "-lc", "-ic", "/c", "-Command"):
            return parts[-1]
        return " ".join(parts)
    return cmd if isinstance(cmd, str) else ""


def read_claude(lines):
    for line in lines:
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if not isinstance(o, dict) or o.get("type") != "assistant":
            continue
        content = (o.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash":
                c = (b.get("input") or {}).get("command")
                if isinstance(c, str) and c.strip():
                    yield c


def read_codex(lines):
    names = {"shell", "exec_command", "local_shell", "container.exec", "shell_command"}
    for line in lines:
        try:
            o = json.loads(line)
        except ValueError:
            continue
        p = o.get("payload") if isinstance(o, dict) else None
        if not isinstance(p, dict):
            p = o if isinstance(o, dict) else {}
        t = p.get("type")
        cmd = None
        if t == "function_call" and p.get("name") in names:
            args = p.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"command": args}
            if isinstance(args, dict):
                cmd = args.get("command") or args.get("cmd")
        elif t == "local_shell_call":
            cmd = (p.get("action") or {}).get("command")
        c = _as_command(cmd)
        if c.strip():
            yield c


def join_continuations(lines, prefix_re=None):
    """Yield whole commands: a line ending in a backslash continues on the next line."""
    pending = None
    for line in lines:
        line = line.rstrip("\r\n")
        if pending is None:
            m = prefix_re.match(line) if prefix_re else None
            pending = m.group(1) if m else line
        else:
            pending += "\n" + line
        if pending.endswith("\\"):
            pending = pending[:-1]
            continue
        c, pending = pending.strip(), None
        if c and not c.startswith("#"):
            yield c
    if pending and pending.strip():
        yield pending.strip()


def read_history(lines):
    return join_continuations(lines, ZSH_RE)


def read_lines(lines):
    return join_continuations(lines)


def detect(lines):
    for line in lines[:50]:
        s = line.strip()
        if not s:
            continue
        if s.startswith("{"):
            try:
                o = json.loads(s)
            except ValueError:
                return "lines"
            if isinstance(o, dict) and ("payload" in o or o.get("type") in ("session_meta", "response_item", "event_msg", "turn_context")):
                return "codex"
            return "claude-code"
        if ZSH_RE.match(s):
            return "shell-history"
        break
    return "lines"


READERS = {"claude-code": read_claude, "codex": read_codex,
           "shell-history": read_history, "lines": read_lines}


def collect(paths, fmt):
    files = []
    for p in paths:
        pp = Path(p).expanduser()
        if pp.is_dir():
            files += sorted(pp.rglob("*.jsonl"))
        elif pp.is_file():
            files.append(pp)
        else:
            sys.stderr.write(f"repeat_scan: not found: {p}\n")
            sys.exit(2)
    if not files:
        sys.stderr.write("repeat_scan: no files found under the given paths\n")
        sys.exit(2)
    commands = []
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            sys.stderr.write(f"repeat_scan: cannot read {f}: {e}\n")
            sys.exit(2)
        use = detect(lines) if fmt == "auto" else fmt
        commands += list(READERS[use](lines))
    return [str(f) for f in files], commands


def analyze(commands, min_count):
    single = collections.OrderedDict()
    chains = collections.OrderedDict()
    nsteps = 0
    for cmd in commands:
        pairs = [(sp, s) for sp, s in split_steps(cmd) if program(s) not in TRIVIAL]
        steps = [s for _, s in pairs]
        nsteps += len(steps)
        norms = []
        for sp, s in pairs:
            n = normalize(s)
            norms.append((sp, n))
            if sp == "|" and program(s) in FILTERS:
                continue
            e = single.setdefault(n, {"form": n, "count": 0, "steps": 1, "program": program(s), "example": s})
            e["count"] += 1
        if len(steps) >= 2:
            key = "".join(f" {sp} {n}" if i else n for i, (sp, n) in enumerate(norms))
            e = chains.setdefault(key, {"form": key, "count": 0, "steps": len(steps),
                                        "program": program(steps[0]), "example": cmd})
            e["count"] += 1
    def finish(d):
        rows = [dict(e, score=e["count"] * e["steps"], example=e["example"][:200])
                for e in d.values() if e["count"] >= min_count]
        return sorted(rows, key=lambda r: (-r["score"], -r["count"], r["form"]))
    return finish(single), finish(chains), nsteps


def table(title, rows):
    print(f"== {title} ({len(rows)})")
    if not rows:
        print("  (none)")
    for r in rows:
        print(f"{r['count']:>4}x  score={r['score']:<4} {r['program'] or '-':<12} {r['form'][:110]}")
        print(f"        e.g. {r['example'][:160].replace(chr(10), ' ')}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 epilog="exit codes: 0 scan done, 2 bad input")
    ap.add_argument("paths", nargs="*", metavar="PATH")
    ap.add_argument("--format", default="auto", choices=FORMATS)
    ap.add_argument("--min-count", type=int, default=2)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.paths:
        sys.stderr.write(
            "repeat_scan: no PATH given. Pass a transcript or history file (or a directory of *.jsonl).\n"
            "  Claude Code  <home>/.claude/projects/<project>/<session>.jsonl\n"
            "  Codex        <home>/.codex/sessions/<year>/<month>/<day>/rollout-*.jsonl\n"
            "  zsh / bash   <home>/.zsh_history  <home>/.bash_history\n"
            "  other        a text file, one command per line (--format lines)\n")
        return 2
    files, commands = collect(args.paths, args.format)
    cands, chains, nsteps = analyze(commands, args.min_count)
    cands, chains = cands[:args.top], chains[:args.top]
    if args.json:
        print(json.dumps({"sources": files, "commands": len(commands), "steps": nsteps,
                          "candidates": cands, "chains": chains}))
    else:
        print(f"{len(files)} file(s), {len(commands)} commands, {nsteps} steps")
        table("repeated steps", cands)
        table("repeated multi-step chains", chains)
    return 0


if __name__ == "__main__":
    sys.exit(main())
