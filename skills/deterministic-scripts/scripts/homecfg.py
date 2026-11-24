#!/usr/bin/env python3
"""Read personal, device and machine values from one home-directory config file.

Deterministic, harness agnostic, read-only. Scripts, prompts, skills and flows never carry
personal values (names, emails, ids, hostnames, home paths, key paths, tokens); they ask this
loader for a KEY at run time, so an AI harness never needs to see the value. The same file is
the deny list for scan_sensitive.py: a value kept here may not appear anywhere else.

Config file (first that is set wins):
  1. --file PATH
  2. $DETERMINISTIC_SCRIPTS_CONFIG
  3. $DETERMINISTIC_SCRIPTS_HOME/config.env
  4. %APPDATA%\\deterministic-scripts\\config.env on Windows, else
     ${XDG_CONFIG_HOME:-~/.config}/deterministic-scripts/config.env
  5. only when 4 does not exist: files named by an optional homecfg_local.py next to this
     script (a function fallback_files() returning paths); a local add-on, never shipped

Format: KEY=value lines. `#` comments, blank lines, an optional `export ` prefix and single
or double quotes around the value are allowed. A leading `~` in a value expands. Keys are
lowercase snake_case, named for what they hold (dev_email, device_serial). Keep the file mode
600 and out of every repo. Keys starting `pattern` hold a regex for scan_sensitive.py instead
of a value. Secrets stay in their own files; the config holds the path to them.

Usage:
  homecfg.py get KEY [--default V]   # print the value; exit 2 if missing and no default
  homecfg.py keys [--json]           # list key names (never values)
  homecfg.py path                    # print the resolved config file path
  homecfg.py check KEY [KEY ...]     # exit 0 if every key is set, else 2 naming the missing ones
  homecfg.py where [--json]          # config and pattern files read, and whether each exists
  options: --file PATH

Python:
  sys.path.insert(0, "<dir of this file>"); import homecfg
  homecfg.get("device_serial")       # raises homecfg.Missing (exit-2 semantics) when absent

Exit codes:
  0  ok
  2  bad input: config file missing, or a key missing with no default
"""

import argparse
import importlib.util
import json
import os
import sys

APP = "deterministic-scripts"
HERE = os.path.dirname(os.path.abspath(__file__))


class Missing(Exception):
    pass


def config_dir():
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP)


def local_module():
    """The optional homecfg_local.py beside this script (a local add-on), or None."""
    p = os.path.join(HERE, "homecfg_local.py")
    if not os.path.isfile(p):
        return None
    spec = importlib.util.spec_from_file_location("homecfg_local", p)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None
    return mod


def path(explicit=None):
    if explicit:
        return os.path.expanduser(explicit)
    if os.environ.get("DETERMINISTIC_SCRIPTS_CONFIG"):
        return os.path.expanduser(os.environ["DETERMINISTIC_SCRIPTS_CONFIG"])
    if os.environ.get("DETERMINISTIC_SCRIPTS_HOME"):
        return os.path.join(os.path.expanduser(os.environ["DETERMINISTIC_SCRIPTS_HOME"]), "config.env")
    default = os.path.join(config_dir(), "config.env")
    if not os.path.isfile(default):
        mod = local_module()
        for p in (getattr(mod, "fallback_files", lambda: [])() if mod else []):
            if os.path.isfile(os.path.expanduser(p)):
                return os.path.expanduser(p)
    return default


def parse(p):
    values = {}
    with open(p, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                val = val[1:-1]
            if val.startswith("~"):
                val = os.path.expanduser(val)
            values[key] = val
    return values


def load(explicit=None):
    p = path(explicit)
    if not os.path.isfile(p):
        raise Missing("homecfg: config file not found: %s (set DETERMINISTIC_SCRIPTS_CONFIG or "
                      "create it; see INSTALL.md)" % p)
    return parse(p)


def get(key, default=None, explicit=None):
    try:
        values = load(explicit)
    except Missing:
        if default is not None:
            return default
        raise
    if key in values and values[key] != "":
        return values[key]
    if default is not None:
        return default
    raise Missing("homecfg: key '%s' not set in %s" % (key, path(explicit)))


def pattern_files():
    """Regex/literal files scan_sensitive.py reads, in order (see its --help)."""
    files = [os.path.join(config_dir(), "patterns.txt")]
    files += [p for p in os.environ.get("DETERMINISTIC_SCRIPTS_PATTERNS_FILE", "").split(os.pathsep) if p]
    files.append(os.path.join(HERE, "patterns.local.txt"))
    return [os.path.expanduser(p) for p in files]


def main():
    ap = argparse.ArgumentParser(description="Read values from the home-directory config file.")
    ap.add_argument("--file", help="config file (overrides DETERMINISTIC_SCRIPTS_CONFIG / _HOME)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get")
    g.add_argument("key")
    g.add_argument("--default")
    k = sub.add_parser("keys")
    k.add_argument("--json", action="store_true")
    sub.add_parser("path")
    c = sub.add_parser("check")
    c.add_argument("key", nargs="+")
    w = sub.add_parser("where")
    w.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        if a.cmd == "path":
            print(path(a.file))
        elif a.cmd == "get":
            print(get(a.key, a.default, a.file))
        elif a.cmd == "keys":
            names = sorted(load(a.file))
            print(json.dumps(names) if a.json else "\n".join(names))
        elif a.cmd == "check":
            values = load(a.file)
            missing = [x for x in a.key if not values.get(x)]
            if missing:
                raise Missing("homecfg: missing keys in %s: %s" % (path(a.file), ", ".join(missing)))
        elif a.cmd == "where":
            rows = [{"kind": "config", "path": path(a.file)}]
            rows += [{"kind": "patterns", "path": p} for p in pattern_files()]
            for r in rows:
                r["exists"] = os.path.isfile(r["path"])
            if a.json:
                print(json.dumps(rows))
            else:
                for r in rows:
                    print("%-8s %s%s" % (r["kind"], r["path"], "" if r["exists"] else "  (absent)"))
    except Missing as e:
        print(str(e), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
