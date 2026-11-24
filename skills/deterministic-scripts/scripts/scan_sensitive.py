#!/usr/bin/env python3
"""Scan files for personal, device, machine and secret values that must not be committed.

Deterministic, harness agnostic, read-only. The one implementation of "is this sensitive":
check_scripts.py and the pre-commit hook both use it. Hits are printed REDACTED (first 3
characters) so the scanner's own output never leaks what it found.

Rules:
  home-path    absolute home paths: /Users/<name>, /home/<name>, C:\\Users\\<name>
  email        email addresses, except noreply, example.* and the allowlist
  config-value any value (6+ chars) from the home config (homecfg.py) or the literal values
               below: the config file IS the deny list, so a value kept there may not appear
               anywhere else
  config-prefix the first 8 characters of any ID-SHAPED config value (16+ chars of hex and
               dashes: fingerprints, UDIDs, UUIDs): a truncated `5CD2...` still identifies
               it. Never applied to words or emails.
  ios-udid     iOS device UDIDs (00008xxx-xxxxxxxxxxxxxxxx)
  fingerprint  uppercase 40-hex certificate fingerprints (SHA-1)
  private-key  PEM private key blocks
  token        common credential shapes: AWS, GitHub (ghp_, github_pat_), Slack (xox*),
               Google API, sk- API keys, `Bearer <token>`
  secret-assign  password/secret/api key/token assigned a literal (password="...", token=...)
  env-secret   .env-style KEY=value where KEY ends in KEY, SECRET, TOKEN, PASSWORD or CREDENTIAL
  <name>       any extra pattern you define (below)

Extra patterns and literal values, all optional, merged with the built-ins (never hard-coded):
  1. home config (homecfg.py path): every value is a literal; a key starting `pattern` holds a
     regex instead
  2. $DETERMINISTIC_SCRIPTS_PATTERNS: regexes, one per line (`REGEX` or `NAME :: REGEX`)
  3. $DETERMINISTIC_SCRIPTS_LITERALS: literal values, comma separated
  4. patterns files (homecfg.py where): <config dir>/patterns.txt, each path in
     $DETERMINISTIC_SCRIPTS_PATTERNS_FILE, and patterns.local.txt next to this script. A line
     is `REGEX`, `NAME :: REGEX` or `literal: VALUE`; `#` starts a comment.

Allowlist: `.sensitive-allow` at the repo root (and any --allow-file), `#` comments. Each line
is `REGEX` (matched against the hit text, any file) or `GLOB :: REGEX` (only in files whose
repo path matches GLOB, e.g. `*tests/* :: PRIVATE KEY`). Use it for PUBLIC or FAKE values
only (support addresses printed in the app, test fixtures).

Usage:
  scan_sensitive.py PATH [PATH ...]      # files or dirs (dirs walked, .git skipped)
  scan_sensitive.py --tracked            # every tracked file in the current git repo
  scan_sensitive.py --staged             # the STAGED content of staged files (pre-commit)
  scan_sensitive.py --staged --added     # only lines this commit adds (legacy lines elsewhere
                                         # in a touched file do not block it)
  options: --json  --no-config  --allow REGEX  --allow-file PATH  --rules home-path,email,...
  --no-config skips the home config, env and patterns-file extras (built-ins only).

Exit codes:
  0  nothing found
  1  sensitive values found
  2  bad input (no such path, not a git repo)
"""

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_BYTES = 2 * 1024 * 1024
ALLOW_FILE = ".sensitive-allow"

SECRET_WORDS = r"(?:[Pp]assword|[Pp]asswd|[Ss]ecret|[Aa]pi_?[Kk]ey|[Aa]ccess_?[Kk]ey|[Tt]oken)"
PATTERNS = {
    "home-path": re.compile(r"/(?:Users|home)/(?!<|\*|\$|Shared\b)[A-Za-z0-9._-]+"
                            r"|\b[A-Za-z]:\\Users\\(?!<|\*|\$|Public\b|Default\b)[A-Za-z0-9._-]+"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
    "ios-udid": re.compile(r"\b[0-9A-F]{8}-[0-9A-F]{16}\b"),
    "fingerprint": re.compile(r"\b[0-9A-F]{40}\b"),
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "token": re.compile(r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{22,}"
                        r"|xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}|sk-(?:ant-)?[A-Za-z0-9_-]{20,})\b"
                        r"|\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    # A quoted literal, or an unquoted one that has a digit (so a value read from a variable is not a hit).
    "secret-assign": re.compile(r"\b[A-Za-z0-9_]*" + SECRET_WORDS + r"[A-Za-z0-9_]*\s*[:=]\s*"
                                r"(?:\"[^\"\s<>$]{8,}\"|'[^'\s<>$]{8,}'|(?=[^\s\"']*\d)[^\s\"'<>$(){}\[\],;]{8,})"),
    "env-secret": re.compile(r"^\s*(?:export\s+)?[A-Z][A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL)S?"
                             r"=[\"']?(?![<$\"'\s])[^\s\"'()\[\]]{8,}(?=[\s\"']|$)"),
}
# Things shaped like a hit that never are: placeholders, retina image file names.
BUILTIN_ALLOW = [
    r"^0+(-0+)?$",
    r"@example\.(com|org|net)$",
    r"@\d+x\.(png|jpg|jpeg|webp|pdf)$",
    r"^git@github\.com$",
    r"@users\.noreply\.github\.com$",
    r"(?i)^noreply@|@(?:[a-z0-9-]+\.)*noreply\.",
    r"(?i)[:=]\s*[\"']?(?:changeme|example|your[_-]|dummy|placeholder|x{4,}|\*{4,}|redacted)",
]


PREFIX_LEN = 8


ID_SHAPED = re.compile(r"^[0-9A-Fa-f-]{16,}$")


def id_shaped(v):
    return bool(ID_SHAPED.match(v))


def prefixes(values):
    return sorted({v[:PREFIX_LEN] for v in values if id_shaped(v)})


def _split_pattern(line):
    name, sep, rx = line.partition(" :: ")
    return (name.strip(), rx.strip()) if sep else ("extra", line.strip())


def load_extra(use=True):
    """(values, patterns): literal deny-list values and [(rule name, compiled regex)] from the
    home config, env and patterns files. Empty when use is False."""
    if not use:
        return [], []
    sys.path.insert(0, HERE)
    values, patterns = [], []

    def add_pattern(name, rx):
        try:
            patterns.append((name, re.compile(rx)))
        except re.error:
            print("scan_sensitive: skipping bad pattern %r" % name, file=sys.stderr)

    try:
        import homecfg
        for k, v in homecfg.load().items():
            if k.startswith("pattern"):
                if v:
                    add_pattern(k, v)
            elif len(v) >= 6:
                values.append(v)
    except Exception:
        pass
    for line in os.environ.get("DETERMINISTIC_SCRIPTS_PATTERNS", "").splitlines():
        if line.strip():
            add_pattern(*_split_pattern(line))
    values += [v.strip() for v in os.environ.get("DETERMINISTIC_SCRIPTS_LITERALS", "").split(",")
               if len(v.strip()) >= 6]
    try:
        import homecfg
        files = homecfg.pattern_files()
    except Exception:
        files = []
    for f in files:
        if not os.path.isfile(f):
            continue
        with open(f, encoding="utf-8") as h:
            for line in h:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("literal:"):
                    v = line[len("literal:"):].strip()
                    if len(v) >= 6:
                        values.append(v)
                else:
                    add_pattern(*_split_pattern(line))
    return sorted(set(values), key=len, reverse=True), patterns


_EXTRA = {}


def extra_patterns(use=True):
    if use not in _EXTRA:
        _EXTRA[use] = load_extra(use)[1]
    return _EXTRA[use]


def config_values(use):
    return load_extra(use)[0]


def load_allow(extra, files):
    """Return [(glob or None, compiled regex)]."""
    allow = [(None, a) for a in list(BUILTIN_ALLOW) + list(extra)]
    for f in files:
        if f and os.path.isfile(f):
            for line in open(f, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if " :: " in line:
                    g, _, rx = line.partition(" :: ")
                    allow.append((g.strip(), rx.strip()))
                else:
                    allow.append((None, line))
    return [(g, re.compile(rx)) for g, rx in allow]


def allowed(hit, name, allow):
    return any((g is None or fnmatch.fnmatch(name, g)) and rx.search(hit) for g, rx in allow)


def redact(v):
    return v[:3] + "…" if len(v) > 3 else "…"


def scan_text(text, values, allow, rules=None, name="", only_lines=None, extra=None):
    """Yield (line_no, rule, hit) for every sensitive value in text. extra: [(rule, regex)]
    beyond the built-ins (default: the ones from the config, env and patterns files)."""
    if extra is None:
        extra = extra_patterns()
    for n, line in enumerate(text.splitlines(), 1):
        if only_lines is not None and n not in only_lines:
            continue
        for rule, rx in list(PATTERNS.items()) + list(extra):
            if rules and rule not in rules:
                continue
            for m in rx.finditer(line):
                hit = m.group(0)
                if not allowed(hit, name, allow):
                    yield n, rule, hit
        if not rules or "config-value" in rules:
            full = [v for v in values if v in line and not allowed(v, name, allow)]
            for v in full:
                yield n, "config-value", v
            for pre in prefixes(values):
                if pre in line and not any(v.startswith(pre) for v in full) \
                        and not allowed(pre, name, allow):
                    yield n, "config-prefix", pre


def walk(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for root, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "bin", "obj", "__pycache__")]
            for f in sorted(files):
                yield os.path.join(root, f)


def read_file(p):
    try:
        if os.path.getsize(p) > MAX_BYTES:
            return None
        data = open(p, "rb").read()
    except OSError:
        return None
    return decode(data)


def decode(data):
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def git(*args):
    return subprocess.run(["git", *args], capture_output=True)


def added_lines(name):
    """Line numbers (in the staged file) that the staged diff adds."""
    out = git("diff", "--cached", "-U0", "--no-color", "--", name).stdout.decode("utf-8", "replace")
    lines = set()
    for m in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", out, re.M):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        lines.update(range(start, start + count))
    return lines


def main():
    ap = argparse.ArgumentParser(description="Scan for sensitive values.")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--tracked", action="store_true")
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--added", action="store_true", help="with --staged: only added lines")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-config", action="store_true")
    ap.add_argument("--allow", action="append", default=[])
    ap.add_argument("--allow-file", action="append", default=[])
    ap.add_argument("--rules", help="comma list; default all")
    a = ap.parse_args()

    rules = set(a.rules.split(",")) if a.rules else None
    extra = extra_patterns(not a.no_config)
    if rules and not rules <= set(PATTERNS) | {"config-value", "config-prefix"} | {n for n, _ in extra}:
        print("scan_sensitive: unknown rule in --rules", file=sys.stderr)
        return 2

    top = git("rev-parse", "--show-toplevel")
    root = top.stdout.decode().strip() if top.returncode == 0 else None
    allow = load_allow(a.allow, a.allow_file + ([os.path.join(root, ALLOW_FILE)] if root else []))
    values = config_values(not a.no_config)

    items = []  # (display path, text, only_lines or None)
    if a.staged or a.tracked:
        if not root:
            print("scan_sensitive: not inside a git repo", file=sys.stderr)
            return 2
        if a.staged:
            names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").stdout
        else:
            names = git("ls-files", "-z").stdout
        for name in [n for n in names.decode().split("\0") if n]:
            if a.staged:
                data = git("show", ":" + name).stdout
            else:
                p = os.path.join(root, name)
                if not os.path.isfile(p) or os.path.islink(p) or os.path.getsize(p) > MAX_BYTES:
                    continue
                data = open(p, "rb").read()
            text = decode(data[:MAX_BYTES]) if len(data) <= MAX_BYTES else None
            if text is not None:
                items.append((name, text, added_lines(name) if a.staged and a.added else None))
    else:
        if not a.paths:
            print("scan_sensitive: give PATHs, --tracked or --staged", file=sys.stderr)
            return 2
        for p in a.paths:
            if not os.path.exists(p):
                print("scan_sensitive: no such path: %s" % p, file=sys.stderr)
                return 2
        for p in walk(a.paths):
            if os.path.islink(p):
                continue
            text = read_file(p)
            if text is not None:
                # Allowlist globs are repo-relative, whatever the cwd or the path given.
                name = os.path.relpath(os.path.abspath(p), root) if root else p
                items.append((name, text, None))

    hits = []
    for name, text, only in items:
        if os.path.basename(name) == ALLOW_FILE:
            continue  # the allowlist holds public/fake values by definition
        for n, rule, hit in scan_text(text, values, allow, rules, name, only, extra):
            hits.append({"file": name, "line": n, "rule": rule, "hit": redact(hit)})

    if a.json:
        print(json.dumps({"files": len(items), "hits": hits}))
    else:
        for h in hits:
            print("%s:%d: [%s] %s" % (h["file"], h["line"], h["rule"], h["hit"]))
        if hits:
            print("%d sensitive value(s) in %d file(s) scanned. Move them to the home config "
                  "(homecfg.py) or, if PUBLIC, allowlist in .sensitive-allow." % (len(hits), len(items)),
                  file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
