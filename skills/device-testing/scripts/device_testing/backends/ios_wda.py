"""Opt-in WebDriverAgent (WDA) for UI iOS draws itself: the soft keyboard's keys and system alerts.

Never an input route. The `native` and `alert` commands call it; nothing else does. The signed
runner is started with `pymobiledevice3 developer dvt xcuitest` (no xcodebuild at run time) and its
port 8100 is forwarded over usbmux. State (pids, session id) is kept per phone in the backend state.
"""
import json
import re
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

from .. import config

DEFAULT_PORT = 8100
RUNNER_SUFFIX = ".WebDriverAgentRunner"
STALE_PROBE = 12  # s a runner found already alive gets to answer before it counts as wedged
EXPIRY_HINT = ("if the launch fails: a free-team profile expires after 7 days (rerun `native build` and "
               "`native install`), and the developer certificate must be trusted on the phone "
               "(Settings > General > VPN & Device Management)")


class WdaError(RuntimeError):
    pass


def port():
    return int(config.get("DEVICE_IOS_WDA_PORT", DEFAULT_PORT))


def runner_bundle():
    return config.require("DEVICE_IOS_WDA_BUNDLE_PREFIX") + RUNNER_SUFFIX + ".xctrunner"


def derived_data():
    return config.scratch("wda-derived-data")


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill(pid):
    if _alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


# --- http ----------------------------------------------------------------------------------------


def http(method, path, body=None, timeout=30):
    data = json.dumps(body if body is not None else {}).encode() if method == "POST" else None
    req = urllib.request.Request(f"http://127.0.0.1:{port()}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except ValueError:
            raise WdaError(f"WDA {method} {path}: HTTP {e.code}")


def _error(result):
    """The W3C error inside a response, as text, or None."""
    value = result.get("value")
    if isinstance(value, dict) and value.get("error"):
        return f"{value['error']}: {value.get('message', '')}".strip()
    return None


def ready():
    try:
        return bool(http("GET", "/status", timeout=3).get("value", {}).get("ready"))
    except (OSError, ValueError, WdaError):
        return False


# --- lifecycle -----------------------------------------------------------------------------------


def _spawn(backend, args, log_name):
    with open(config.scratch(log_name), "a") as log:
        return subprocess.Popen(backend.pmd3(*args), stdout=log, stderr=log, start_new_session=True,
                                env=backend.env())


PASSCODE_MARK = "initializationForUITestingDidFailWithError"
PASSCODE_HINT = ('the phone is asking for its passcode to enable UI automation ("Enter iPhone Passcode for '
                 'XCTest" / "Enable UI Automation"); a person must enter it on the phone, then rerun')


def _log_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _passcode_asked(path, offset):
    """True when the runner log gained the UI-automation failure line after offset."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            return PASSCODE_MARK.encode() in f.read()
    except OSError:
        return False


def start(backend, timeout=60):
    """Runner, forward and a session; reuses whatever is already up."""
    st = backend.state("wda") or {}
    if ready() and st.get("session"):
        return st
    stale = _alive(st.get("runner"))
    runner_log = config.scratch(f"wda-runner-{backend.serial}.log")
    log_from = _log_size(runner_log)
    if not stale:
        # After a reboot or a replug the developer disk image is not mounted until something asks for
        # it, and the runner then dies with InvalidService / "DTX capability handshake timed out".
        backend.ok(backend.devicectl("device info ddiServices"))
        runner = _spawn(backend, ("developer", "dvt", "xcuitest", runner_bundle()), f"wda-runner-{backend.serial}.log")
        st["runner"] = runner.pid
    if not _alive(st.get("fwd")):
        st["fwd"] = _spawn(backend, ("usbmux", "forward", "--serial", backend.serial, str(port()),
                                     str(DEFAULT_PORT)), f"wda-forward-{backend.serial}.log").pid
    backend.remember("wda", st)
    end = time.time() + (min(timeout, STALE_PROBE) if stale else timeout)
    while time.time() < end and not ready():
        if _passcode_asked(runner_log, log_from):
            raise WdaError(PASSCODE_HINT)
        if not _alive(st["runner"]):
            raise WdaError(f"the WDA runner exited -- see {config.scratch(f'wda-runner-{backend.serial}.log')}; {EXPIRY_HINT}")
        time.sleep(1)
    if not ready() and _passcode_asked(runner_log, log_from):
        raise WdaError(PASSCODE_HINT)  # a restart cannot clear it
    if stale and not ready():
        # Alive but never ready: restart once through the normal path (it also mounts the DDI).
        stop(backend)
        return start(backend, timeout)
    if not ready():
        raise WdaError(f"WDA not ready after {int(timeout)}s; {EXPIRY_HINT}")
    st["session"] = new_session()
    backend.remember("wda", st)
    return st


def new_session():
    result = http("POST", "/session", {"capabilities": {"alwaysMatch": {"shouldWaitForQuiescence": False}}})
    sid = result.get("sessionId") or (result.get("value") or {}).get("sessionId")
    if not sid:
        raise WdaError(f"no WDA session: {result}")
    # A game redraws every frame and never goes idle, so the default 10 s idle wait lands on every lookup.
    http("POST", f"/session/{sid}/appium/settings", {"settings": {"waitForIdleTimeout": 0, "animationCoolOffTimeout": 0}})
    return sid


def stop(backend):
    st = backend.state("wda") or {}
    for key in ("runner", "fwd"):
        _kill(st.get(key))
    backend.remember("wda", None)
    return {"stopped": [k for k in ("runner", "fwd") if st.get(k)]}


def status(backend):
    """Read-only: reports what is up, never starts anything."""
    st = backend.state("wda") or {}
    if not ready():
        return {"ready": False, "runner": _alive(st.get("runner")), "forward": _alive(st.get("fwd"))}
    info = http("GET", "/status").get("value", {})
    return {"ready": info.get("ready"), "state": info.get("state"), "os": info.get("os"),
            "session": st.get("session"), "frontApp": front_app(backend) if st.get("session") else None}


def front_app(backend):
    result = call(backend, "GET", "/wda/activeAppInfo")
    return (result.get("value") or {}).get("bundleId")


def call(backend, method, path, body=None, timeout=30):
    """A session request; starts WDA when it is not up and renews a lost session once."""
    st = start(backend)
    for attempt in (0, 1):
        result = http(method, f"/session/{st['session']}{path}", body, timeout)
        err = _error(result)
        if err and "invalid session" in err and attempt == 0:
            st["session"] = new_session()
            backend.remember("wda", st)
            continue
        return result
    return result


def checked(backend, method, path, body=None, timeout=30):
    result = call(backend, method, path, body, timeout)
    err = _error(result)
    if err:
        raise WdaError(err)
    return result.get("value")


# --- build and install ---------------------------------------------------------------------------


def build(backend):
    src = config.require("DEVICE_IOS_WDA_SRC")
    team = config.require("DEVICE_IOS_WDA_TEAM")
    prefix = config.require("DEVICE_IOS_WDA_BUNDLE_PREFIX")
    backend.sh(["xcodebuild", "build-for-testing", "-project", os.path.join(src, "WebDriverAgent.xcodeproj"),
                "-scheme", "WebDriverAgentRunner", "-destination", f"id={backend.serial}",
                "-derivedDataPath", derived_data(), "-allowProvisioningUpdates",
                f"DEVELOPMENT_TEAM={team}", "CODE_SIGN_STYLE=Automatic",
                f"PRODUCT_BUNDLE_IDENTIFIER={prefix}{RUNNER_SUFFIX}"], stdout=sys.stderr)
    return app_path()


def app_path():
    return os.path.join(derived_data(), "Build", "Products", "Debug-iphoneos", "WebDriverAgentRunner-Runner.app")


def install(backend):
    app = app_path()
    if not os.path.isdir(app):
        raise WdaError(f"no runner build at {app} -- run `native build` first")
    backend.sh(backend.pmd3("apps", "install", app), stdout=sys.stderr, env=backend.env())
    return app


# --- touch ---------------------------------------------------------------------------------------


def _touch(backend, steps):
    """W3C actions. `/wda/tap` returns success and does nothing on iOS 26, so it is never used."""
    checked(backend, "POST", "/actions", {"actions": [{
        "type": "pointer", "id": "finger", "parameters": {"pointerType": "touch"}, "actions": steps}]})
    call(backend, "DELETE", "/actions")


def _pts(backend, *pixels):
    scale = backend.display()["scale"] or 1
    return [round(p / scale, 1) for p in pixels]


def _move(x, y, duration=0):
    return {"type": "pointerMove", "duration": duration, "x": x, "y": y}


def tap(backend, x, y):
    hold(backend, x, y, 80)


def hold(backend, x, y, ms):
    px, py = _pts(backend, x, y)
    _touch(backend, [_move(px, py), {"type": "pointerDown", "button": 0},
                     {"type": "pause", "duration": int(ms)}, {"type": "pointerUp", "button": 0}])


def swipe(backend, x1, y1, x2, y2, ms, hold_ms=50):
    """`hold_ms` before moving turns it into a long-press-and-slide, e.g. onto a key's accent popup."""
    a, b, c, d = _pts(backend, x1, y1, x2, y2)
    _touch(backend, [_move(a, b), {"type": "pointerDown", "button": 0}, {"type": "pause", "duration": int(hold_ms)},
                     _move(c, d, int(ms)), {"type": "pointerUp", "button": 0}])


def _find(backend, label):
    for using, value in (("accessibility id", label),
                         ("-ios predicate string", f'label == "{label}" OR name == "{label}" OR value == "{label}"')):
        found = call(backend, "POST", "/element", {"using": using, "value": value}).get("value")
        if isinstance(found, dict) and not found.get("error"):
            return found.get("ELEMENT") or next((v for k, v in found.items() if k.startswith("element-")), None)
    raise WdaError(f"no element with accessibility id or label '{label}'")


def press(backend, label, hold_ms=None):
    el = _find(backend, label)
    if hold_ms:
        r = checked(backend, "GET", f"/element/{el}/rect")
        scale = backend.display()["scale"] or 1
        hold(backend, (r["x"] + r["width"] / 2) * scale, (r["y"] + r["height"] / 2) * scale, hold_ms)
    else:
        checked(backend, "POST", f"/element/{el}/click")


_KEY_LINE = re.compile(r"^\s*(?:Key|Button), 0x[0-9a-f]+, \{\{([\d.]+), ([\d.]+)\}, \{([\d.]+), ([\d.]+)\}\}(.*)$")


def _key_rects(backend):
    """label and identifier -> rect (points) of every key, from one tree read (format=json takes over 30 s)."""
    rects = {}
    for line in source(backend).splitlines():
        m = _KEY_LINE.match(line)
        if not m:
            continue
        rect = dict(zip(("x", "y", "width", "height"), map(float, m.groups()[:4])))
        for k in re.findall(r"(?:identifier|label): '([^']*)'", m.group(5)):
            rects.setdefault(k, rect)
    return rects


def keys(backend, labels, gap_ms=60):
    """Tap keys by label in one action; one `press` per key is ~12 s (process, lookup and click each).
    Key rects are cached: a tree read over a game can take 30 s. A label not in the cache re-reads it."""
    rects = backend.state("wda_keys") or {}
    if any(k not in rects for k in labels):
        rects = _key_rects(backend)
        backend.remember("wda_keys", rects)
    missing = [k for k in labels if k not in rects]
    if missing:
        raise WdaError(f"no key labelled {missing}; read the layout with `native source --grep Key`")
    # One finger per key: on a single source, the move after a lift lands as a second press (abc typed Abbcc).
    # WDA times each source on its own clock, so a finger waits out the taps before it in one pause.
    sources = []
    for i, k in enumerate(labels):
        r = rects[k]
        steps = [{"type": "pause", "duration": i * (40 + int(gap_ms))}]
        steps += [_move(r["x"] + r["width"] / 2, r["y"] + r["height"] / 2), {"type": "pointerDown", "button": 0},
                  {"type": "pause", "duration": 40}, {"type": "pointerUp", "button": 0},
                  {"type": "pause", "duration": int(gap_ms)}]
        sources.append({"type": "pointer", "id": f"finger{i}", "parameters": {"pointerType": "touch"},
                        "actions": steps})
    # Over a game view XCTest still takes ~5 s to deliver each touch (measured on iOS 26.6.2).
    checked(backend, "POST", "/actions", {"actions": sources}, timeout=30 + 8 * len(labels))
    call(backend, "DELETE", "/actions")
    return len(labels)


def source(backend):
    return checked(backend, "GET", "/source?format=description", timeout=90)  # 27 s seen over a game


# --- alerts --------------------------------------------------------------------------------------


def alert(backend, action):
    """accept | dismiss | text. No alert up is a result, not an error."""
    method, path = ("GET", "/alert/text") if action == "text" else ("POST", f"/alert/{action}")
    result = call(backend, method, path)
    err = _error(result)
    if err:
        if "no such alert" in err:
            return {"alert": False}
        raise WdaError(err)
    return {"alert": True, "text": result.get("value")} if action == "text" else {"alert": True, "done": action}
