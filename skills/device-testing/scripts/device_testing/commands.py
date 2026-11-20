"""Every command, written once over the backend primitives, so arguments and output are the same on
every platform. Output strings are stable: harnesses grep them.

Add-ons (DEVICE_TESTING_ADDONS, ':'-separated .py paths) define `register(api)` and call
  api.command(name, fn, usage)   fn(ctx, args) -> exit code
  api.hook(event, fn)            fn(ctx); events: "preflight", "pre-record"
An add-on command runs on every platform, because it only uses ctx.device primitives and ctx.run.
"""
import datetime
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import time

from . import config, inputserver, nav, ocr, sheet
from .backends import Unsupported


class Fail(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


class Context:
    def __init__(self, device):
        self.device = device
        self.alias = config.alias_for(device.serial)
        self.platform = device.name

    def nav_profile(self):
        return config.resolve_in_skills(config.per_device(self.alias, "NAV_PROFILE"))

    def run(self, command, args=()):
        """Run another command, e.g. an add-on calling `nav`."""
        return dispatch(self, command, list(args))

    def shot(self, out=None):
        out = out or config.scratch("shots", time.strftime("%Y%m%d-%H%M%S") + ".png")
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        self.device.screenshot(out)
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            raise Fail("screenshot wrote nothing -- is the screen on?")
        return out


COMMANDS = {}
HOOKS = {"preflight": [], "pre-record": []}


def command(name, usage=""):
    def wrap(fn):
        COMMANDS[name] = (fn, usage)
        return fn
    return wrap


class AddonApi:
    def command(self, name, fn, usage=""):
        COMMANDS[name] = (fn, usage)

    def hook(self, event, fn):
        HOOKS.setdefault(event, []).append(fn)


def load_addons():
    for path in filter(None, config.get("DEVICE_TESTING_ADDONS", "").split(":")):
        spec = importlib.util.spec_from_file_location(f"addon_{abs(hash(path))}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.register(AddonApi())


def run_hooks(ctx, event):
    for fn in HOOKS.get(event, []):
        fn(ctx)


def dispatch(ctx, name, args):
    if name not in COMMANDS:
        raise Fail(f"unknown command '{name}' -- try `help`")
    return COMMANDS[name][0](ctx, args) or 0


def flag(args, name, default=None, has_value=True):
    """Pull `--name value` (or bare `--name`) out of args; returns the value or default."""
    if name not in args:
        return default
    i = args.index(name)
    if not has_value:
        del args[i]
        return True
    value = args[i + 1] if i + 1 < len(args) else default
    del args[i:i + 2]
    return value


def emit(body):
    print(json.dumps(body))
    return 0 if body.get("ok", True) else 1


# --- state ---------------------------------------------------------------------------------------


def wda_line(d):
    """iOS only, and only when a WebDriverAgent runner is configured: when its signing profile lapses."""
    if d.name != "ios" or not config.get("DEVICE_IOS_WDA_BUNDLE_PREFIX"):
        return
    try:
        expiry = d.wda_expiry()
    except (RuntimeError, Unsupported, subprocess.CalledProcessError, ValueError) as e:
        print(f"wda       (unresolved: {e})")
        return
    if expiry is None:
        print("wda       not installed")
        return
    days = (expiry - datetime.datetime.utcnow()).total_seconds() / 86400
    print(f"wda       expires {expiry:%Y-%m-%d} ({days:.0f} days)")
    if days < 2:
        print("          !! the runner's profile lapses within 2 days: rerun `native build` and `native install`")


@command("check", "resolved config, device, app, nav profile")
def cmd_check(ctx, args):
    d = ctx.device
    print(f"config    {config.PATH} {'' if os.path.exists(config.PATH) else '(missing)'}")
    print(f"device    {d.serial} {f'({ctx.alias})' if ctx.alias else ''}")
    print(f"platform  {ctx.platform}")
    print(f"aliases   {' '.join(sorted(config.aliases()))}")
    try:
        print(f"app       {d.app_id()}")
    except RuntimeError as e:
        print(f"app       ({e})")
    print(f"scratch   {config.scratch()}")
    if hasattr(d, "input_route"):
        try:
            print(f"input     {d.input_route()}")
        except (RuntimeError, subprocess.CalledProcessError, ValueError) as e:
            print(f"input     (unresolved: {e})")
    profile = ctx.nav_profile()
    print(f"nav       {profile or '(none; nav is unavailable)'} {'' if not profile or os.path.exists(profile) else '(missing)'}")
    if profile and os.path.exists(profile):
        try:
            nav.check_screen(profile, d.size(), d.density())
        except nav.NavError as e:
            print(f"          !! {e}")
        except Unsupported:
            pass
    wda_line(d)
    print(f"buffer    {config.get('DEVICE_LOGCAT_BUFFER', '32M')}")
    try:
        print("installed " + (" ".join(d.version()) if d.installed() else "no -- the app is not on this device"))
    except (RuntimeError, Unsupported):
        pass


# --- quiet ---------------------------------------------------------------------------------------

def _quiet_steps(state):
    return "; ".join(f"{i}. {step}" for i, step in enumerate(state.get("steps", []), 1))


def ensure_quiet(ctx, out=None):
    """Before a pass, a recording or a screenshot: make sure no notification can leak into it.
    Automatic where the platform allows (Android); otherwise warn, or fail under DEVICE_QUIET_REQUIRED=1.
    Never fails by default, even when the platform cannot be checked."""
    out = out or sys.stdout
    required = config.get("DEVICE_QUIET_REQUIRED") == "1"
    try:
        state = ctx.device.quiet_state()
        if not state["manual"] and not state["quiet"]:
            state = ctx.device.quiet_on()
        if state["quiet"]:
            print(f"quiet    on ({state['detail']})", file=out)
            return state
        problem = state["detail"]
        steps = f"{_quiet_steps(state)}; then `quiet on --confirmed`" if state["manual"] else "run `quiet on`"
    except Unsupported:
        return None
    except Exception as e:  # a failed check must not stop a pass unless the pass demands quiet
        problem, steps = f"could not check ({e})", "run `quiet status`"
    if required:
        raise Fail(f"notifications not silenced ({problem}) -- {steps}")
    print(f"!! notifications not silenced ({problem}) -- {steps}", file=out)
    return None


_SHOT_WARNED = []


def warn_quiet_once(ctx):
    """shot: warn once per process, from the state file only (iOS), so shot stays fast."""
    if _SHOT_WARNED or ctx.platform != "ios":
        return
    _SHOT_WARNED.append(True)
    try:
        state = ctx.device.quiet_state()
    except Exception:
        return
    if not state["quiet"]:
        print(f"!! notifications not silenced ({state['detail']}) -- {_quiet_steps(state)}; "
              "then `quiet on --confirmed`", file=sys.stderr)


@command("quiet", "on|off|status [--confirmed]  silence notifications before recording or screenshots: Android sets "
                  "do not disturb, heads-up off, status-bar icons hidden (off restores them); iOS has no host-side "
                  "switch, so it prints the steps and --confirmed records that they were done (stale after 12 h)")
def cmd_quiet(ctx, args):
    args = list(args)
    confirmed = flag(args, "--confirmed", False, has_value=False)
    if len(args) != 1 or args[0] not in ("on", "off", "status"):
        raise Fail("usage: quiet on|off|status [--confirmed]")
    action, d = args[0], ctx.device
    state = {"on": lambda: d.quiet_on(confirmed=bool(confirmed)), "off": d.quiet_off, "status": d.quiet_state}[action]()
    word = "on" if state["quiet"] else "off"
    if state["manual"]:
        if action == "off":
            print("quiet    off (cleared the confirmation)")
            print(f"undo     {_quiet_steps(state)}")
            return 0
        if state["quiet"]:
            print(f"quiet    on ({state['detail']})")
            return 0
        print(f"quiet    off ({state['detail']})")
        print(f"steps    {_quiet_steps(state)}; then `quiet on --confirmed`")
        return 0 if action == "on" else 2
    print(f"quiet    {word} ({state['detail']})")
    return 0 if action != "status" or state["quiet"] else 2


@command("preflight", "[--keep-logs]  clean room: unlocked, awake, logs ready, app stopped, logs cleared")
def cmd_preflight(ctx, args):
    d = ctx.device
    keep = flag(args, "--keep-logs", False, has_value=False)
    # A locked screen taps the lockscreen and records the lockscreen.
    if d.locked():
        raise Fail("device is locked -- unlock it before the pass")
    awake = d.keep_awake()
    buffer = config.get("DEVICE_LOGCAT_BUFFER", "32M")
    d.log_prepare(buffer)
    cmd_stop(ctx, [])
    if not keep:
        d.log_clear()
        print("cleared  logs")
    ensure_quiet(ctx)
    run_hooks(ctx, "preflight")
    print(f"ready    buffer {buffer}, {awake}, {d.app_id()} stopped")


@command("awake", "on|off  keep the device awake for a pass (preflight turns it on); off releases what iOS holds")
def cmd_awake(ctx, args):
    if not args or args[0] not in ("on", "off"):
        raise Fail("usage: awake on|off")
    print(ctx.device.keep_awake() if args[0] == "on" else ctx.device.release_awake())
    return 0


@command("stop", "stop the app, then PROVE the process is gone")
def cmd_stop(ctx, args):
    d = ctx.device
    # Stopping an app that is not installed succeeds and proves nothing.
    if not d.installed():
        raise Fail(f"{d.app_id()} is not installed on this device")
    d.stop()
    for _ in range(20):
        if d.pid() is None:
            print(f"stopped  {d.app_id()}")
            return 0
        time.sleep(0.5)
    raise Fail(f"{d.app_id()} is still running after stop")


@command("bg", "send it to the background, then prove it lost the focus")
def cmd_bg(ctx, args):
    d = ctx.device
    if d.pid() is None:
        raise Fail(f"{d.app_id()} is not running -- nothing to background")
    d.background()
    end = time.time() + 10
    while time.time() < end:
        if not d.focus()[0]:
            print(f"backgrounded {d.app_id()}")
            return 0
        time.sleep(0.5)
    raise Fail(f"{d.app_id()} still owns the focus after HOME")


@command("resume-check", "[--hold S] [--settle S] [path]  background, return, settled screenshot")
def cmd_resume_check(ctx, args):
    d = ctx.device
    hold = float(flag(args, "--hold", 3))
    settle = float(flag(args, "--settle", 3))
    before = d.pid()
    cmd_bg(ctx, [])
    time.sleep(hold)
    d.launch()
    time.sleep(settle)  # a frame grabbed mid-splash reads as a rendering bug and is not one
    after = d.pid()
    out = args[0] if args else config.scratch("shots", "resume-" + time.strftime("%Y%m%d-%H%M%S") + ".png")
    print(ctx.shot(out))
    if before != after:
        print(f"warning: pid {before} -> {after}, the app was killed while backgrounded -- that is a cold "
              f"start, not a resume", file=sys.stderr)


@command("version", "installed version of the app")
def cmd_version(ctx, args):
    print("\n".join(ctx.device.version()))


@command("focus", "what is in front; non-zero if it is not the app")
def cmd_focus(ctx, args):
    ours, description = ctx.device.focus()
    print(description)
    return 0 if ours else 2


@command("launch", "[--timeout S] [--input-server] [--url URL] [--accept-alerts [S]]  launch and wait until the app "
                   "is in front; --url opens a deep link in it; --accept-alerts watches S seconds (default 20) "
                   "after launch and accepts each system alert, through WebDriverAgent (iOS only, opt-in)")
def cmd_launch(ctx, args):
    d = ctx.device
    timeout = float(flag(args, "--timeout", 30))
    server = flag(args, "--input-server", False, has_value=False)
    url = flag(args, "--url")
    accept = None
    if "--accept-alerts" in args:
        i = args.index("--accept-alerts")
        has_value = i + 1 < len(args) and re.fullmatch(r"\d+(\.\d+)?", args[i + 1])
        accept = float(args[i + 1]) if has_value else 20.0
        del args[i:i + (2 if has_value else 1)]
    d.launch(inputserver.launch_environment()[ctx.platform] if server else None, url=url)
    end = time.time() + timeout
    while time.time() < end:
        if d.focus()[0]:
            suffix = f" (input server on device port {inputserver.device_port()})" if server else ""
            print(f"launched {d.app_id()}{suffix}")
            if accept:
                watch_alerts(ctx, accept, "accept")
            return 0
        time.sleep(0.5)
    raise Fail(f"{d.app_id()} never took the focus within {int(timeout)}s")


# --- builds --------------------------------------------------------------------------------------


@command("build-symbol", "<build> <symbol>  is the change compiled into this APK/IPA")
def cmd_build_symbol(ctx, args):
    if len(args) < 2 or not os.path.isfile(args[0]):
        raise Fail("usage: build-symbol <apk|ipa> <symbol>")
    data = ctx.device.build_symbol_file(args[0])
    if args[1].encode() in data:
        print(f"present  {args[1]}")
        return 0
    print(f"ABSENT   {args[1]} -- this build predates the change", file=sys.stderr)
    return 2


COMMANDS["apk-symbol"] = (cmd_build_symbol, "<apk> <symbol>  alias of build-symbol")


@command("install", "<build> [--symbol SYM]  refuse unless SYM is in the build, then verify")
def cmd_install(ctx, args):
    d = ctx.device
    symbol = flag(args, "--symbol")
    if not args or not os.path.isfile(args[0]):
        raise Fail(f"no such build: {args[0] if args else ''}")
    if symbol and cmd_build_symbol(ctx, [args[0], symbol]) != 0:
        raise Fail(f"refusing to install a build without {symbol}")
    before = d.last_update()
    d.install(args[0])
    if before and before == d.last_update():
        print("WARNING  the installed build did not change -- the install may not have replaced anything",
              file=sys.stderr)
    cmd_version(ctx, [])


# --- evidence-driven input -----------------------------------------------------------------------


def log_count(ctx, regex):
    pattern = re.compile(regex)
    return sum(1 for line in ctx.device.log_lines() if pattern.search(line))


@command("count", "<regex>  log matches right now (the baseline before an action)")
def cmd_count(ctx, args):
    if not args:
        raise Fail("usage: count <regex>")
    print(log_count(ctx, args[0]))


@command("log", "<regex> [n]  the last n matching lines (default 40)")
def cmd_log(ctx, args):
    if not args:
        raise Fail("usage: log <regex> [n]")
    pattern = re.compile(args[0])
    matches = [line for line in ctx.device.log_lines() if pattern.search(line)]
    print("\n".join(matches[-int(args[1] if len(args) > 1 else 40):]))


@command("wait", "<regex> [baseline] [timeout]  poll until the count passes baseline (0, 15s)")
def cmd_wait(ctx, args):
    if not args:
        raise Fail("usage: wait <regex> [baseline] [timeout]")
    regex, base = args[0], int(args[1]) if len(args) > 1 else 0
    timeout = float(args[2]) if len(args) > 2 else 15
    end = time.time() + timeout
    while time.time() < end:
        now = log_count(ctx, regex)
        if now > base:
            print(f"saw      {regex} ({base} -> {now})")
            return 0
        time.sleep(0.3)
    print(f"TIMEOUT  {regex} did not appear in {int(timeout)}s (still at {base})", file=sys.stderr)
    return 2


# How each action reads in output; harnesses grep the wording the original script used.
INPUT_WORDS = {"tap": "tap", "swipe": "swipe", "key": "keyevent", "native_tap": "native tap",
               "native_hold": "native hold", "native_swipe": "native swipe"}


def describe(action, values):
    return f"input {INPUT_WORDS[action]} {' '.join(str(v) for v in values)}"


def act_and_wait(ctx, regex, timeout, action, values):
    """Take the baseline, act, then prove the screen changed. A focus-stealing card makes a blind
    loop finish empty and look clean, so an unmet wait is an error, not a warning."""
    base = log_count(ctx, regex) if regex else 0
    getattr(ctx.device, action)(*values)
    if not regex:
        print(f"sent     {describe(action, values)} (unverified -- no wait given)")
        return 0
    return cmd_wait(ctx, [regex, str(base), str(timeout)])


def _points(ctx, args, count, norm):
    try:
        values = [float(a) for a in args[:count]]
    except ValueError:
        raise Fail("coordinates must be numbers")
    if len(values) < count:
        raise Fail(f"need {count} coordinates")
    if norm:
        w, h = ctx.device.size()
        values = [v * (w if i % 2 == 0 else h) for i, v in enumerate(values)]
    return values


@command("tap", "<x> <y> [--wait RE] [--timeout S] [--norm]  tap; pixels of the screen, or 0-1 with --norm")
def cmd_tap(ctx, args):
    regex, timeout = flag(args, "--wait"), flag(args, "--timeout", 15)
    norm = flag(args, "--norm", False, has_value=False)
    return act_and_wait(ctx, regex, timeout, "tap", _points(ctx, args, 2, norm))


@command("swipe", "<x1> <y1> <x2> <y2> [ms] [--wait RE] [--timeout S] [--norm]  swipe; pixels of the screen, or 0-1 with --norm")
def cmd_swipe(ctx, args):
    regex, timeout = flag(args, "--wait"), flag(args, "--timeout", 15)
    norm = flag(args, "--norm", False, has_value=False)
    values = _points(ctx, args, 4, norm)
    values.append(int(args[4]) if len(args) > 4 else 400)
    return act_and_wait(ctx, regex, timeout, "swipe", values)


@command("key", "<KEYCODE> [--wait RE] [--timeout S]  Android keycodes; iOS maps HOME, POWER, VOLUME_*, DEL, ENTER")
def cmd_key(ctx, args):
    regex, timeout = flag(args, "--wait"), flag(args, "--timeout", 15)
    if not args:
        raise Fail("usage: key <KEYCODE> [--wait RE]")
    return act_and_wait(ctx, regex, timeout, "key", [args[0]])


# --- nav profile ---------------------------------------------------------------------------------


def _profile(ctx):
    path = ctx.nav_profile()
    if not path:
        raise Fail("no nav profile -- set DEVICE_NAV_PROFILE or DEVICE_<ALIAS>_NAV_PROFILE")
    if not os.path.exists(path):
        raise Fail(f"nav profile not found: {path}")
    return path


@command("nav", "<id> [--timeout S] [--dry-run]  a named step from the nav profile, waiting on its evidence")
def cmd_nav(ctx, args):
    path = _profile(ctx)
    timeout = flag(args, "--timeout", 15)
    dry = flag(args, "--dry-run", False, has_value=False)
    if not args:
        raise Fail("usage: nav <id> [--timeout S] [--dry-run]")
    size, density = ctx.device.size(), ctx.device.density()
    try:
        nav.check_screen(path, size, density)
    except nav.NavError as e:
        raise Fail(str(e))
    row = next((r for r in nav.rows(path) if r[0] == args[0]), None)
    if row is None:
        raise Fail(f"no step '{args[0]}' in {path} (try nav-list)")
    action, values = nav.resolve_action(row[1], size, density)
    # Print where the step would land without touching the device. Checking a normalized profile by
    # running it is how you navigate the app by accident.
    if dry:
        print(f"would send  {describe(action, values)}")
        return 0
    return act_and_wait(ctx, row[2] or None, timeout, action, values)


@command("nav-list", "every step the profile defines")
def cmd_nav_list(ctx, args):
    for row in nav.rows(_profile(ctx)):
        print(f"{row[0]:<22} {row[1]:<28} {row[3]}")


# --- capture -------------------------------------------------------------------------------------


@command("sleep", "<seconds>  wait, so a plan can let an animation settle")
def cmd_sleep(ctx, args):
    if len(args) != 1:
        raise Fail("usage: sleep <seconds>")
    try:
        seconds = float(args[0])
    except ValueError:
        raise Fail("usage: sleep <seconds>")
    time.sleep(seconds)
    print(f"slept    {args[0]}s")


@command("pause", "<seconds>  alias of sleep")
def cmd_pause(ctx, args):
    return cmd_sleep(ctx, args)


DEFAULT_SHOT_MAX_HEIGHT = 1000


@command("shot", "[path] [--max-height N]  screenshot; prints the small viewing copy (_s) first, then the full-size "
                 "path. N defaults to SHOT_MAX_HEIGHT (1000); 0 = full size only. Read the _s copy, it costs a "
                 "fraction of the tokens")
def cmd_shot(ctx, args):
    args = list(args)
    max_height = flag(args, "--max-height")
    if max_height is None:
        max_height = config.get("SHOT_MAX_HEIGHT", str(DEFAULT_SHOT_MAX_HEIGHT))
    try:
        max_height = int(max_height)
    except ValueError:
        raise Fail("--max-height / SHOT_MAX_HEIGHT must be a whole number of pixels")
    warn_quiet_once(ctx)
    path = ctx.shot(args[0] if args else None)
    small = path
    if max_height > 0:
        try:
            small = sheet.downscale(path, max_height)
        except ImportError:
            print("note     Pillow is not installed, no small copy: python3 -m pip install Pillow", file=sys.stderr)
    if small != path:
        print(small)
    print(path)
    return 0


@command("ocr", "[image] [--region X1,Y1,X2,Y2] [--match RE]  read the text on a screenshot (a fresh one when no image), "
         "one line each: cx cy text. macOS Vision; --region crops the listing to a pixel box")
def cmd_ocr(ctx, args):
    args = list(args)
    region, pattern = flag(args, "--region"), flag(args, "--match")
    try:
        found = ocr.read_text(args[0] if args else ctx.shot())
    except (RuntimeError, ValueError) as e:
        raise Fail(str(e))
    if region:
        x1, y1, x2, y2 = (float(v) for v in region.split(","))
        found = [f for f in found if x1 <= f["cx"] <= x2 and y1 <= f["cy"] <= y2]
    for f in sorted(found, key=lambda f: (f["cy"], f["cx"])):
        if not pattern or re.search(pattern, f["text"]):
            print(f"{f['cx']:.0f} {f['cy']:.0f} {f['text']}")


@command("sheet", "<out.png> <image>... [--cols N] [--width PX]  contact sheet of screenshots")
def cmd_sheet(ctx, args):
    args = list(args)
    cols, width = int(flag(args, "--cols", 5)), int(flag(args, "--width", 216))
    if len(args) < 2:
        raise Fail("usage: sheet <out.png> <image>... [--cols N] [--width PX]")
    try:
        print(sheet.contact_sheet(args[1:], args[0], cols, width))
    except RuntimeError as e:
        raise Fail(str(e))


@command("burst", "<prefix> <n> <gap> [--swipe X1,Y1,X2,Y2 [--swipes K] [--alternate] [--settle S]] [--norm]  "
                  "n screenshots gap seconds apart plus one contact sheet; with --swipe, each round is K swipes "
                  "gap apart, a settle wait (default 3 s), then the still; --alternate reverses the swipe every "
                  "second round")
def cmd_burst(ctx, args):
    args = list(args)
    swipe = flag(args, "--swipe")
    swipes, settle = int(flag(args, "--swipes", 2)), float(flag(args, "--settle", 3))
    alternate = flag(args, "--alternate", False, has_value=False)
    norm = flag(args, "--norm", False, has_value=False)
    if len(args) != 3:
        raise Fail("usage: burst <prefix> <n> <gap> [--swipe X1,Y1,X2,Y2 ...]")
    prefix, n, gap = args[0], int(args[1]), float(args[2])
    points = _points(ctx, swipe.split(","), 4, norm) if swipe else None
    folder = os.path.dirname(config.scratch("burst", prefix))
    files = []
    for i in range(n):
        if points:
            x1, y1, x2, y2 = points
            if alternate and i % 4 >= 2:
                x1, y1, x2, y2 = x2, y2, x1, y1
            for _ in range(swipes):
                ctx.device.swipe(x1, y1, x2, y2, 100)
                time.sleep(gap)
            time.sleep(settle)
        files.append(ctx.shot(os.path.join(folder, f"{prefix}_{i:02d}.png")))
        if not points and i < n - 1:
            time.sleep(gap)
    try:
        print(sheet.contact_sheet(files, os.path.join(folder, f"{prefix}_sheet.png"),
                                  5 if points else 4, 216 if points else 270))
    except RuntimeError as e:
        raise Fail(str(e))
    return 0


@command("log-clear", "clear the log buffer, so a later count or wait starts from zero")
def cmd_log_clear(ctx, args):
    ctx.device.log_clear()
    print("cleared  logs")


@command("clear-data", "[--grant PERMISSION]...  wipe the app's data (first-run state, forces a re-login), then "
                       "grant runtime permissions; Android")
def cmd_clear_data(ctx, args):
    args = list(args)
    grants = []
    while "--grant" in args:
        grants.append(flag(args, "--grant"))
    ctx.device.clear_data(grants)
    print(f"cleared  app data{' and granted ' + ', '.join(grants) if grants else ''}")


@command("record", "start|stop [name] [--segments]  screen video into the scratch dir; --segments (Android) chains "
                   "170 s clips so a take can pass 3 minutes, and stop joins them with ffmpeg")
def cmd_record(ctx, args):
    args = list(args)
    segments = flag(args, "--segments", False, has_value=False)
    if not args or args[0] not in ("start", "stop"):
        raise Fail("usage: record start|stop [name] [--segments]")
    name = args[1] if len(args) > 1 else "run"
    directory = os.path.dirname(config.scratch("video", name))
    if args[0] == "start":
        if ctx.device.locked():
            raise Fail("device is locked -- it would record the lockscreen")
        if segments and ctx.platform != "android":
            raise Unsupported("record --segments")
        ensure_quiet(ctx, sys.stderr)
        run_hooks(ctx, "pre-record")
        print(ctx.device.record_start(name, directory, segmented=True) if segments
              else ctx.device.record_start(name, directory))
    else:
        print(ctx.device.record_stop(name, directory))


@command("prefs", "[key]  the app's preferences, or the lines around KEY")
def cmd_prefs(ctx, args):
    body = ctx.device.prefs_text()
    if args:
        matches = [m.group(0) for m in re.finditer(rf".{{0,80}}{re.escape(args[0])}.{{0,80}}", body)]
        if matches:
            print("\n".join(matches))
    else:
        print(body, end="" if body.endswith("\n") else "\n")


# --- shared, JSON out ----------------------------------------------------------------------------


@command("forward", "USB port forward to the app's input server, one Mac port per device")
def cmd_forward(ctx, args):
    port = inputserver.local_port(ctx.device.serial)
    ctx.device.forward(port, inputserver.device_port())
    return emit({"ok": True, "platform": ctx.platform, "port": port})


@command("info", "input server: screen size, keyboard visible/area, focused object")
def cmd_info(ctx, args):
    return emit(inputserver.call(ctx.device.serial, "/info"))


@command("text", "<value>  set the focused input field directly")
def cmd_text(ctx, args):
    if not args:
        raise Fail("usage: text <value>")
    return emit(inputserver.call(ctx.device.serial, "/text", value=" ".join(args)))


@command("type", "<text>  type through the native keyboard")
def cmd_type(ctx, args):
    if not args:
        raise Fail("usage: type <text>")
    ctx.device.type_text(" ".join(args))
    return emit({"ok": True, "platform": ctx.platform})


@command("clipboard", "set <text> | get  the device clipboard (iOS: CoreDevice pasteboard, exact UTF-8; "
                      "Android has no host route, exit 3). Pasting into a field is a separate step")
def cmd_clipboard(ctx, args):
    if not args or args[0] not in ("set", "get"):
        raise Fail("usage: clipboard set <text> | get")
    if args[0] == "set":
        if len(args) < 2:
            raise Fail("usage: clipboard set <text>")
        ctx.device.clipboard_set(" ".join(args[1:]))
        return emit({"ok": True, "platform": ctx.platform, "set": " ".join(args[1:])})
    return emit({"ok": True, "platform": ctx.platform, "text": ctx.device.clipboard_get()})


@command("deselect", "clear focus, which closes the keyboard")
def cmd_deselect(ctx, args):
    return emit(inputserver.call(ctx.device.serial, "/deselect"))


@command("keyboard", "system style, keyboard state, input mode (+ native detail)")
def cmd_keyboard(ctx, args):
    d = ctx.device
    info = inputserver.call(d.serial, "/info")
    native = d.keyboard_native()
    return emit({"ok": True, "platform": ctx.platform, "systemStyle": d.system_style(),
                 "keyboardVisible": info.get("keyboardVisible"), "keyboardArea": info.get("keyboardArea"),
                 "selected": info.get("selected"), "inputMode": native.get("inputMode"),
                 "keyboardAppearance": native.get("keyboardAppearance"), "native": native})


@command("appearance", "[dark|light]  read or set the system dark/light style")
def cmd_appearance(ctx, args):
    style = args[0] if args else None
    if style not in (None, "dark", "light"):
        raise Fail("usage: appearance [dark|light]")
    return emit({"ok": True, "platform": ctx.platform, "style": ctx.device.system_style(style)})


@command("rotate", "left|right  turn the device 90 degrees as if by hand (counterclockwise or clockwise); "
                   "prints the orientation it reports")
def cmd_rotate(ctx, args):
    if not args or args[0] not in ("left", "right"):
        raise Fail("usage: rotate left|right")
    return emit({"ok": True, "platform": ctx.platform, **ctx.device.rotate(args[0])})


# --- native UI: opt-in WebDriverAgent (iOS), for the soft keyboard and system alerts ---------------

NATIVE_USAGE = ("start|stop|status|build|install|tap|hold|swipe|press|keys|source  opt-in WebDriverAgent (iOS only), "
                "for UI the platform draws itself: the soft keyboard's keys and system alerts. Never the "
                "default input. `tap <x> <y>`, `hold <x> <y> [ms]`, `swipe <x1> <y1> <x2> <y2> [ms] [--hold ms]` (hold first: long-press-and-slide) "
                "([--wait RE] [--timeout S] [--norm]); `press <label> [--hold ms]`; `keys <label>... [--gap ms]` (one action, no per-key lookup; "
                "still ~5 s a key over a game); `source [--grep RE]`")


@command("native", NATIVE_USAGE)
def cmd_native(ctx, args):
    d = ctx.device
    args = list(args)
    sub = args.pop(0) if args else ""
    if sub in ("start", "stop", "status", "build", "install"):
        result = getattr(d, "native_" + sub)()
        return emit({"ok": True, "platform": ctx.platform, "result": result})
    if sub in ("tap", "hold", "swipe"):
        regex, timeout = flag(args, "--wait"), flag(args, "--timeout", 15)
        norm = flag(args, "--norm", False, has_value=False)
        hold_first = flag(args, "--hold")
        count = 4 if sub == "swipe" else 2
        values = _points(ctx, args, count, norm)
        if sub == "hold":
            values.append(int(args[2]) if len(args) > 2 else 800)
        if sub == "swipe":
            values.append(int(args[4]) if len(args) > 4 else 400)
            if hold_first:
                values.append(int(hold_first))
        return act_and_wait(ctx, regex, timeout, "native_" + sub, values)
    if sub == "press":
        hold = flag(args, "--hold")
        if not args:
            raise Fail("usage: native press <label> [--hold ms]")
        d.native_press(" ".join(args), int(hold) if hold else None)
        return emit({"ok": True, "pressed": " ".join(args), "hold": int(hold) if hold else None})
    if sub == "keys":
        gap = int(flag(args, "--gap", 60))
        if not args:
            raise Fail("usage: native keys <label>... [--gap ms]")
        return emit({"ok": True, "tapped": d.native_keys(args, gap), "gap_ms": gap})
    if sub == "source":
        pattern = re.compile(flag(args, "--grep")) if "--grep" in args else None
        lines = d.native_source().splitlines()
        print("\n".join(l for l in lines if not pattern or pattern.search(l)))
        return 0
    raise Fail("usage: native start|stop|status|build|install|tap|hold|swipe|press|keys|source")


MEDIA_USAGE = ("build|install|push <file...> [--album NAME]|hide-others [--album NAME]|unhide|picker  the Photos library "
               "on an iOS device, through the media helper app (WDA team signing; WDA answers its prompt). "
               "hide-others hides everything outside the album for a clean capture and records it; unhide "
               "restores exactly those. Nothing is ever deleted")


@command("media", MEDIA_USAGE)
def cmd_media(ctx, args):
    d = ctx.device
    if ctx.platform != "ios":
        raise Unsupported("media: iOS devices only (an Android test can `adb push` into DCIM and scan)")
    args = list(args)
    sub = args.pop(0) if args else ""
    album = flag(args, "--album", "Device Testing")
    if sub in ("build", "install"):
        return emit({"ok": True, "platform": ctx.platform, "result": getattr(d, "media_" + sub)()})
    if sub == "push":
        if not args:
            raise Fail("usage: media push <file...> [--album NAME]")
        return emit({"ok": True, "platform": ctx.platform, **d.media_run("push", args, album)})
    if sub == "pull":
        # media pull <out-dir> [--since UNIX] [--name N]: the newest video since then (e.g. a
        # Screen Recording) copied off the device.
        since, name = float(flag(args, "--since", 0)), flag(args, "--name", "recording")
        out = args[0] if args else "."
        result = d.media_run("pull", extra={"since": since, "name": name})
        local = os.path.join(out, os.path.basename(result["file"]))
        from .backends import ios_media
        ios_media.copy_from(d, result["file"], local)
        return emit({"ok": True, "platform": ctx.platform, "local": local, **result})
    if sub in ("hide-others", "unhide", "picker"):
        return emit({"ok": True, "platform": ctx.platform, **d.media_run(sub, (), album)})
    raise Fail("usage: media " + MEDIA_USAGE.split("  ")[0])


def watch_alerts(ctx, seconds, action="accept"):
    """Poll for system alerts for `seconds`, answer each with `action`, print its text. Returns the
    texts seen. A second look right after an answer catches an alert queued behind the first."""
    d = ctx.device
    seen = []
    end = time.time() + seconds
    while time.time() < end:
        found = d.native_alert("text")
        if found.get("alert"):
            text = found.get("text")
            seen.append(text)
            print(f"alert    {action}: {str(text).replace(chr(10), ' | ')}", flush=True)
            d.native_alert(action)
            time.sleep(0.5)
            continue
        time.sleep(0.5)
    print(f"alert    watched {seconds:g}s, answered {len(seen)}")
    return seen


@command("alert", "accept|dismiss|text|watch S [accept|dismiss]  a system alert, through WebDriverAgent (iOS only, "
                  "opt-in); watch answers every alert that shows within S seconds and prints its text")
def cmd_alert(ctx, args):
    if not args or args[0] not in ("accept", "dismiss", "text", "watch"):
        raise Fail("usage: alert accept|dismiss|text|watch S [accept|dismiss]")
    if args[0] == "watch":
        if len(args) < 2:
            raise Fail("usage: alert watch S [accept|dismiss]")
        action = args[2] if len(args) > 2 else "accept"
        if action not in ("accept", "dismiss"):
            raise Fail("usage: alert watch S [accept|dismiss]")
        seen = watch_alerts(ctx, float(args[1]), action)
        return emit({"ok": True, "platform": ctx.platform, "answered": len(seen), "texts": seen})
    return emit({"ok": True, "platform": ctx.platform, **ctx.device.native_alert(args[0])})


# --- simulation: location and network ------------------------------------------------------------


def _route_points(path, speed):
    """Read a GPX (trkpt, rtept, wpt) or CSV (`lat,lng` per line) file and return
    [(lat, lng, seconds)] one point per second of travel at `speed` m/s."""
    with open(path) as f:
        text = f.read()
    raw = []
    if path.lower().endswith(".gpx") or "<gpx" in text[:500]:
        for m in re.finditer(r"<(?:trkpt|rtept|wpt)\b([^>]*)>", text):
            lat = re.search(r'lat="([-\d.]+)"', m.group(1))
            lng = re.search(r'lon="([-\d.]+)"', m.group(1))
            if lat and lng:
                raw.append((float(lat.group(1)), float(lng.group(1))))
    else:
        for line in text.splitlines():
            parts = [p for p in re.split(r"[,;\s]+", line.strip()) if p]
            try:
                raw.append((float(parts[0]), float(parts[1])))
            except (ValueError, IndexError):
                continue  # a header line
    if len(raw) < 2:
        raise Fail(f"{path}: need at least two points (GPX trkpt/rtept/wpt, or `lat,lng` CSV lines)")
    points, t = [(raw[0][0], raw[0][1], 0.0)], 0.0
    for (la1, lo1), (la2, lo2) in zip(raw, raw[1:]):
        meters = _haversine(la1, lo1, la2, lo2)
        steps = max(1, round(meters / speed))
        for i in range(1, steps + 1):
            t += meters / speed / steps
            points.append((la1 + (la2 - la1) * i / steps, lo1 + (lo2 - lo1) * i / steps, t))
    return points


def _haversine(la1, lo1, la2, lo2):
    r = 6371000.0
    p1, p2 = math.radians(la1), math.radians(la2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@command("location", "set <lat> <lng> | route <gpx|csv> [--speed M/S] | clear  simulate the GPS position for every "
                     "app; always `clear` when done. `route` walks the file at --speed (default 1.4) and stays "
                     "at its last point")
def cmd_location(ctx, args):
    d = ctx.device
    args = list(args)
    sub = args.pop(0) if args else ""
    if sub == "set":
        try:
            lat, lng = float(args[0]), float(args[1])
        except (ValueError, IndexError):
            raise Fail("usage: location set <lat> <lng>")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise Fail("latitude is -90..90 and longitude -180..180")
        d.location_set(lat, lng)
        print(f"location set {lat:.6f} {lng:.6f}")
        return 0
    if sub == "route":
        speed = float(flag(args, "--speed", 1.4))
        if not args or not os.path.isfile(args[0]) or speed <= 0:
            raise Fail("usage: location route <gpx|csv> [--speed M/S]")
        points = _route_points(args[0], speed)
        print(f"route    {len(points)} points, {points[-1][2]:.0f}s at {speed} m/s")
        d.location_route(points)
        print(f"location set {points[-1][0]:.6f} {points[-1][1]:.6f} (route finished)")
        return 0
    if sub == "clear":
        d.location_clear()
        print("location cleared")
        return 0
    raise Fail("usage: location set <lat> <lng> | route <gpx|csv> [--speed M/S] | clear")


@command("network", "<profile> [--relaunch [--input-server]] | list | clear  simulate a network condition (a bad link, "
                    "100% loss); always `clear` when done. iOS applies it only to an app started under it, so "
                    "--relaunch restarts the app (a running or devicectl-launched app is unaffected); `clear` then "
                    "relaunches it normally")
def cmd_network(ctx, args):
    d = ctx.device
    args = list(args)
    relaunch = flag(args, "--relaunch", False, has_value=False)
    server = flag(args, "--input-server", False, has_value=False)
    if not args:
        raise Fail("usage: network <profile> [--relaunch [--input-server]] | list | clear")
    if args[0] == "list":
        for identifier, description in d.network_list():
            print(f"{identifier:<30} {description}")
        return 0
    if args[0] == "clear":
        relaunched = d.network_clear()
        print("network cleared" + (" (app stopped and relaunched normally)" if relaunched else ""))
        return 0
    env = inputserver.launch_environment()[ctx.platform] if server else None
    d.network_set(args[0], relaunch=relaunch, environment=env)
    print(f"network {args[0]}" + (f" (app relaunched under it, pid {d.pid()})" if relaunch else ""))
    return 0


# --- plan runner ---------------------------------------------------------------------------------


def parse_plan(path):
    """(steps, finals): one command per line, whole-line `#` comments and blank lines ignored; a line `finally:`
    starts the section that always runs."""
    steps, finals = [], []
    section = steps
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.rstrip(":").lower() == "finally" and line.endswith(":"):
                section = finals
                continue
            section.append(line)
    return steps, finals


def _run_line(ctx, line):
    """Run one plan line in-process; returns (ok, note)."""
    import shlex
    from .inputserver import ServerError
    try:
        parts = shlex.split(line)
    except ValueError as e:
        return False, f"cannot parse: {e}"
    if not parts or parts[0] == "run":
        return False, "a plan line is a device command, not `run`"
    try:
        code = dispatch(ctx, parts[0], parts[1:])
    except Unsupported as e:
        return False, f"not supported on {ctx.platform}: {e}"
    except Fail as e:
        return False, str(e)
    except (ServerError, Exception) as e:
        return False, str(e)
    return code == 0, f"exit {code}" if code else ""


FILE_IN_OUTPUT = re.compile(r"(/[^\s\"']+\.(?:png|jpe?g|gif|mp4|mov|csv|log|txt|json))")
FAIL_TAIL_LINES = 15


def _produced_files(text):
    """Existing files a step printed, in order, without repeats."""
    found = []
    for path in FILE_IN_OUTPUT.findall(text):
        if path not in found and os.path.isfile(path):
            found.append(path)
    return found


def _wait_regex(line):
    """The log regex a plan line waits on (`wait RE ...` or `--wait RE`), if any."""
    import shlex
    try:
        parts = shlex.split(line)
    except ValueError:
        return None
    if parts and parts[0] == "wait" and len(parts) > 1:
        return parts[1]
    return parts[parts.index("--wait") + 1] if "--wait" in parts[:-1] else None


def _failure_detail(ctx, line, output):
    """The last ~15 lines worth reading after a failed step: matching log lines when it waited on a regex,
    else the tail of what the step printed."""
    regex = _wait_regex(line)
    if regex:
        try:
            pattern = re.compile(regex)
            matches = [l for l in ctx.device.log_lines() if pattern.search(l)]
            if matches:
                return [f"log      last {min(len(matches), FAIL_TAIL_LINES)} lines matching {regex}"] + \
                    [l[:240] for l in matches[-FAIL_TAIL_LINES:]]
            return [f"log      no line matches {regex}"]
        except Exception:
            pass
    return [l[:240] for l in output.splitlines()[-FAIL_TAIL_LINES:]]


def _resolve_plan(path):
    return config.resolve_in_skills(path) if not os.path.exists(path) else path


@command("run", "<plan> [--keep-going] [--verbose]  run a plan file: one command per line (# comments), stop on the "
                "first failure unless --keep-going, always run the lines after `finally:`. Any device command is a "
                "plan line (tap, swipe, key, native ..., nav, wait, sleep, shot, record, ...). Output is compact: one "
                "`ok|FAIL <ms> <cmd>` line per step, the last ~15 matching lines of a failed step, a `files` line "
                "naming every screenshot/log/video made, and a full log path; --verbose streams each step live. "
                "Exit 1 if any step failed")
def cmd_run(ctx, args):
    import contextlib
    import io
    args = list(args)
    keep_going = flag(args, "--keep-going", False, has_value=False)
    verbose = flag(args, "--verbose", False, has_value=False)
    if not args:
        raise Fail("usage: run <plan> [--keep-going] [--verbose]")
    plan = _resolve_plan(args[0])
    if not os.path.exists(plan):
        raise Fail(f"plan not found: {args[0]}")
    steps, finals = parse_plan(plan)
    name = os.path.basename(plan)
    log_path = config.scratch("runs", f"{os.path.splitext(name)[0]}-{time.strftime('%Y%m%d-%H%M%S')}.log")
    log = open(log_path, "w")
    started = time.time()
    passed = failed = skipped = 0
    stopped = False
    summary, files = [], []

    def one(line, final):
        nonlocal passed, failed
        label = ("finally " if final else "") + line
        buffer = io.StringIO()
        t0 = time.time()
        if verbose:
            print(f"--- {label}", flush=True)
            ok, note = _run_line(ctx, line)
        else:
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                ok, note = _run_line(ctx, line)
        ms = int((time.time() - t0) * 1000)
        output = buffer.getvalue()
        log.write(f"--- {label}\n{output}{'FAIL ' + note if not ok and note else ''}\n")
        log.flush()
        for path in _produced_files(output):
            if path not in files:
                files.append(path)
        mark = "ok  " if ok else "FAIL"
        summary.append(f"{mark} {ms:>6} {label}" + (f"  -- {note}" if note and not ok else ""))
        if verbose:
            print(summary[-1], flush=True)
        elif not ok:
            detail = _failure_detail(ctx, line, output)
            summary.extend("       | " + l for l in detail)
        passed += ok
        failed += not ok
        return ok

    for line in steps:
        if stopped:
            skipped += 1
            summary.append(f"skip        0 {line}")
            continue
        if not one(line, False) and not keep_going:
            stopped = True
    for line in finals:
        one(line, True)
    log.close()
    if not verbose:
        print("\n".join(summary))
    # Small viewing copies first, so a caller reads the cheap file.
    files[:] = [p for p in files if p.endswith("_s.png")] + [p for p in files if not p.endswith("_s.png")]
    if files:
        print("files    " + " ".join(files))
    print(f"log      {log_path}")
    print(f"plan {name}: {passed} ok, {failed} failed, {skipped} skipped, {time.time() - started:.0f}s"
          f"{'  (stopped at first failure)' if stopped else ''}")
    return 1 if failed else 0


# --- performance ---------------------------------------------------------------------------------


def _median(values):
    values = sorted(v for v in values if v is not None)
    return values[len(values) // 2] if values else None


def _percentile(values, pct):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * pct / 100))] if values else None


def _fmt(value, unit="", digits=1):
    return "n/a" if value is None else f"{value:.{digits}f}{unit}"


@command("perf", "sample <seconds> [name] [--dir DIR]  sample the running app: frames per second, CPU, memory. "
                 "Writes <name>.csv and prints a summary line. Frame rate is the compositor's on iOS and the "
                 "app surface's on Android; each can read n/a")
def cmd_perf(ctx, args):
    args = list(args)
    directory = flag(args, "--dir")
    if not args or args[0] != "sample":
        raise Fail("usage: perf sample <seconds> [name] [--dir DIR]")
    try:
        seconds = float(args[1])
    except (ValueError, IndexError):
        raise Fail("usage: perf sample <seconds> [name] [--dir DIR]")
    name = args[2] if len(args) > 2 else "perf-" + time.strftime("%Y%m%d-%H%M%S")
    d = ctx.device
    if d.pid() is None:
        raise Fail(f"{d.app_id()} is not running -- nothing to sample")
    result = d.perf_sample(seconds)
    rows, frames = result["rows"], result["frame_ms"]
    out = os.path.join(directory, f"{name}.csv") if directory else config.scratch("perf", f"{name}.csv")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as f:
        f.write("t_s,fps,cpu_pct,mem_mb\n")
        for r in rows:
            f.write(",".join("" if r[k] is None else f"{r[k]:.2f}" for k in ("t", "fps", "cpu", "mem_mb")) + "\n")
    mem = [r["mem_mb"] for r in rows if r["mem_mb"] is not None]
    print(f"perf     {len(rows)} samples over {seconds:.0f}s: fps median {_fmt(_median(r['fps'] for r in rows))}, "
          f"frame p95 {_fmt(_percentile(frames, 95), ' ms')}, cpu median {_fmt(_median(r['cpu'] for r in rows), '%')}, "
          f"memory peak {_fmt(max(mem) if mem else None, ' MB', 0)}")
    for note in result.get("notes", []):
        print(f"note     {note}")
    print(out)
    return 0


# --- crashes -------------------------------------------------------------------------------------


def _since(text):
    """`30m`, `2h`, `1d`, `YYYY-MM-DD`, `YYYY-MM-DD HH:MM` or `HH:MM` (today) as a naive local datetime."""
    now = datetime.datetime.now()
    m = re.fullmatch(r"(\d+)([mhd])", text)
    if m:
        return now - datetime.timedelta(**{{"m": "minutes", "h": "hours", "d": "days"}[m.group(2)]: int(m.group(1))})
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%H:%M"):
        try:
            parsed = datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(year=now.year, month=now.month, day=now.day) if fmt == "%H:%M" else parsed
    raise Fail("--since takes 30m, 2h, 1d, YYYY-MM-DD, 'YYYY-MM-DD HH:MM' or HH:MM")


@command("crashes", "[--since TIME] [--pull DIR] [--fail]  the app's crash reports (iOS .ips, Android crash/ANR/native "
                    "crash records), newest last; none is a clean result; --fail exits 2 when any is found. TIME: 30m, 2h, 1d, a "
                    "date or HH:MM")
def cmd_crashes(ctx, args):
    args = list(args)
    since = _since(flag(args, "--since")) if "--since" in args else None
    pull = flag(args, "--pull")
    fail = flag(args, "--fail", False, has_value=False)
    found = ctx.device.crashes()
    if since:
        found = [c for c in found if c["time"] is None or c["time"] >= since]
    found.sort(key=lambda c: c["time"] or datetime.datetime.min)
    if not found:
        print(f"no crashes for {ctx.device.app_id()}" + (f" since {since:%Y-%m-%d %H:%M}" if since else ""))
        return 0
    for c in found:
        print(f"{c['time']:%Y-%m-%d %H:%M:%S}" if c["time"] else "(no time)          ", f"{c['kind']:<12}", c["name"])
    if pull:
        os.makedirs(pull, exist_ok=True)
        for path in ctx.device.crash_pull(found, pull):
            print(f"pulled   {path}")
    return 2 if fail else 0
