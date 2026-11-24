#!/usr/bin/env python3
"""Check scripts against the script contract and for personal values that belong in config.

Deterministic, harness agnostic, read-only (runs each script's --help only with --run-help).
Point it at script files or directories; it checks every *.py / *.sh / *.bash / *.zsh /
extensionless file with a shebang.

Checks:
  shebang      first line is #!
  exec         executable bit set
  doc          a header doc (python docstring or a 3+ line bash comment block)
  contract     the file mentions --help handling, --json and its exit codes
  lang         a shell script (bash/zsh/sh) carries a `portability-exception: <reason>` header
               line; python3 is the default language so scripts run on any OS
  extension    a python or extensionless script is named `<name>.py` (shell scripts with a
               stated portability exception, e.g. git hooks, may be extensionless)
  indexed      the script's name appears in the nearest README.md that has a "## Index"
  personal     every scan_sensitive.py rule (home paths, emails, config values, device ids,
               fingerprints, keys, tokens), honouring the repo's .sensitive-allow
Exempt from the contract checks (still scanned for personal values): python modules with no
`__main__` (libraries, not scripts) and anything under a `vendor/` folder (third party).
  help-runs    with --run-help: `<script> --help` exits 0 within 10s

Usage:
  check_scripts.py DIR_OR_FILE [...] [--json] [--run-help] [--allow REGEX ...] [--no-config]

Options:
  --allow REGEX   Ignore personal-value hits matching REGEX (repeatable), e.g. 'git@github\\.com'.
  --no-config     Skip comparing against config values (e.g. no config on this machine).
  --run-help      Execute each script's --help.
  --json          {"files":N,"violations":[{"file","check","detail"}]}

Exit codes:
  0  every file passes
  1  at least one violation
  2  bad input (path does not exist)
"""

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = None
SCRIPT_EXT = (".py", ".sh", ".bash", ".zsh")
SHELL_EXT = (".sh", ".bash", ".zsh")
SHELL_SHEBANG = re.compile(r"^#!.*\b(?:ba|z|k|da)?sh\b")
sys.path.insert(0, HERE)
import scan_sensitive  # noqa: E402


def is_script_file(fp):
    f = os.path.basename(fp)
    if f.endswith(SCRIPT_EXT):
        return True
    if "." not in f:
        try:
            with open(fp, "rb") as h:
                return h.read(2) == b"#!"
        except OSError:
            return False
    return False


def candidates(paths):
    for p in paths:
        if os.path.isfile(p):
            if is_script_file(p):
                yield p
            continue
        for root, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            for f in sorted(files):
                fp = os.path.join(root, f)
                if f.endswith(SCRIPT_EXT):
                    yield fp
                elif "." not in f:
                    try:
                        with open(fp, "rb") as h:
                            if h.read(2) == b"#!":
                                yield fp
                    except OSError:
                        pass




def nearest_index(path):
    d = os.path.dirname(os.path.abspath(path))
    while True:
        p = os.path.join(d, "README.md")
        if os.path.isfile(p):
            text = open(p, encoding="utf-8").read()
            if "## Index" in text:
                return text
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def check(path, values, allow, run_help, extra=None):
    out = []
    add = lambda c, d: out.append({"file": path, "check": c, "detail": d})
    try:
        text = open(path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError) as e:
        add("read", str(e))
        return out
    lines = text.splitlines()
    rel = os.path.relpath(os.path.abspath(path), ROOT) if ROOT else path
    for n, rule, hit in scan_sensitive.scan_text(text, values, allow, name=rel, extra=extra):
        add("personal", "line %d [%s] %s" % (n, rule, scan_sensitive.redact(hit)))
    exempt = "/vendor/" in os.path.abspath(path) or \
        (path.endswith(".py") and "__main__" not in text)
    if exempt:
        return out
    if not lines or not lines[0].startswith("#!"):
        add("shebang", "first line is not a #! shebang")
    if not os.access(path, os.X_OK):
        add("exec", "not executable (chmod +x)")
    body = "\n".join(lines[1:40])
    has_doc = ('"""' in body) if path.endswith(".py") else \
        sum(1 for l in lines[1:20] if l.startswith("#")) >= 3
    if not has_doc:
        add("doc", "no header doc")
    is_shell = path.endswith(SHELL_EXT) or bool(lines and SHELL_SHEBANG.match(lines[0]))
    if is_shell and "portability-exception:" not in "\n".join(lines[:40]):
        add("lang", "shell script: port to python3, or add a '# portability-exception: <reason>' "
                    "header line and raise it with the owner")
    base = os.path.basename(path)
    if not is_shell and not base.endswith(".py") and "." not in base:
        add("extension", "python entrypoint without a .py extension")
    elif lines and re.match(r"^#!.*\bpython", lines[0]) and not base.endswith(".py"):
        add("extension", "python shebang on a file not named .py")
    low = text.lower()
    if "--json" not in text:
        add("contract", "no --json output mode")
    if "exit code" not in low:
        add("contract", "exit codes not documented")
    if not (path.endswith(".py") and "argparse" in text) and "--help" not in text:
        add("contract", "no --help handling")
    idx = nearest_index(path)
    if idx is None or os.path.basename(path) not in idx:
        add("indexed", "not listed in the nearest README.md '## Index'")
    if run_help:
        try:
            r = subprocess.run([path, "--help"], capture_output=True, timeout=10)
            if r.returncode != 0:
                add("help-runs", "--help exited %d" % r.returncode)
        except Exception as e:
            add("help-runs", str(e))
    return out


def main():
    ap = argparse.ArgumentParser(description="Check scripts against the script contract.")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--allow", action="append", default=[])
    ap.add_argument("--no-config", action="store_true")
    ap.add_argument("--run-help", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    for p in a.paths:
        if not os.path.exists(p):
            print("check_scripts: no such path: %s" % p, file=sys.stderr)
            return 2
    global ROOT
    top = scan_sensitive.git("rev-parse", "--show-toplevel")
    ROOT = top.stdout.decode().strip() if top.returncode == 0 else None
    values = scan_sensitive.config_values(not a.no_config)
    allow = scan_sensitive.load_allow(a.allow, [os.path.join(ROOT, ".sensitive-allow")] if ROOT else [])
    files = list(candidates(a.paths))
    extra = scan_sensitive.extra_patterns(not a.no_config)
    violations = [v for f in files for v in check(f, values, allow, a.run_help, extra)]
    if a.json:
        print(json.dumps({"files": len(files), "violations": violations}))
    else:
        for v in violations:
            print("%s: [%s] %s" % (v["file"], v["check"], v["detail"]))
        print("%d file(s), %d violation(s)" % (len(files), len(violations)))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
