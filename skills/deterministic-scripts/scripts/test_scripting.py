#!/usr/bin/env python3
"""Self-test for the scripting kit: scan_sensitive, scrub_sensitive, check_scripts, new_script,
install_hooks, repeat_scan.

Deterministic, read-only outside a temp dir it creates and removes. Fixtures are written by
hand here (never produced by the code under test), with a throwaway config via
DETERMINISTIC_SCRIPTS_CONFIG and an empty config home, so it runs the same on any machine and
touches no real config. Secret-shaped fixtures are assembled from pieces so this file never
trips the scanner itself.

Covers the failures that have actually happened: a truncated-prefix rule that rewrote every
plain word because an email in the config began with it; a scaffolder that baked a home
path into a script created outside the tree; plus allowlist globs, the added-lines-only
pre-commit mode, the generic token/secret rules, extra patterns from env and files, the
hook installer (report, --write, --uninstall) and the repeat finder.

Usage:
  test_scripting.py [--json]

Exit codes:
  0  all cases pass
  1  a case failed
  2  bad input (git or python3 missing)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

EMAIL = "robot@" + "proj-1.iam.gserviceaccount.com"      # first 8 chars: "robot@pr"
PROJECT_WORD = "robotics"                            # shares the email's first 5 chars only
EMAIL2 = "plainword@" + "x.iam.gserviceaccount.com"      # first 8 chars are a plain word
SHA = "ABCDEF0123456789" * 2 + "ABCDEF01"
UDID = "00001234-" + "00AB12CD34EF5678"
HOMEP = "/" + "Users" + "/someone"                    # an absolute home path, built in pieces
CORP = "me@" + "corp.io"
TOK = "a1B2c3D4" * 5                                   # 40 alphanumerics


def run(args, cwd=None, env=None):
    r = subprocess.run([PY] + args, cwd=cwd, env=env, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def main():
    ap = argparse.ArgumentParser(description="Self-test the scripting kit.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not shutil.which("git"):
        print("test_scripting: git not found", file=sys.stderr)
        return 2

    results = []

    def case(name, ok, detail=""):
        results.append({"case": name, "ok": bool(ok), "detail": detail})

    tmp = tempfile.mkdtemp(prefix="test-scripting-")
    try:
        cfg = os.path.join(tmp, "config.env")
        with open(cfg, "w") as f:
            f.write("svc_email=%s\nsvc_email2=%s\nsigning_sha1=%s\ndevice_udid=%s\n" % (EMAIL, EMAIL2, SHA, UDID))
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("DETERMINISTIC_SCRIPTS", "XDG_", "APPDATA"))}
        env.update(DETERMINISTIC_SCRIPTS_CONFIG=cfg, XDG_CONFIG_HOME=os.path.join(tmp, "xdg"),
                   APPDATA=os.path.join(tmp, "xdg"))
        scan = os.path.join(HERE, "scan_sensitive.py")
        scrub = os.path.join(HERE, "scrub_sensitive.py")

        def scan_text(text, extra=()):
            p = os.path.join(tmp, "probe.md")
            with open(p, "w") as f:
                f.write(text)
            code, out, _ = run([scan, p, "--json", *extra], env=env)
            return code, [h["rule"] for h in json.loads(out)["hits"]]

        # --- scan: rules fire on real leaks
        case("full config value", "config-value" in scan_text("key %s here" % SHA)[1])
        case("truncated fingerprint", scan_text("signed by 5DEF…".replace("5DEF", SHA[:8]))[1] == ["config-prefix"])
        case("udid", "ios-udid" in scan_text("device " + "00008999-" + "000A11B22C33D44E")[1])
        case("home path", scan_text("see %s/x" % HOMEP)[1] == ["home-path"])
        case("email", scan_text("mail " + CORP)[1] == ["email"])
        case("private key", scan_text("-----BEGIN " + "RSA PRIVATE KEY-----")[1] == ["private-key"])
        # --- scan: and stay quiet on look-alikes
        case("plain word sharing an email's prefix is NOT a hit",
             scan_text("plainword runs; the plainword tool")[1] == [])
        case("example.com is not a hit", scan_text("me@example.com")[1] == [])
        case("~ path is not a hit", scan_text("see ~/x and /Users/<name>/x")[1] == [])
        case("git sha (lowercase) is not a fingerprint", scan_text("abcdef0123456789abcdef0123456789abcdef01")[1] == [])

        # --- allowlist: glob-scoped
        allow = os.path.join(tmp, "allow")
        with open(allow, "w") as f:
            f.write("*probe.md :: PRIVATE KEY\n")
        case("glob allowlist silences its file",
             scan_text("-----BEGIN " + "PRIVATE KEY-----", ["--allow-file", allow])[1] == [])

        # --- allowlist globs are repo-relative in path mode, from any cwd
        grepo = os.path.join(tmp, "grepo")
        os.makedirs(os.path.join(grepo, "sub"))
        subprocess.run(["git", "init", "-q"], cwd=grepo, capture_output=True)
        with open(os.path.join(grepo, ".sensitive-allow"), "w") as f:
            f.write("sub/fake.md :: ^me@corp\\.io$\nsub/fake.md :: ^%s$\n" % HOMEP)  # 2nd line: a hit if the allowlist were scanned
        with open(os.path.join(grepo, "sub", "fake.md"), "w") as f:
            f.write(CORP + "\n")
        code_dot, _, _ = run([scan, "."], cwd=grepo, env=env)
        code_sub, _, _ = run([scan, "fake.md"], cwd=os.path.join(grepo, "sub"), env=env)
        case("glob matches via '.' from the root", code_dot == 0)
        case("glob matches from a subdirectory", code_sub == 0)
        case("the allowlist file itself is not scanned", code_dot == 0)

        # --- scrub: replaces leaks, leaves words alone
        doc = os.path.join(tmp, "doc.md")
        with open(doc, "w") as f:
            f.write("plainword uses %s and %s, cert %s… at %s/work/x\n" % (EMAIL2, EMAIL, SHA[:8], HOMEP))
        code, _, _ = run([scrub, doc, "--write"], env=env)
        text = open(doc).read()
        case("scrub keeps the plain word", text.startswith("plainword uses "), text)
        case("scrub replaces emails", "<config:svc_email2>" in text and "<config:svc_email>" in text, text)
        case("scrub replaces a truncated fingerprint", "<config:signing_sha1>" in text and SHA[:8] not in text, text)
        case("scrub home path to ~", "~/work/x" in text and "someone" not in text, text)
        code, _, _ = run([scan, doc], env=env)
        case("scrubbed file scans clean", code == 0)

        # --- pre-commit: added-lines mode
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        g = lambda *x: subprocess.run(["git", *x], cwd=repo, capture_output=True, env=env)
        g("init", "-q")
        g("config", "user.email", "t@example.com")
        g("config", "user.name", "t")
        legacy = os.path.join(repo, "a.md")
        with open(legacy, "w") as f:
            f.write("legacy %s/x\n" % HOMEP)
        g("add", "a.md")
        g("commit", "-qm", "legacy", "--no-verify")
        with open(legacy, "a") as f:
            f.write("a clean new line\n")
        g("add", "a.md")
        c_added, _, _ = run([scan, "--staged", "--added"], cwd=repo, env=env)
        c_full, _, _ = run([scan, "--staged"], cwd=repo, env=env)
        case("added-lines mode ignores a legacy line", c_added == 0)
        case("full staged mode still sees it", c_full == 1)
        with open(legacy, "a") as f:
            f.write("new leak %s\n" % SHA)
        g("add", "a.md")
        c_added2, _, _ = run([scan, "--staged", "--added"], cwd=repo, env=env)
        case("added-lines mode blocks a new leak", c_added2 == 1)

        # --- new_script: never bakes a home path
        code, out, _ = run([os.path.join(HERE, "new_script.py"), "--dir", os.path.join(tmp, "x"),
                            "--name", "t", "--summary", "t", "--config"], env=env)
        home_name = os.path.basename(os.path.expanduser("~"))
        under_home = home_name in os.path.relpath(HERE, os.path.join(tmp, "x")).split(os.sep)
        case("scaffold outside the tree with --config is refused (when the kit is under home)",
             code == 2 or not under_home)

        # --- check_scripts: module exempt from the contract, still scanned
        mod = os.path.join(tmp, "lib_mod.py")
        with open(mod, "w") as f:
            f.write('"""lib"""\nX = 1\n')
        code, out, _ = run([os.path.join(HERE, "check_scripts.py"), mod, "--json"], env=env)
        case("module is exempt from the contract", code == 0, out)
        with open(mod, "a") as f:
            f.write('P = "%s/x"\n' % HOMEP)
        code, out, _ = run([os.path.join(HERE, "check_scripts.py"), mod, "--json"], env=env)
        case("module is still scanned for personal values", code == 1, out)

        # --- portability: shell needs a stated exception, python does not
        code, _, _ = run([os.path.join(HERE, "new_script.py"), "--dir", tmp, "--name", "s",
                          "--summary", "s", "--lang", "bash"], env=env)
        case("scaffold bash without --reason is refused", code == 2)
        sh = os.path.join(tmp, "plain.sh")
        with open(sh, "w") as f:
            f.write("#!/usr/bin/env bash\n# a\n# b\n# c\n")
        code, out, _ = run([os.path.join(HERE, "check_scripts.py"), sh, "--json"], env=env)
        case("shell script without an exception fails lang",
             any(v["check"] == "lang" for v in json.loads(out)["violations"]), out)
        with open(sh, "a") as f:
            f.write("# portability-exception: sourced by bash callers\n")
        code, out, _ = run([os.path.join(HERE, "check_scripts.py"), sh, "--json"], env=env)
        case("shell script with an exception passes lang",
             not any(v["check"] == "lang" for v in json.loads(out)["violations"]), out)

        # --- generic token and secret rules
        for label, text in [("ghp_ token", "t " + "ghp" + "_" + TOK[:36]),
                            ("github_pat_ token", "t " + "github" + "_pat_" + TOK[:30]),
                            ("sk- key", "k " + "sk" + "-" + TOK[:30]),
                            ("AWS key id", "k " + "AKIA" + "ABCDEFGHIJKLMNOP"),
                            ("slack token", "t " + "xox" + "b-1234567890-abcdefghij"),
                            ("bearer header", "Authorization: " + "Bearer " + TOK[:30])]:
            case(label, scan_text(text)[1] == ["token"], str(scan_text(text)))
        case("password assignment", scan_text("db_" + "password" + "=" + '"hunter2hunter2"')[1] == ["secret-assign"])
        case("password read from code is not a hit", scan_text("pass" + "word = args.pass" + "word")[1] == [])
        case("placeholder secret is not a hit", scan_text("api_key = " + '"changeme-please"')[1] == [])
        case(".env secret", scan_text("STRIPE_" + "SECRET_KEY" + "=abcd1234efgh")[1] == ["env-secret"])
        case(".env variable reference is not a hit", scan_text("API_" + "TOKEN" + "=$OTHER_VALUE")[1] == [])
        case("noreply email is not a hit", scan_text("mail noreply@" + "corp.io and 12345+x@users.noreply.github.com")[1] == [])
        case("windows home path", scan_text("C:" + "\\" + "Users" + "\\" + "someone" + "\\x")[1] == ["home-path"])

        # --- extra patterns and literals: env, config key, patterns file
        env_x = dict(env, DETERMINISTIC_SCRIPTS_PATTERNS="ticket :: \\bPRJ-\\d{4}\\b",
                     DETERMINISTIC_SCRIPTS_LITERALS="codename-zebra, other")
        p = os.path.join(tmp, "x.md")
        with open(p, "w") as f:
            f.write("see PRJ-1234 and codename-zebra\n")
        _, out, _ = run([scan, p, "--json"], env=env_x)
        case("env pattern and literal fire", sorted(h["rule"] for h in json.loads(out)["hits"]) == ["config-value", "ticket"], out)
        _, out, _ = run([scan, p, "--json", "--no-config"], env=env_x)
        case("--no-config ignores extras", json.loads(out)["hits"] == [], out)
        pf = os.path.join(tmp, "pats.txt")
        with open(pf, "w") as f:
            f.write("# comment\nhost :: \\bbuild-box-\\d+\nliteral: project-nightjar\n")
        env_f = dict(env, DETERMINISTIC_SCRIPTS_PATTERNS_FILE=pf)
        with open(p, "w") as f:
            f.write("on build-box-7 for project-nightjar\n")
        _, out, _ = run([scan, p, "--json"], env=env_f)
        case("patterns file regex and literal fire", sorted(h["rule"] for h in json.loads(out)["hits"]) == ["config-value", "host"], out)
        with open(cfg, "a") as f:
            f.write("pattern_ticket=\\bZZ-\\d+\\b\n")
        with open(p, "w") as f:
            f.write("ZZ-42\n")
        _, out, _ = run([scan, p, "--json"], env=env)
        case("config pattern_* key is a regex", [h["rule"] for h in json.loads(out)["hits"]] == ["pattern_ticket"], out)
        code, _, _ = run([scrub, p, "--write"], env=env)
        case("scrub never rewrites a pattern key", open(p).read() == "ZZ-42\n")

        # --- homecfg reads the config
        hc = os.path.join(HERE, "homecfg.py")
        code, out, _ = run([hc, "get", "signing_sha1"], env=env)
        case("homecfg get", code == 0 and out.strip() == SHA, out)
        code, _, _ = run([hc, "get", "no_such_key"], env=env)
        case("homecfg missing key exits 2", code == 2)

        # --- install_hooks: report, --write, --uninstall
        ih = os.path.join(HERE, "install_hooks.py")
        irepo = os.path.join(tmp, "irepo")
        os.makedirs(irepo)
        subprocess.run(["git", "init", "-q"], cwd=irepo, capture_output=True)
        code, _, _ = run([ih, irepo], env=env)
        case("install_hooks reports first and changes nothing", code == 1 and
             subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=irepo,
                            capture_output=True, text=True).stdout.strip() == "")
        code, _, _ = run([ih, irepo, "--write"], env=env)
        case("install_hooks --write installs", code == 0)
        code, _, _ = run([ih, irepo], env=env)
        case("install_hooks reports installed", code == 0)
        code, _, _ = run([ih, irepo, "--uninstall"], env=env)
        case("install_hooks --uninstall without --write only reports", code == 1)
        code, _, _ = run([ih, irepo, "--uninstall", "--write"], env=env)
        case("install_hooks --uninstall --write removes", code == 0 and run([ih, irepo], env=env)[0] == 1)

        # --- check_scripts: extension
        noext = os.path.join(tmp, "tool")
        with open(noext, "w") as f:
            f.write("#!/usr/bin/env python3\n")
        code, out, _ = run([os.path.join(HERE, "check_scripts.py"), noext, "--json"], env=env)
        case("extensionless python script fails extension",
             any(v["check"] == "extension" for v in json.loads(out)["violations"]), out)

        # --- repeat_scan: groups equal command shapes
        hist = os.path.join(tmp, "history.txt")
        with open(hist, "w") as f:
            f.write("git log -n 5 ./a.py\ngit log -n 9 ./b.py\nls\n")
        code, out, _ = run([os.path.join(HERE, "repeat_scan.py"), hist, "--format", "lines", "--json"], env=env)
        cands = json.loads(out)["candidates"] if code == 0 else []
        case("repeat_scan groups equal shapes", len(cands) == 1 and cands[0]["count"] == 2, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in results if not r["ok"]]
    if a.json:
        print(json.dumps({"passed": len(results) - len(failed), "failed": len(failed), "cases": results}))
    else:
        for r in results:
            print("%s  %s%s" % ("ok  " if r["ok"] else "FAIL", r["case"],
                                ("  -- " + r["detail"][:160]) if not r["ok"] and r["detail"] else ""))
        print("%d passed, %d failed" % (len(results) - len(failed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
