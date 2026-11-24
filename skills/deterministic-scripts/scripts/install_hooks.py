#!/usr/bin/env python3
"""Point a git repo's hooks at this kit's hooks/ folder (core.hooksPath), so every commit
runs the sensitive-value scan and the script contract check.

Reports first: with no flag it only says whether the hook is installed. --write installs it;
--uninstall --write removes it. The path is stored RELATIVE to the repo root, so it survives
moving the checkout. Install refuses (exit 2) if the repo already has its own non-sample
hooks in .git/hooks or another core.hooksPath, since that would silently disable them.
Uninstall only unsets core.hooksPath when it points at this kit's hooks. The only thing
written is that one git config key, and only with --write.

Usage:
  install_hooks.py [REPO_DIR] [--write] [--uninstall] [--json]
    REPO_DIR     repo to inspect or change (default: the current repo)
    (no flag)    report whether it is installed; change nothing
    --write      install (or, with --uninstall, remove)
    --uninstall  select removal instead of install
    --check      same as no flag (kept for older callers)
    --json       print {"repo","hooks_path","installed","changed"}

Exit codes:
  0  installed (or already), or uninstalled / already absent with --uninstall --write
  1  report found it not installed (or, with --uninstall, still installed)
  2  bad input: not a git repo, or the repo already has its own hooks
"""

import argparse
import json
import os
import subprocess
import sys

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks")


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def main():
    ap = argparse.ArgumentParser(description="Report, install or remove a repo's core.hooksPath for this kit's hooks.")
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--write", action="store_true", help="install (or remove with --uninstall)")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--check", action="store_true", help="report only (the default)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    code, root = git(a.repo, "rev-parse", "--show-toplevel")
    if code != 0:
        print("install_hooks: not a git repo: %s" % a.repo, file=sys.stderr)
        return 2
    try:
        rel = os.path.relpath(HOOKS, root).replace(os.sep, "/")
    except ValueError:
        print("install_hooks: hooks folder and repo are on different drives", file=sys.stderr)
        return 2
    _, current = git(root, "config", "--get", "core.hooksPath")
    mine = current == rel

    def emit(installed, changed, text):
        if a.json:
            print(json.dumps({"repo": os.path.basename(root), "hooks_path": rel,
                              "installed": installed, "changed": changed}))
        else:
            print(text)

    if a.uninstall:
        if not mine:
            emit(False, False, "not installed in %s (core.hooksPath='%s')" % (root, current))
            return 0 if a.write else 1
        if not a.write:
            emit(True, False, "installed: %s -> %s (add --write to remove)" % (root, rel))
            return 1
        if git(root, "config", "--unset", "core.hooksPath")[0] != 0:
            print("install_hooks: git config --unset failed", file=sys.stderr)
            return 2
        emit(False, True, "uninstalled: %s" % root)
        return 0

    if mine:
        emit(True, False, "installed: %s -> %s" % (root, rel))
        return 0
    if not a.write or a.check:
        emit(False, False, "NOT installed in %s (core.hooksPath='%s'); add --write to install" % (root, current))
        return 1

    _, gitdir = git(root, "rev-parse", "--absolute-git-dir")
    hooks_dir = os.path.join(gitdir, "hooks")
    own = sorted(f for f in (os.listdir(hooks_dir) if os.path.isdir(hooks_dir) else [])
                 if os.path.isfile(os.path.join(hooks_dir, f)) and not f.endswith(".sample"))
    if own or current:
        what = os.path.join(hooks_dir, own[0]) if own else "core.hooksPath=" + current
        print("install_hooks: %s already has hooks (%s); merge by hand" % (root, what), file=sys.stderr)
        return 2
    if git(root, "config", "core.hooksPath", rel)[0] != 0:
        print("install_hooks: git config failed", file=sys.stderr)
        return 2
    emit(True, True, "installed: %s -> %s" % (root, rel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
