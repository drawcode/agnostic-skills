"""iOS on a physical iPhone. Primitives only; the commands live in commands.py.

Tools: devicectl (ships with Xcode) for install, launch, processes, lock state and app files;
pymobiledevice3 for screenshots, syslog, app state, display info, hardware buttons and appearance.
Touches go through the app's input server (references/input-server.md), because iOS 26 gives the
host no touch injection without a signed XCTest runner; CoreDevice's HID service does it from
iOS 27 on (backends/ios_hid.py). DEVICE_IOS_INPUT picks the route: auto, hid or server.
"""
import datetime
import glob
import json
import os
import plistlib
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile

from .. import config, inputserver
from . import ios_hid, ios_media, ios_wda
from .base import Backend, Unsupported

HERE = os.path.dirname(os.path.abspath(__file__))
RECORDER = os.path.join(os.path.dirname(HERE), "ios_recorder.py")
HOLDER = os.path.join(os.path.dirname(HERE), "ios_condition_holder.py")
CAPTURE_SOURCE = os.path.join(os.path.dirname(HERE), "ios_screen_recorder.swift")

# Android keycodes `key` accepts, mapped to what iOS has: a hardware button or a keyboard action.
BUTTONS = {"KEYCODE_HOME": "home", "3": "home", "KEYCODE_POWER": "lock", "26": "lock",
           "KEYCODE_VOLUME_UP": "volume-up", "24": "volume-up",
           "KEYCODE_VOLUME_DOWN": "volume-down", "25": "volume-down"}
KEYBOARD = {"KEYCODE_DEL": "delete", "67": "delete", "KEYCODE_ENTER": "return", "66": "return"}


def pmd3_path():
    """PYMOBILEDEVICE3 from config, else the conventional venv, else whatever is on PATH."""
    venv = os.path.expanduser("~/.venvs/pymobiledevice3/bin/pymobiledevice3")
    return config.get("PYMOBILEDEVICE3") or (venv if os.path.exists(venv) else shutil.which("pymobiledevice3") or venv)


ORIENTATIONS = ("portrait", "landscape-right", "landscape-left")

class IosBackend(Backend):
    name = "ios"

    @classmethod
    def discover(cls):
        if not os.path.exists(pmd3_path()):
            return {}
        try:
            listed = json.loads(cls.out([pmd3_path(), "usbmux", "list"]))
        except (subprocess.CalledProcessError, ValueError):
            print("pymobiledevice3 usbmux list failed; iPhones not listed", file=sys.stderr)
            return {}
        found = {d["UniqueDeviceID"]: d.get("ProductType", "") for d in listed if d.get("ConnectionType") == "USB"}
        return found or cls.discover_network()

    @classmethod
    def discover_network(cls):
        """Paired iPhones devicectl can reach over the network, used when none is on USB.
        Install and launch work this way; pymobiledevice3 captures (shot, log) may not."""
        if not shutil.which("xcrun"):
            return {}
        with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
            done = subprocess.run(["xcrun", "devicectl", "list", "devices", "--json-output", tmp.name],
                                  capture_output=True, timeout=30)
            if done.returncode:
                return {}
            try:
                devices = json.load(open(tmp.name))["result"]["devices"]
            except (ValueError, KeyError):
                return {}
        found = {}
        for d in devices:
            hw, conn = d.get("hardwareProperties", {}), d.get("connectionProperties", {})
            if (hw.get("reality") == "physical" and hw.get("platform") == "iOS"
                    and conn.get("pairingState") == "paired" and conn.get("transportType")):
                found[hw["udid"]] = hw.get("productType", "")
        return found

    def over_network(self):
        """True when the phone is reachable only through devicectl over the network, not usbmux."""
        if not hasattr(self, "_over_network"):
            try:
                listed = json.loads(self.out([pmd3_path(), "usbmux", "list"]))
                self._over_network = not any(d.get("UniqueDeviceID") == self.serial
                                             and d.get("ConnectionType") == "USB" for d in listed)
            except (subprocess.CalledProcessError, ValueError, OSError):
                self._over_network = True
        return self._over_network

    # --- plumbing ------------------------------------------------------------------------------
    def env(self):
        # pymobiledevice3 commands disagree on a --udid flag; this variable reaches all of them.
        return dict(os.environ, PYMOBILEDEVICE3_UDID=self.serial)

    def pmd3(self, *args):
        return [pmd3_path(), *args]

    def devicectl(self, subcommand, *args):
        """`subcommand` is the full path, e.g. "device info apps"; --device must follow it."""
        return ["xcrun", "devicectl", *subcommand.split(), "--device", self.serial, *args]

    def devicectl_json(self, subcommand, *args):
        with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
            self.out(self.devicectl(subcommand, *args, "--json-output", tmp.name))
            with open(tmp.name) as f:
                return json.load(f)["result"]

    def state(self, key):
        return inputserver.load_state().get(key, {}).get(self.serial)

    def remember(self, key, value):
        state = inputserver.load_state()
        state.setdefault(key, {})[self.serial] = value
        inputserver.save_state(state)

    def app_id(self):
        return config.require("APP_IOS_BUNDLE_ID")

    def app(self):
        # --include-all-apps so a system app (Safari, Settings) can be the app under test too.
        apps = self.devicectl_json("device info apps", "--bundle-id", self.app_id(), "--include-all-apps")["apps"]
        if apps:
            return apps[0]
        # devicectl lists no apps at all on iOS 27 (and on any locked phone); installation_proxy still
        # does. Shaped like a devicectl entry, so `url` prefixes the process executables as before.
        found = json.loads(self.out(self.pmd3("apps", "query", self.app_id()), env=self.env())).get(self.app_id())
        if not found or not found.get("Path"):
            return None
        return {"url": "file://" + found["Path"].rstrip("/") + "/", "bundleIdentifier": self.app_id(),
                "version": found.get("CFBundleShortVersionString"), "bundleVersion": found.get("CFBundleVersion")}

    def executable(self):
        return os.path.basename(self.app()["url"].rstrip("/")).removesuffix(".app")

    # --- app -----------------------------------------------------------------------------------
    def installed(self):
        return self.app() is not None

    def version(self):
        app = self.app()
        if not app:
            raise RuntimeError(f"{self.app_id()} is not installed")
        return [f"versionName={app.get('version')}", f"bundleVersion={app.get('bundleVersion')}"]

    def last_update(self):
        app = self.app()
        return json.dumps(app, sort_keys=True) if app else ""

    def install(self, path):
        self.sh(self.devicectl("device install app", path), stdout=subprocess.DEVNULL)

    def launch(self, environment=None, url=None):
        args = ["--activate"]
        if url:
            args += ["--payload-url", url]
        if environment:
            args += ["--environment-variables", json.dumps({k: str(v) for k, v in environment.items()})]
        if environment is not None:
            args.insert(0, "--terminate-existing")
        self.sh(self.devicectl("device process launch", *args, self.app_id()), stdout=subprocess.DEVNULL)

    def pids(self):
        app = self.app()
        if not app:
            return []
        procs = self.devicectl_json("device info processes")["runningProcesses"]
        return [p["processIdentifier"] for p in procs if p.get("executable", "").startswith(app["url"])]

    def pid(self):
        pids = self.pids()
        return pids[0] if pids else None

    def stop(self):
        for pid in self.pids():
            self.ok(self.devicectl("device process terminate", "--pid", str(pid)))

    def focus(self):
        """App state from the DVT notification stream's opening snapshot: Running in front,
        Suspended behind. The snapshot lists every app once, so read until ours shows up."""
        pid = self.pid()
        if pid is None:
            return False, "not running"
        if self.over_network():
            # DVT needs usbmux; over the network all devicectl can say is that the process runs.
            return True, "running (network connection: front/back not readable; plug in USB)"
        proc = subprocess.Popen(self.pmd3("developer", "dvt", "notifications"), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, env=self.env())
        state = "unknown"
        end = time.time() + 15
        try:
            for line in proc.stdout:
                if f'"pid": {pid},' in line and "applicationStateNotification" in line:
                    state = json.loads(line)[1][0].get("state_description", "unknown")
                    break
                if time.time() > end:
                    break
        finally:
            proc.kill()
        return state == "Running", f"{self.app_id()} pid {pid} {state}"

    def background(self):
        self.key("KEYCODE_HOME")

    def build_symbol_file(self, build_path):
        pattern = config.require("APP_IOS_SYMBOL_FILE")
        with zipfile.ZipFile(build_path) as z:
            names = [n for n in z.namelist() if glob.fnmatch.fnmatch(n, pattern)]
            if not names:
                raise RuntimeError(f"no {pattern} in {build_path}")
            return z.read(names[0])

    # --- device --------------------------------------------------------------------------------
    def locked(self):
        return bool(self.devicectl_json("device info lockState").get("passcodeRequired"))

    def keep_awake(self):
        """Hold a PreventUserIdleSystemSleep power assertion for DEVICE_IOS_AWAKE_SECONDS (default
        4 h) in a background holder, like location and network conditions. ⚠ Unmeasured whether it
        also stops the DISPLAY from auto-locking (2026-11-05), so the note still names Auto-Lock."""
        seconds = config.get("DEVICE_IOS_AWAKE_SECONDS", "14400")
        try:
            self._hold("awake", ["power-assertion", "PreventUserIdleSystemSleep", "device-testing", seconds],
                       "Hit Ctrl+C")
        except RuntimeError as e:
            return f"stay-awake: power assertion failed ({e}); set Auto-Lock to Never on the device"
        return f"stay-awake: power assertion held {seconds}s (pid {self.state('awake')}); if the screen still locks, set Auto-Lock to Never"

    def release_awake(self):
        self._release("awake")
        return "stay-awake: power assertion released"

    def display(self):
        cached = self.state("display")
        if cached:
            return cached
        info = json.loads(self.out(self.pmd3("developer", "core-device", "get-display-info"), env=self.env()))
        mode = info["displays"][0]["availableModes"][0]
        value = {"size": [int(v) for v in mode["size"]], "scale": mode.get("preferredUIScale", 1)}
        self.remember("display", value)
        return value

    def orientation(self):
        """DEVICE_IOS_ORIENTATION: portrait (default), landscape-right or landscape-left. The display
        reports its native portrait size and the HID service takes native coordinates, while a
        landscape app's screenshots come back landscape; this names which way round the app is."""
        value = (config.get("DEVICE_IOS_ORIENTATION") or "portrait").strip().lower()
        if value not in ORIENTATIONS:
            raise RuntimeError(f"DEVICE_IOS_ORIENTATION={value}: use {', '.join(ORIENTATIONS)}")
        return value

    def size(self):
        """Screen pixels the way screenshots and profiles see them: native size, turned for landscape."""
        w, h = self.display()["size"]
        return (max(w, h), min(w, h)) if self.orientation() != "portrait" else (w, h)

    def to_native(self, x, y):
        """Screen pixels (see size) to 0-1 fractions of the native portrait panel the HID service takes.
        landscape-right was measured on an iOS 27 iPhone (a tap on a landscape game's button);
        landscape-left is its mirror and is not yet measured."""
        w, h = self.size()
        fx, fy = x / w, y / h
        turn = self.orientation()
        if turn == "landscape-right":
            return 1 - fy, fx
        if turn == "landscape-left":
            return fy, 1 - fx
        return fx, fy

    def density(self):
        # A point is 160 dpi-equivalent, so `dp` in a nav profile means a point here.
        return int(self.display()["scale"] * 160)

    def screenshot(self, out):
        # CoreDevice's screen-capture service writes the same PNG as DVT and is a little faster
        # (median ~0.98 s against ~1.10 s over five runs); DVT stays as the fallback.
        done = subprocess.run(self.pmd3("developer", "core-device", "screen-capture", "screenshot", out),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.env())
        if done.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
            return
        self.sh(self.pmd3("developer", "dvt", "screenshot", out), stdout=sys.stderr,
                stderr=subprocess.DEVNULL, env=self.env())

    # --- input ---------------------------------------------------------------------------------
    def _server(self, path, **params):
        result = inputserver.call(self.serial, path, **params)
        if not result.get("ok"):
            raise RuntimeError(result.get("error"))
        return result

    def lockdown_value(self, key):
        """One key of `lockdown info`. pymobiledevice3 11 dropped `lockdown get --key`: its `get` now takes
        positional DOMAIN KEY, and a bare key reads as a domain and returns {}."""
        return json.loads(self.out(self.pmd3("lockdown", "info"), env=self.env())).get(key)

    def product_version(self):
        cached = self.state("productversion")
        if cached:
            return cached
        value = self.lockdown_value("ProductVersion")
        self.remember("productversion", value)
        return value

    def input_route(self):
        """`hid` (host-side CoreDevice, iOS 27+) or `server` (the app's input server).
        DEVICE_IOS_INPUT picks one; `auto` is `hid` from iOS 27 on, else `server`."""
        want = (config.get("DEVICE_IOS_INPUT") or "auto").strip().lower()
        if want not in ("auto", "hid", "server"):
            raise RuntimeError(f"DEVICE_IOS_INPUT={want}: use auto, hid or server")
        if want != "auto":
            return want
        major = int(self.product_version().split(".")[0])
        return "hid" if major >= 27 else "server"

    def tap(self, x, y):
        route = self.input_route()
        if route == "hid":
            return ios_hid.tap(self, x, y)
        w, h = self.size()
        self._server("/tap", x=round(x / w, 5), y=round(y / h, 5))

    def swipe(self, x1, y1, x2, y2, ms):
        route = self.input_route()
        if route == "hid":
            return ios_hid.swipe(self, x1, y1, x2, y2, ms)
        w, h = self.size()
        self._server("/swipe", x1=round(x1 / w, 5), y1=round(y1 / h, 5), x2=round(x2 / w, 5),
                     y2=round(y2 / h, 5), ms=int(ms))

    def key(self, keycode):
        keycode = str(keycode)
        if keycode in BUTTONS:
            self.sh(self.pmd3("developer", "core-device", "hid", "button", BUTTONS[keycode]),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.env())
        elif keycode in KEYBOARD:
            # Editing keys stay on the app's input server on every route; the HID CLI has no named key.
            self._server("/key", special=KEYBOARD[keycode])
        else:
            raise Unsupported(f"key {keycode} (iOS maps HOME, POWER, VOLUME_UP/DOWN, DEL, ENTER)")

    def type_text(self, text):
        route = self.input_route()
        if route == "hid":
            return ios_hid.type_text(self, text)
        self._server("/key", text=text)

    # --- native UI through WebDriverAgent: opt-in commands, never an input route ----------------
    def _wda(self, fn, *args):
        try:
            return fn(self, *args)
        except ios_wda.WdaError as e:
            raise RuntimeError(str(e))

    def native_start(self): return self._wda(ios_wda.start)
    def native_stop(self): return self._wda(ios_wda.stop)
    def native_status(self): return self._wda(ios_wda.status)
    def native_build(self): return self._wda(ios_wda.build)
    def native_install(self): return self._wda(ios_wda.install)
    def native_tap(self, x, y): return self._wda(ios_wda.tap, x, y)
    def native_hold(self, x, y, ms): return self._wda(ios_wda.hold, x, y, ms)
    def native_swipe(self, x1, y1, x2, y2, ms, hold_ms=50): return self._wda(ios_wda.swipe, x1, y1, x2, y2, ms, hold_ms)
    def native_press(self, label, hold_ms=None): return self._wda(ios_wda.press, label, hold_ms)
    def native_source(self): return self._wda(ios_wda.source)
    def native_keys(self, labels, gap_ms): return self._wda(ios_wda.keys, labels, gap_ms)
    def native_alert(self, action): return self._wda(ios_wda.alert, action)

    # --- Photos library, through the media helper app ------------------------------------------------
    def media_build(self): return ios_media.build(self)
    def media_install(self): return ios_media.install(self)

    def media_run(self, op, files=(), album="Device Testing", extra=None):
        def answer():
            # The Photos prompt offers Limit / Allow Full Access / Don't Allow; a generic accept may
            # pick Limit, which hides the library from the helper. Press Full Access by name.
            found = self.native_alert("text")
            if found.get("alert"):
                text = str(found.get("text"))
                print(f"media    Photos prompt: {text.splitlines()[0]}", file=sys.stderr)
                # Hiding/unhiding needs a per-change system confirmation ("Allow X to hide 3 photos?").
                if " to unhide " in text or " to hide " in text:
                    self.native_press("Unhide" if " to unhide " in text else "Hide")
                    return
                # TCC's button id is stable across locales; the label is not. Measured iPadOS 27.2.
                for target in ("kTCCServicePhotos-allow-button", "Allow Full Access"):
                    try:
                        self.native_press(target)
                        return
                    except Exception:
                        continue
                raise RuntimeError("Photos prompt up but no Full Access button found; never `alert accept` it "
                                   "(that can choose Limited or Don't Allow)")
        return ios_media.run(self, op, files, album, answer_alert=answer, extra=extra)

    # --- on-device recording: iOS's own Screen Recording, through WDA + the media helper ---------
    def _control_center(self, open_it):
        w, _ = self.size()
        if open_it:
            self.native_swipe(int(w * 0.96), 4, int(w * 0.96), 1000, 400)
        else:
            # One swipe up from the bottom edge closes Control Center; a second would go Home.
            h = self.size()[1]
            self.native_swipe(w // 2, h - 6, w // 2, int(h * 0.6), 300)
        time.sleep(1.2)

    def _device_record_toggle(self):
        self._control_center(True)
        try:
            self.native_press("Screen Recording")
        finally:
            time.sleep(0.6)
            self._control_center(False)

    def _device_record_start(self, name, directory):
        """Starts the device's built-in Screen Recording (Control Center, 3 s countdown). The video
        is recorded ON the device, which is what App Review asks for, and lands in Photos."""
        self.remember("rec_since", time.time() - 2)
        self._device_record_toggle()
        time.sleep(3.5)  # the countdown; the first frames show Control Center closing
        self.remember("rectype", "device")
        print(f"recording on the device (Screen Recording), name {name}", file=sys.stderr)
        return {"type": "device", "name": name}

    @staticmethod
    def _red_pill(png_path, rows=0.03):
        """(x, y) pixel centre of the red screen-recording pill in the status bar, or None. Stdlib
        PNG decode of the top strip only (8-bit RGB/RGBA, non-interlaced, which is what iOS
        screenshots are)."""
        import struct
        import zlib
        with open(png_path, "rb") as f:
            data = f.read()
        pos, idat, w = 8, b"", 0
        while pos < len(data):
            length, kind = struct.unpack(">I4s", data[pos:pos + 8])
            chunk = data[pos + 8:pos + 8 + length]
            if kind == b"IHDR":
                w, h, depth, ctype = struct.unpack(">IIBB", chunk[:10])
                if depth not in (8, 16) or ctype not in (2, 6):
                    return None
                # CoreDevice screenshots are 16-bit RGB (measured iPadOS 27.2), DVT ones 8-bit.
                step = depth // 8
                bpp = (4 if ctype == 6 else 3) * step
            elif kind == b"IDAT":
                idat += chunk
            pos += 12 + length
        raw = zlib.decompress(idat)
        stride, limit = w * bpp, max(1, int(h * rows))
        prev, xs, ys = bytearray(stride), [], []
        for y in range(limit):
            ftype, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b, c = prev[i], (prev[i - bpp] if i >= bpp else 0)
                if ftype == 1: line[i] = (line[i] + a) & 255
                elif ftype == 2: line[i] = (line[i] + b) & 255
                elif ftype == 3: line[i] = (line[i] + ((a + b) >> 1)) & 255
                elif ftype == 4:
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
            for x in range(0, w, 2):
                r, g, bl = line[x * bpp], line[x * bpp + step], line[x * bpp + 2 * step]  # high bytes
                if r > 200 and g < 90 and bl < 90:
                    xs.append(x)
                    ys.append(y)
            prev = line
        if len(xs) < 40:
            return None
        return (sorted(xs)[len(xs) // 2], sorted(ys)[len(ys) // 2])

    def _device_record_stop(self, name, directory):
        # Stop through the status-bar pill ("Stop screen recording?" > Stop): Control Center opens
        # on whichever page was last shown, so its Screen Recording button is not reliably there.
        shot = os.path.join(directory, f"{name}.stop.png")
        try:
            self.screenshot(shot)
            pill = self._red_pill(shot)
        except Exception as e:  # a failed find falls back to Control Center below
            print(f"record   pill search failed: {e}", file=sys.stderr)
            pill = None
        if pill:
            self.native_tap(*pill)
            time.sleep(1.2)
            self.native_press("Stop")
        else:
            print("record   no recording pill found; trying Control Center", file=sys.stderr)
            self._device_record_toggle()
        time.sleep(4)  # the file is finalised into Photos after the stop
        since = self.state("rec_since") or 0
        result = self.media_run("pull", extra={"since": since, "name": name})
        local = os.path.join(directory, os.path.basename(result["file"]))
        ios_media.copy_from(self, result["file"], local)
        self.remember("rectype", None)
        print(f"pulled {local} ({result.get('duration', 0):.1f}s, {result.get('bytes', 0)} bytes)", file=sys.stderr)
        return local

    # --- logs: a background syslog collector per phone, filtered to the app's process --------
    def _log_file(self):
        return config.scratch("logs", f"{self.serial}.log")

    def _collector_alive(self):
        pid = self.state("logpid")
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _start_collector(self, keep=False):
        if not keep:
            with open(self._log_file(), "w"):
                pass
        log = open(self._log_file(), "a")
        # DEBUG is most of the volume (CoreMotion, CoreLocation, networking) and never the app's own
        # log, which arrives at NOTICE from the app's image; DEVICE_IOS_SYSLOG_ARGS overrides.
        extra = (config.get("DEVICE_IOS_SYSLOG_ARGS") or "--no-debug").split()
        # The relay drops now and then (measured: when a USB screen capture starts, and mid-take), so a
        # supervisor shell restarts it into the same file; a few lines in the gap are lost.
        command = " ".join(shlex.quote(a) for a in self.pmd3("syslog", "live", "--process-name", self.executable(), *extra))
        proc = subprocess.Popen(["/bin/sh", "-c", f"while true; do {command}; sleep 1; done"], stdout=log,
                                stderr=open(self._log_file() + ".err", "a"), start_new_session=True, env=self.env())
        self.remember("logpid", proc.pid)

    def log_prepare(self, buffer):
        if not self._collector_alive():
            self._start_collector()

    def log_clear(self):
        pid = self.state("logpid")
        if pid:
            try:
                os.killpg(pid, signal.SIGTERM)  # the supervisor shell and its syslog child
            except OSError:
                pass
        self._start_collector()

    def log_lines(self):
        if not self._collector_alive():
            print("no syslog collector was running; started one, so lines from its gap are missing", file=sys.stderr)
            self._start_collector(keep=True)
            time.sleep(1)
        with open(self._log_file(), errors="replace") as f:
            return f.read().splitlines()

    # --- capture and state ---------------------------------------------------------------------
    def record_start(self, name, directory):
        """DEVICE_IOS_RECORD picks the recorder: `capture` (QuickTime's USB screen capture, full rate,
        needs Camera access for the host app), `stream` (CoreDevice video, iOS 27), `shots` (~5 fps
        screenshots). `auto` tries capture, then stream on the hid route, then shots."""
        mode = (config.get("DEVICE_IOS_RECORD") or "auto").strip().lower()
        if mode not in ("auto", "capture", "stream", "shots", "device"):
            raise RuntimeError(f"DEVICE_IOS_RECORD={mode}: use auto, capture, stream, shots or device")
        if self.state("rectype") == "device":
            # A device recording stopped by hand leaves the flag behind; trust the screen, not it.
            probe = config.scratch("rec-probe.png")
            self.screenshot(probe)
            if self._red_pill(probe):
                raise RuntimeError("a device Screen Recording is already running (red pill in the status bar)")
            self.remember("rectype", None)
        if self._recording():
            raise RuntimeError("a recording is already running")
        if mode == "device":
            # The device's own Screen Recording (red pill in the status bar while it runs); needs
            # WDA and the media helper. `capture` shows no indicator on the device.
            return self._device_record_start(name, directory)
        if mode in ("auto", "capture"):
            try:
                return self._capture_start(name, directory)
            except RuntimeError as e:
                if mode == "capture":
                    raise
                print(f"screen capture unavailable ({e}); falling back", file=sys.stderr)
        if mode == "stream" or (mode == "auto" and self.input_route() == "hid"):
            self.remember("rectype", "stream")
            return ios_hid.record_start(self, name, directory)
        self.remember("rectype", "shots")
        return self._shots_start(name, directory)

    def record_stop(self, name, directory):
        kind = self.state("rectype")
        if kind == "device":
            return self._device_record_stop(name, directory)
        if kind == "capture":
            return self._capture_stop(name, directory)
        if kind == "stream" or os.path.exists(os.path.join(directory, f"{name}.rtp")):
            return ios_hid.record_stop(self, name, directory)
        return self._shots_stop(name, directory)

    def _recording(self):
        pid = self.state("recpid")
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _capture_binary(self):
        """The Swift recorder, compiled on first use and again whenever the source changes."""
        binary = config.scratch("bin", "ios_screen_recorder")
        if not os.path.exists(binary) or os.path.getmtime(binary) < os.path.getmtime(CAPTURE_SOURCE):
            self.sh(["xcrun", "swiftc", "-O", CAPTURE_SOURCE, "-o", binary], stdout=sys.stderr)
        return binary

    def _capture_start(self, name, directory):
        mov = os.path.join(directory, f"{name}.mov")
        start = os.path.join(directory, f"{name}.start")
        log_path = os.path.join(directory, f"{name}.recorder.log")
        for path in (mov, start):
            if os.path.exists(path):
                os.remove(path)
        device_name = self.lockdown_value("DeviceName")
        proc = subprocess.Popen([self._capture_binary(), device_name, mov, start], stdout=open(log_path, "w"),
                                stderr=subprocess.STDOUT, start_new_session=True)
        end = time.time() + 20
        while time.time() < end and proc.poll() is None and not os.path.exists(start):
            time.sleep(0.25)
        if not os.path.exists(start):
            proc.kill()
            with open(log_path) as f:
                raise RuntimeError(f.read().strip().splitlines()[-1] if os.path.getsize(log_path) else "recorder never started")
        self.remember("recpid", proc.pid)
        self.remember("rectype", "capture")
        with open(start) as f:
            started = f.read().strip()
        return f"recording {mov} (started {started}, screen capture)"

    def _capture_stop(self, name, directory):
        pid = self.state("recpid")
        if not pid:
            raise RuntimeError("no recording running")
        try:
            os.kill(pid, signal.SIGINT)
        except OSError:
            pass
        end = time.time() + 20  # the movie file is finalized before the recorder exits
        while self._recording() and time.time() < end:
            time.sleep(0.25)
        self.remember("recpid", None)
        self.remember("rectype", None)
        mov = os.path.join(directory, f"{name}.mov")
        if not os.path.exists(mov) or os.path.getsize(mov) == 0:
            raise RuntimeError(f"no video written -- see {directory}/{name}.recorder.log")
        out = os.path.join(directory, f"{name}.mp4")
        self.sh(["ffmpeg", "-y", "-loglevel", "error", "-i", mov, "-c", "copy", "-movflags", "+faststart", out])
        os.remove(mov)
        return out

    def _shots_start(self, name, directory):
        if self.state("recpid"):
            try:
                os.kill(self.state("recpid"), 0)
                raise RuntimeError("a recording is already running")
            except OSError:
                pass
        frames = os.path.join(directory, f"{name}.frames")
        shutil.rmtree(frames, ignore_errors=True)
        os.makedirs(frames)
        python = os.path.join(os.path.dirname(pmd3_path()), "python")
        proc = subprocess.Popen([python, RECORDER, self.serial, frames], stdout=subprocess.DEVNULL,
                                stderr=open(os.path.join(directory, f"{name}.recorder.log"), "w"),
                                start_new_session=True)
        self.remember("recpid", proc.pid)
        now = time.time()
        started = time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now * 1000) % 1000:03d}"  # HH:MM:SS.mmm like the other recorders
        with open(os.path.join(directory, f"{name}.start"), "w") as f:
            f.write(started + "\n")
        time.sleep(4)
        if proc.poll() is not None:
            raise RuntimeError(f"recorder exited -- see {directory}/{name}.recorder.log")
        return f"recording {frames} (started {started}, screenshot frames)"

    def _shots_stop(self, name, directory):
        pid = self.state("recpid")
        if not pid:
            raise RuntimeError("no recording running")
        try:
            os.kill(pid, signal.SIGINT)
        except OSError:
            pass
        frames = os.path.join(directory, f"{name}.frames")
        timing = os.path.join(frames, "timing.json")
        end = time.time() + 15
        while not os.path.exists(timing) and time.time() < end:
            time.sleep(0.5)
        self.remember("recpid", None)
        with open(timing) as f:
            t = json.load(f)
        fps = max(1.0, t["frames"] / max(t["seconds"], 0.1))
        out = os.path.join(directory, f"{name}.mp4")
        self.sh(["ffmpeg", "-y", "-loglevel", "error", "-framerate", f"{fps:.2f}", "-i",
                 os.path.join(frames, "frame_%06d.png"), "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", out])
        shutil.rmtree(frames, ignore_errors=True)  # keep the video, not the frames
        return out

    def prefs_text(self):
        path = config.require("APP_IOS_PREFS_FILE").format(bundle=self.app_id())
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, "prefs.plist")
            self.out(self.devicectl("device copy from", "--domain-type", "appDataContainer",
                                    "--domain-identifier", self.app_id(), "--source", path,
                                    "--destination", local))
            with open(local, "rb") as f:
                data = plistlib.load(f)
        return "\n".join(f"{k} = {v!r}" for k, v in sorted(data.items()))

    # --- input server and appearance -----------------------------------------------------------
    def forward(self, local_port, device_port):
        pid = self.state("fwdpid")
        if pid:
            try:
                os.kill(pid, 0)
                return
            except OSError:
                pass
        with open(config.scratch(f"forward-{self.serial}.log"), "a") as log:
            proc = subprocess.Popen(self.pmd3("usbmux", "forward", "--serial", self.serial, str(local_port),
                                              str(device_port)), stdout=log, stderr=log,
                                    start_new_session=True, env=self.env())
        self.remember("fwdpid", proc.pid)

    def system_style(self, set_to=None):
        cmd = self.pmd3("developer", "core-device", "user-interface-style")
        if set_to:
            self.ok(cmd + [set_to], env=self.env())
        return json.loads(self.out(cmd, env=self.env()))

    # --- quiet ---------------------------------------------------------------------------------
    # No host-side API sets Focus or notification previews (pymobiledevice3 has none), so the
    # person does it and `quiet on --confirmed` records when, per device, in the shared state file.
    QUIET_STALE_HOURS = 12
    QUIET_STEPS = ["Control Center > Focus > Do Not Disturb on",
                   "Settings > Notifications > Show Previews > Never (a banner that slips through shows no content)"]

    def _quiet_confirmed_at(self):
        return inputserver.load_state().get("quiet", {}).get(self.serial, {}).get("confirmed_at")

    def _quiet_store(self, stamp):
        state = inputserver.load_state()
        quiet = state.setdefault("quiet", {})
        if stamp is None:
            quiet.pop(self.serial, None)
        else:
            quiet[self.serial] = {"confirmed_at": stamp}
        inputserver.save_state(state)

    def quiet_on(self, confirmed=False):
        if confirmed:
            self._quiet_store(time.time())
        return self.quiet_state()

    def quiet_off(self):
        self._quiet_store(None)
        state = self.quiet_state()
        state["steps"] = ["Control Center > Focus > Do Not Disturb off",
                          "Settings > Notifications > Show Previews back to When Unlocked (or what it was)"]
        return state

    def quiet_state(self):
        stamp = self._quiet_confirmed_at()
        state = {"quiet": False, "manual": True, "steps": list(self.QUIET_STEPS), "confirmed": stamp is not None}
        if stamp is None:
            state["detail"] = "not confirmed"
            return state
        age_h = max(0.0, (time.time() - float(stamp)) / 3600)
        stale = age_h > self.QUIET_STALE_HOURS
        state.update({"quiet": not stale, "age_h": round(age_h, 1), "stale": stale,
                      "detail": f"confirmed {age_h:.1f} h ago" + (f", stale after {self.QUIET_STALE_HOURS} h" if stale else "")})
        return state

    def rotate(self, direction):
        # CoreDevice's orientation service turns the device itself, so it rotates even with the
        # orientation lock on; there is no getter, the new orientation comes back from each turn.
        state = json.loads(self.out(self.pmd3("developer", "core-device", "rotate", direction), env=self.env()))
        return {"orientation": state.get("currentDeviceOrientation"),
                "orientationLocked": state.get("currentDeviceOrientationLocked")}

    def clipboard_set(self, text):
        subprocess.run(self.pmd3("developer", "core-device", "copy"), input=text.encode("utf-8"),
                       check=True, capture_output=True, env=self.env())

    def clipboard_get(self):
        return self.out(self.pmd3("developer", "core-device", "paste"), env=self.env()).rstrip("\n")

    def keyboard_native(self):
        native = self._server("/keyboard").get("state", {})
        focused = native.get("focused") or {}
        return {"inputMode": focused.get("inputMode"), "keyboardAppearance": focused.get("keyboardAppearance"),
                "windows": native.get("windows")}

    # --- simulation: DVT services that last only while their connection is open ---------------------
    def _hold(self, key, args, ready, timeout=40):
        """Start a pymobiledevice3 command that applies something and then waits (location, network
        condition) and keep it running in the background; its pid is stored under `key`. Returns once
        `ready` shows in its output."""
        self._release(key)
        log_path = config.scratch(f"{key}-{self.serial}.log")
        with open(log_path, "w"):
            pass
        proc = subprocess.Popen(self.pmd3(*args), stdout=open(log_path, "w"), stderr=subprocess.STDOUT,
                                start_new_session=True, env=dict(self.env(), PYTHONUNBUFFERED="1"))
        self.remember(key, proc.pid)
        end = time.time() + timeout
        while time.time() < end:
            with open(log_path, errors="replace") as f:
                body = f.read()
            if ready in body:
                return
            if proc.poll() is not None:
                raise RuntimeError(f"{' '.join(args[:4])} failed: {body.strip().splitlines()[-1] if body.strip() else 'no output'}")
            time.sleep(0.3)
        raise RuntimeError(f"{' '.join(args[:4])} never became ready; see {log_path}")

    def _release(self, key):
        pid = self.state(key)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            self.remember(key, None)

    def location_set(self, lat, lng):
        self._hold("locpid", ["developer", "dvt", "simulate-location", "set", "--", str(lat), str(lng)],
                   "Press Ctrl+C")

    def location_route(self, points):
        # `play` paces by GPX timestamps, so write the points with times and let it walk them.
        base = datetime.datetime(2000, 1, 1)
        path = config.scratch("route.gpx")
        with open(path, "w") as f:
            f.write('<?xml version="1.0"?><gpx version="1.1" creator="device-testing"><trk><trkseg>\n')
            for lat, lng, at in points:
                stamp = (base + datetime.timedelta(seconds=at)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                f.write(f'<trkpt lat="{lat:.7f}" lon="{lng:.7f}"><time>{stamp}</time></trkpt>\n')
            f.write("</trkseg></trk></gpx>\n")
        self._hold("locpid", ["developer", "dvt", "simulate-location", "play", path], "Press Ctrl+C",
                   timeout=points[-1][2] + 60)

    def location_clear(self):
        self._release("locpid")
        self.out(self.pmd3("developer", "dvt", "simulate-location", "clear"), env=self.env())

    def network_list(self):
        listed = json.loads(self.out(self.pmd3("developer", "dvt", "condition", "list"), env=self.env()))
        rows = []
        for condition in listed:
            for profile in condition["profiles"]:
                rows.append((profile["identifier"], f"{condition['name']}: {profile['name']}"))
        return rows

    def network_set(self, profile, relaunch=False, environment=None):
        """A condition lasts while its holder runs and reaches only processes launched inside the
        holder's DVT session, so `relaunch` starts the app there (killing a running copy)."""
        known = [identifier for identifier, _ in self.network_list()]
        if profile not in known:
            raise RuntimeError(f"unknown condition '{profile}' -- `network list` has {len(known)}")
        python = os.path.join(os.path.dirname(pmd3_path()), "python")
        args = [python, HOLDER, self.serial, profile]
        if relaunch:
            args += ["--launch", self.app_id()]
            for key, value in (environment or {}).items():
                args += ["--env", f"{key}={value}"]
        self._hold_command("netpid", args, "ready")
        # `clear` needs to know the app was started by the holder: that copy is left black afterwards.
        self.remember("netlaunch", {"environment": environment or {}} if relaunch else None)

    def _hold_command(self, key, argv, ready, timeout=60):
        """Like `_hold` for a script that is not a pymobiledevice3 subcommand."""
        self._release(key)
        log_path = config.scratch(f"{key}-{self.serial}.log")
        proc = subprocess.Popen(argv, stdout=open(log_path, "w"), stderr=subprocess.STDOUT,
                                start_new_session=True, env=dict(self.env(), PYTHONUNBUFFERED="1"))
        self.remember(key, proc.pid)
        end = time.time() + timeout
        while time.time() < end:
            with open(log_path, errors="replace") as f:
                body = f.read()
            if ready in body.split():
                return
            if proc.poll() is not None:
                raise RuntimeError(f"condition holder failed: {body.strip().splitlines()[-1] if body.strip() else 'no output'}")
            time.sleep(0.3)
        raise RuntimeError(f"condition holder never became ready; see {log_path}")

    def network_clear(self):
        pid = self.state("netpid")
        self._release("netpid")
        # The holder clears the condition itself on SIGTERM; give it a moment so the list below does
        # not read its last state.
        for _ in range(40):
            try:
                os.kill(pid, 0)
            except (OSError, TypeError):
                break
            time.sleep(0.25)
        # The device can keep reporting the condition for a few seconds; clear until it reads inactive.
        for _ in range(8):
            self.out(self.pmd3("developer", "dvt", "condition", "clear"), env=self.env())
            listed = json.loads(self.out(self.pmd3("developer", "dvt", "condition", "list"), env=self.env()))
            if not any(c.get("isActive") for c in listed):
                break
            time.sleep(3)
        else:
            raise RuntimeError("the device still reports a network condition active after eight clears")
        # An app the holder launched stays on a black screen once the session ends: stop it and start a
        # normal launch (with the input server env when it was launched with it).
        launched = self.state("netlaunch")
        if launched is not None:
            self.remember("netlaunch", None)
            self.stop()
            self.launch(environment=launched["environment"] or None)
            return True
        return False

    # --- performance: DVT graphics (frame rate) and the process monitor (CPU, memory) -----------
    def perf_sample(self, seconds):
        pid = self.pid()
        mon_path = config.scratch("perf", f"{self.serial}.monitor.jsonl")
        if os.path.exists(mon_path):
            os.remove(mon_path)
        mon = subprocess.Popen(self.pmd3("developer", "dvt", "sysmon", "process", "monitor", "process", "-f",
                                         f"name={self.executable()}", "--choose", "last", "-k", "pid", "-k", "cpuUsage", "-k",
                                         "physFootprint", "-o", mon_path),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.env())
        gfx = subprocess.Popen(self.pmd3("developer", "dvt", "graphics"), stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, env=self.env())
        rows, begin = [], None
        try:
            end = time.time() + seconds + 60  # the tunnel takes a few seconds to come up
            while time.time() < end:
                ready, _, _ = select.select([gfx.stdout], [], [], 1)
                if not ready:
                    continue
                line = gfx.stdout.readline()
                if not line:
                    break
                try:
                    sample = json.loads(line)
                except ValueError:
                    continue
                now = time.time()
                begin = begin or now
                if now - begin > seconds:
                    break
                rows.append({"wall": now, "t": now - begin, "fps": sample.get("CoreAnimationFramesPerSecond"),
                             "cpu": None, "mem_mb": None, "gpu": sample.get("Device Utilization %")})
        finally:
            gfx.kill()
            mon.terminate()
        records = []
        if os.path.exists(mon_path):
            with open(mon_path) as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        records.append((datetime.datetime.fromisoformat(r["timestamp"]).timestamp(), r))
                    except (ValueError, KeyError):
                        continue
        for row in rows:
            if records:
                near = min(records, key=lambda rec: abs(rec[0] - row["wall"]))[1]
                row["cpu"] = near.get("cpuUsage")
                row["mem_mb"] = near["physFootprint"] / 1048576 if near.get("physFootprint") else None
        notes = ["fps is the compositor's (system-wide); cpu is percent of one core; memory is the app's footprint",
                 "no per-frame times from DVT, so the p95 frame time is n/a"]
        if not rows:
            notes.append("the graphics stream produced no samples")
        return {"rows": rows, "frame_ms": [], "notes": notes}

    # --- crashes: the phone's CrashReports, filtered to the app's executable ------------------------
    CRASH_STAMP = re.compile(r"(\d{4}-\d\d-\d\d)-(\d{6})")

    def crashes(self):
        exe = self.executable()
        found = []
        for line in self.out(self.pmd3("crash", "ls"), env=self.env()).splitlines():
            base = os.path.basename(line.strip())
            if not base.startswith(exe + "-") and not base.startswith(exe + "."):
                continue
            m = self.CRASH_STAMP.search(base)
            when = datetime.datetime.strptime(m.group(1) + m.group(2), "%Y-%m-%d%H%M%S") if m else None
            found.append({"name": base, "time": when, "kind": "crash", "ref": line.strip()})
        return found

    def crash_pull(self, entries, directory):
        pattern = "^(" + "|".join(re.escape(e["name"]) for e in entries) + ")$"
        self.out(self.pmd3("crash", "pull", directory, "--match", pattern), env=self.env())
        return [os.path.join(directory, e["name"]) for e in entries]

    # --- WebDriverAgent signing profile -------------------------------------------------------
    def wda_expiry(self):
        runner = ios_wda.runner_bundle()
        profiles = json.loads(self.out(self.pmd3("provision", "list"), env=self.env()))
        dates = [datetime.datetime.fromisoformat(p["ExpirationDate"]) for p in profiles
                 if p.get("Entitlements", {}).get("application-identifier", "").endswith("." + runner)]
        return max(dates) if dates else None
