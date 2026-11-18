#!/usr/bin/env python3
# device_tap_nudge.py: PostToolUse (Bash) hook nudging toward a plan/flow or the device-runner.
# --help prints usage; --json prints the nudge/state as JSON; exit 0 always when run as a hook.
"""PostToolUse hook (matcher Bash): nudge when device inputs are sent one per turn.

Reads the hook JSON on stdin. A "single step" is one device input (tap, swipe, press, text, key,
back, native ...) through the device CLI or an add-on wrapper, an `adb shell input`, or a bare
`sleep N`, optionally chained with one such step. Anything else (`run <plan>`, other commands)
resets the counter. On the Nth consecutive single step, and every Nth after, it prints
additionalContext telling the model to put the flow in a plan or hand the pass to the
device-runner subagent. Never blocks: exits 0 on every error.

Configuration (the environment first, then ~/.config/device-testing/config.env or
DEVICE_TESTING_CONFIG, the same file the device CLI reads, then the default):
  DEVICE_TAP_NUDGE_EVERY   steps in a row before a nudge (default 3)
  DEVICE_TAP_NUDGE_CLI     regex of the CLI names (default: device.py, device, and any
                           `*-device` wrapper, e.g. myapp-device)
  DEVICE_TAP_NUDGE_STATE   state folder (default: <SCRATCH_ROOT>/device-testing/tap-nudge,
                           SCRATCH_ROOT defaulting to the system temp dir)

Setup (Claude Code): add a PostToolUse hook with matcher "Bash" and the command
  python3 <skills>/device-testing/scripts/device_tap_nudge.py

Usage:
  device_tap_nudge.py < hook.json        hook mode (stdin is the PostToolUse JSON)
  device_tap_nudge.py --selftest         assert the classifier and the counter (temp state dir)
  device_tap_nudge.py --json < hook.json print {"single","count","nudge"} instead of the hook JSON
  device_tap_nudge.py --help

Exit codes: 0 always in hook mode; --selftest exits 1 on a failed assertion.
"""
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
DEFAULT_CLI = r"(?:device(?:\.py)?|[\w.]+-device|[\w.]+-device\.py)"
SUBS = r"(?:tap|swipe|press|text|key|back|keyevent|long-?press|scroll|drag)"
MSG = ("%d single device inputs in a row -- each turn re-reads the whole context. Put the rest of "
       "this flow in a plan and run it once with `<device cli> run <plan>` (see the "
       "device-testing skill and the app add-on's flows), replace sleep with `wait <regex>`, or "
       "hand the pass to the device-runner subagent.")


def _config():
    """device_testing.config from this scripts folder, or None when it cannot load."""
    try:
        sys.path.insert(0, HERE)
        from device_testing import config
        return config
    except Exception:
        return None


def setting(name, default):
    cfg = _config()
    if cfg:
        try:
            return cfg.get(name, default)
        except Exception:
            pass
    return os.environ.get(name) or default


def state_dir():
    explicit = os.environ.get("DEVICE_TAP_NUDGE_STATE")
    if explicit:
        return explicit
    cfg = _config()
    if cfg:
        try:
            return cfg.scratch("tap-nudge")
        except Exception:
            pass
    return os.path.join(tempfile.gettempdir(), "device-testing", "tap-nudge")


def build_regexes(cli):
    dev = r"(?:\S*/)?(?:python3?\s+)?(?:\S*/)?(?:" + cli + r")"
    re_device = re.compile(
        r"^" + dev + r"(?:\s+(?:--device|-d|-s)[=\s]\S+)*(?:\s+native)?\s+" + SUBS + r"(?:\s|$)")
    re_adb = re.compile(r"^(?:\S*/)?adb(?:\s+-[sde]\s*\S*)*\s+shell\s+input(?:\s|$)")
    re_sleep = re.compile(r"^sleep\s+[\d.]+[smh]?$")
    return re_device, re_adb, re_sleep


def is_single_step(cmd, cli=DEFAULT_CLI):
    regs = build_regexes(cli)
    parts = [p for p in re.split(r"\s*(?:&&|;|\|\|)\s*", cmd.strip()) if p.strip()]
    if not parts or len(parts) > 2:
        return False
    return all(any(r.match(p.strip()) for r in regs) for p in parts)


def update(folder, session, single):
    os.makedirs(folder, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session or "none")[:80]
    path = os.path.join(folder, safe + ".json")
    count = 0
    try:
        with open(path) as f:
            count = int(json.load(f).get("count", 0))
    except Exception:
        pass
    count = count + 1 if single else 0
    with open(path, "w") as f:
        json.dump({"count": count}, f)
    return count


def selftest():
    yes = ["device.py tap 100 200", "myapp-device swipe 1 2 3 4", "device --device R5 press back",
           "/x/y/device.py --device abc tap 5 5", "python3 /x/device.py native tap 1 2",
           "myapp-device text hello", "adb -s X shell input tap 1 2", "sleep 3",
           "sleep 2 && myapp-device tap 1 1", "device back"]
    no = ["device.py run flows/a.plan", "myapp-device --device X run plan", "ls -la", "git status",
          "device log", "device wait foo", "", "sleep 3 && ls", "a && b && device tap 1 1"]
    for c in yes:
        assert is_single_step(c), c
    for c in no:
        assert not is_single_step(c), c
    assert is_single_step("pd tap 1 1", r"pd")
    with tempfile.TemporaryDirectory() as tmp:
        seq = [update(tmp, "s", True) for _ in range(4)] + [update(tmp, "s", False), update(tmp, "s", True)]
        assert seq == [1, 2, 3, 4, 0, 1], seq
        assert update(tmp, "other", True) == 1
    print("selftest ok: %d single, %d not, counter ok" % (len(yes), len(no)))


def main():
    argv = sys.argv[1:]
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return
    if "--selftest" in argv:
        selftest()
        return
    data = json.load(sys.stdin)
    cmd = (data.get("tool_input") or {}).get("command") or ""
    every = max(1, int(setting("DEVICE_TAP_NUDGE_EVERY", "3")))
    single = is_single_step(cmd, setting("DEVICE_TAP_NUDGE_CLI", DEFAULT_CLI))
    count = update(state_dir(), data.get("session_id", ""), single)
    nudge = bool(count and count % every == 0)
    if "--json" in argv:
        print(json.dumps({"single": single, "count": count, "nudge": nudge}))
    elif nudge:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": MSG % count}}))


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("selftest FAILED: %s" % e, file=sys.stderr)
        sys.exit(1)
    except BaseException:
        pass
    sys.exit(0)
