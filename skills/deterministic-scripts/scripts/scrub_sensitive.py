#!/usr/bin/env python3
"""Replace personal and machine values in text files with portable references.

Deterministic, harness agnostic. Dry run by default (prints what would change, redacted);
--write rewrites the files. Two substitutions, both reversible by whoever holds the config:

  home paths    /Users/<name>/…  or  /home/<name>/…   ->  ~/…
  config values any value (6+ chars) from the home config (homecfg.py)  ->  <config:key>
                (read it back with `homecfg.py get key`); a truncated form of an ID-shaped value
                (`5CD2…`, `5CD2...4D38`, a bare 8-char prefix) is replaced the same way

Values that are not in the config (a new device id, an email, a token) are left alone and
reported by scan_sensitive.py: add them to the config first, then run this again. Literal
values from env or patterns files have no key and are never rewritten.

Usage:
  scrub_sensitive.py PATH [PATH ...] [--write] [--json]

Exit codes:
  0  nothing to change, or changes written with --write
  1  dry run found changes to make
  2  bad input (no such path)
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import homecfg  # noqa: E402
import scan_sensitive  # noqa: E402

HOME_RE = re.compile(r"/(?:Users|home)/(?!<|\*|\$|Shared\b)[A-Za-z0-9._-]+(?=/|\b)")
WIN_HOME_RE = re.compile(r"\b[A-Za-z]:\\Users\\(?!<|\*|\$|Public\b|Default\b)[A-Za-z0-9._-]+")


def main():
    ap = argparse.ArgumentParser(description="Replace home paths and config values with references.")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    for p in a.paths:
        if not os.path.exists(p):
            print("scrub_sensitive: no such path: %s" % p, file=sys.stderr)
            return 2
    try:
        cfg = homecfg.load()
    except homecfg.Missing as e:
        print("%s; rewriting home paths only" % e, file=sys.stderr)
        cfg = {}
    # Longest first, so a value containing another is replaced whole.
    pairs = sorted(((v, k) for k, v in cfg.items() if len(v) >= 6 and not k.startswith("pattern")), key=lambda x: -len(x[0]))

    changes = []
    for p in scan_sensitive.walk(a.paths):
        if os.path.islink(p):
            continue
        text = scan_sensitive.read_file(p)
        if text is None:
            continue
        new, n_home = HOME_RE.subn("~", text)
        new, n_win = WIN_HOME_RE.subn("~", new)
        n_home += n_win
        counts = {"home-path": n_home}
        for v, k in pairs:
            c = new.count(v)
            if c:
                new = new.replace(v, "<config:%s>" % k)
                counts[k] = c
        for v, k in pairs:
            if scan_sensitive.id_shaped(v):
                rx = re.compile(re.escape(v[:scan_sensitive.PREFIX_LEN]) + r"(?:…|\.\.\.[0-9A-Za-z]*)?")
                new, c = rx.subn("<config:%s>" % k, new)
                if c:
                    counts[k] = counts.get(k, 0) + c
        counts = {k: c for k, c in counts.items() if c}
        if counts:
            changes.append({"file": p, "replaced": counts})
            if a.write:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(new)

    if a.json:
        print(json.dumps({"written": a.write, "changes": changes}))
    else:
        for c in changes:
            print("%s: %s" % (c["file"], ", ".join("%s×%d" % kv for kv in c["replaced"].items())))
        print("%d file(s) %s" % (len(changes), "rewritten" if a.write else "would change"))
    return 1 if changes and not a.write else 0


if __name__ == "__main__":
    sys.exit(main())
