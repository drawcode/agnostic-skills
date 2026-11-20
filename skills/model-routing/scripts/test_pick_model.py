#!/usr/bin/env python3
"""Tests for pick_model.py: briefs land on the tier the tiered-delegation rules give them

Run: python3 scripts/delegation/test_pick_model.py   (stdlib unittest; --help for its options)
Exit codes: 0 all pass, 1 a test failed. No --json: unittest output only.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pick_model  # noqa: E402

CASES = [
    # sprinter: pure mechanics, target handed in
    ("grep every caller of UIUtil.SetText under Assets/Code and count them per file", "sprinter"),
    ("List all .uxml files in Assets/Views and dump their root element names", "sprinter"),
    ("Rename FooPanel to BarPanel in these three files. Do not refactor anything else.", "sprinter"),
    ("Run the script tools/locale-lint/lint.py and return its output", "sprinter"),
    ("Convert locales/fr.json to the flat key format; never reword a value", "sprinter"),
    ("Find all TODO comments in src/ and extract file:line", "sprinter"),
    # workhorse: judgment about the code, or anything that ranks or summarizes
    ("Implement the retry in NetClient.cs following the existing backoff helper", "workhorse"),
    ("Summarize the five most important findings from these logs", "workhorse"),
    ("Grep the callers, then rank them by how risky the change is", "workhorse"),
    ("Review this diff for correctness bugs", "workhorse"),
    ("Translate the new strings in en.json into de and fr", "workhorse"),
    ("Update the header comment in Foo.cs", "workhorse"),  # no signal: default
    ("List files without tests and rank them by risk", "workhorse"),
    ("Rename X to Y; do not refactor and then summarize the diff", "workhorse"),
    # architect: scoping, root cause, decisions
    ("Find the root cause of the blank Results screen after CONTINUE", "architect"),
    ("Investigate why the build fails on Windows only, then list the failing steps", "architect"),
    ("Design the save-migration approach and decide between the two trade-offs", "architect"),
]


class Classify(unittest.TestCase):
    def test_cases(self):
        for brief, tier in CASES:
            with self.subTest(brief=brief):
                does, _ = pick_model.classify(brief)
                self.assertEqual(pick_model.TIER_OF[does], tier)

    def test_negated_judgment_does_not_bump(self):
        does, sig = pick_model.classify("Replace X with Y in a.py. Don't review or explain.")
        self.assertEqual(does, "edit", sig)

    def test_alias_is_family_not_dated_id(self):
        for alias in pick_model.ALIAS.values():
            self.assertNotRegex(alias, r"\d")


UNITY_PROFILES = {
    "profiles": {"unity-editor-cli": {"model": "sonnet", "fixed_tokens": 9683},
                 "unity-cli-runner": {"model": "haiku", "fixed_tokens": 5400}},
    "needs": {"unity": {"light": "unity-cli-runner", "light_reason": "light",
                        "heavy": "unity-editor-cli", "heavy_reason": "heavy"}},
}


class Cli(unittest.TestCase):
    script_dir = HERE
    env_extra = {}

    def run_cli(self, *args, stdin=None):
        env = dict(os.environ)
        env.pop("TIERED_DELEGATION_PROFILES", None)
        env.update(self.env_extra)
        return subprocess.run([sys.executable, str(self.script_dir / "pick_model.py"), *args],
                              input=stdin, capture_output=True, text=True, env=env)

    def test_json_contract(self):
        p = self.run_cli("--task", "count the files in docs/", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout)
        self.assertEqual((r["model"], r["tier"], r["agent"]), ("haiku", "sprinter", "scout"))
        self.assertEqual(set(r), {"model", "tier", "does", "agent", "fixed_tokens",
                                  "signals", "reason"})

    def test_stdin_and_needs(self):
        p = self.run_cli("--task", "-", "--needs", "web", "--json",
                         stdin="search for the release notes and extract the dates")
        r = json.loads(p.stdout)
        self.assertEqual((r["model"], r["agent"]), ("haiku", "general-purpose"))

    def test_any_harness_prints_role(self):
        p = self.run_cli("--does", "plan", "--harness", "any", "--json")
        self.assertEqual(json.loads(p.stdout)["model"], "strongest")

    def test_bad_input_exits_2(self):
        self.assertEqual(self.run_cli("--task", "x", "--needs", "telepathy").returncode, 2)
        self.assertEqual(self.run_cli("--task", "   ").returncode, 2)


class ProfilesFile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bare = self.tmp / "bare"
        self.bare.mkdir()
        for name in ("pick_agent.py", "pick_model.py"):
            shutil.copy(HERE / name, self.bare / name)

    def run_cli(self, *args, env_path=None):
        env = dict(os.environ)
        env.pop("TIERED_DELEGATION_PROFILES", None)
        env["HOME"] = str(self.tmp)  # keep ~/.config out of the lookup
        if env_path:
            env["TIERED_DELEGATION_PROFILES"] = str(env_path)
        return subprocess.run([sys.executable, str(self.bare / "pick_model.py"), *args],
                              capture_output=True, text=True, env=env)

    def test_unity_routes_by_kind(self):
        f = self.tmp / "profiles.json"
        f.write_text(json.dumps(UNITY_PROFILES), encoding="utf-8")
        r = json.loads(self.run_cli("--task", "run the script and dump the console", "--needs",
                                    "unity", "--json", env_path=f).stdout)
        self.assertEqual((r["agent"], r["tier"], r["fixed_tokens"]),
                         ("unity-cli-runner", "sprinter", 5400))
        r = json.loads(self.run_cli("--task", "find the root cause of the hang", "--needs",
                                    "unity", "--json", env_path=f).stdout)
        self.assertEqual(r["agent"], "unity-editor-cli")

    def test_no_profiles_file_rejects_extra_need(self):
        p = self.run_cli("--does", "run", "--needs", "unity")
        self.assertEqual(p.returncode, 2)
        self.assertIn("unknown --needs unity", p.stderr)

    def test_malformed_file_falls_back_to_builtins(self):
        f = self.tmp / "bad.json"
        f.write_text("{not json", encoding="utf-8")
        p = self.run_cli("--does", "run", "--json", env_path=f)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["agent"], "runner")
        self.assertIn("warning", p.stderr)
        self.assertEqual(self.run_cli("--does", "run", "--needs", "unity",
                                      env_path=f).returncode, 2)


if __name__ == "__main__":
    unittest.main()
