#!/usr/bin/env python3
"""Scaffold a new deterministic script that already meets the script contract.

Deterministic, harness agnostic, no network. Emits a python script (or, with a stated
--reason, a bash one) with a header
doc, --help, --json, the exit-code contract (0 ok / 1 failure / 2 bad input) and, when asked,
a config lookup through homecfg (baked in as a path RELATIVE to the new script, so the pair
moves together). Then adds a row to the scripts index README.

Dry run by default: prints the file it would write. Pass --write to create it.

Usage:
  new_script.py --dir scripts/build --name read_build_number --lang python \\
      --summary "Print the build number read out of a built binary" [--config] [--write]
  new_script.py ... --index path/to/README.md     # index file (default: nearest README.md
                                                  # with a "## Index" table, walking up)

Options:
  --dir DIR        Target directory (created with --write).
  --name NAME      Script name, lowercase snake_case, no extension.
  --lang L         python | bash (default python). Python runs on every OS; bash is an
                   exception and needs --reason.
  --reason TEXT    Why this must be bash (required with --lang bash). Written into the
                   header as `portability-exception:`, which check_scripts.py requires.
  --summary TEXT   One line: what it does. Becomes the header's first line and the index row.
  --config         Wire in homecfg for personal/machine values (read from the home config file).
  --index PATH     README whose "## Index" table gets the new row.
  --write          Actually write the script (chmod +x) and the index row.
  --json           Print {"path","index","written"} instead of the script text.

Exit codes:
  0  printed (dry run) or written
  2  bad input: bad name, --lang bash without --reason, file already exists, homecfg not
     found for --config, --config target outside homecfg's tree (a relative path would climb
     through a home dir), no index found
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

PY = '''#!/usr/bin/env python3
"""{summary}

Deterministic, read-only unless stated. TODO: describe inputs, method and output.

Usage:
  {name}.py [--json]

Exit codes:
  0  ok
  1  the check/operation failed
  2  bad input
"""

import argparse
import json
import os
import sys
{cfg_import}

def main():
    ap = argparse.ArgumentParser(description="{summary_esc}")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()
{cfg_use}
    result = {{}}  # TODO
    print(json.dumps(result) if a.json else result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

PY_CFG_IMPORT = '''sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "{rel}"))
import homecfg  # noqa: E402
'''

PY_CFG_USE = '''    try:
        example = homecfg.get("example_key")  # TODO: real key names
    except homecfg.Missing as e:
        print(str(e), file=sys.stderr)
        return 2
'''

SH = '''#!/usr/bin/env bash
# {summary}
#
# Deterministic, read-only unless stated. TODO: describe inputs, method and output.
#
# Usage:
#   {name}.sh [--json]
#
# Exit codes: 0 ok, 1 the check/operation failed, 2 bad input.
# portability-exception: {reason}
set -euo pipefail
{cfg_import}
json=0
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) sed -n '2,9p' "$0" | sed 's/^# \\{{0,1\\}}//'; exit 0 ;;
        --json) json=1 ;;
        *) echo "{name}: unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done
{cfg_use}
# TODO
if [ "$json" = 1 ]; then echo '{{}}'; else echo "ok"; fi
'''

SH_CFG_IMPORT = '''homecfg="$(cd "$(dirname "$0")" && pwd)/{rel}/homecfg.py"
'''

SH_CFG_USE = '''example="$(python3 "$homecfg" get example_key)" || exit 2  # TODO: real key names
'''


def find_index(start):
    d = os.path.abspath(start)
    while True:
        p = os.path.join(d, "README.md")
        if os.path.isfile(p) and "## Index" in open(p, encoding="utf-8").read():
            return p
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def main():
    ap = argparse.ArgumentParser(description="Scaffold a contract-conforming script.")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--lang", choices=["python", "bash"], default="python")
    ap.add_argument("--reason")
    ap.add_argument("--summary", required=True)
    ap.add_argument("--config", action="store_true")
    ap.add_argument("--index")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not re.fullmatch(r"[a-z][a-z0-9_]*", a.name):
        print("new_script: --name must be lowercase snake_case", file=sys.stderr)
        return 2
    if a.lang == "bash" and not (a.reason or "").strip():
        print("new_script: --lang bash needs --reason (python3 is the portable default)", file=sys.stderr)
        return 2
    ext = ".py" if a.lang == "python" else ".sh"
    target = os.path.join(a.dir, a.name + ext)
    if os.path.exists(target):
        print("new_script: already exists: %s" % target, file=sys.stderr)
        return 2

    rel = os.path.relpath(HERE, os.path.abspath(a.dir))
    if a.config and not os.path.isfile(os.path.join(HERE, "homecfg.py")):
        print("new_script: homecfg.py not found next to new_script.py", file=sys.stderr)
        return 2
    if a.config and os.path.basename(os.path.expanduser("~")) in rel.split(os.sep):
        # The relative path would descend through a home dir, baking a machine path in.
        print("new_script: --config needs --dir in the same tree as homecfg (%s)" % HERE, file=sys.stderr)
        return 2

    summary = a.summary.rstrip(".")
    if a.lang == "python":
        text = PY.format(name=a.name, summary=summary, summary_esc=summary.replace('"', '\\"'),
                         cfg_import=PY_CFG_IMPORT.format(rel=rel) if a.config else "",
                         cfg_use=PY_CFG_USE if a.config else "")
    else:
        text = SH.format(name=a.name, summary=summary, reason=a.reason.strip(),
                         cfg_import=SH_CFG_IMPORT.format(rel=rel) if a.config else "",
                         cfg_use=SH_CFG_USE if a.config else "")

    index = a.index or find_index(a.dir if os.path.isdir(a.dir) else os.path.dirname(os.path.abspath(a.dir)) or ".")
    if a.write and not index:
        print("new_script: no README.md with a '## Index' table found; pass --index", file=sys.stderr)
        return 2

    if a.write:
        os.makedirs(a.dir, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(target, 0o755)
        rel_script = os.path.relpath(os.path.abspath(target), os.path.dirname(index))
        with open(index, "a", encoding="utf-8") as f:
            f.write("| `%s` | %s |\n" % (rel_script, summary))

    if a.json:
        print(json.dumps({"path": target, "index": index, "written": a.write}))
    elif not a.write:
        sys.stdout.write(text)
    else:
        print("wrote %s; indexed in %s" % (target, index))
    return 0


if __name__ == "__main__":
    sys.exit(main())
