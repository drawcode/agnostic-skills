"""The per-platform interface. A backend supplies primitives only; every command is written once
over them in commands.py, so output and arguments match on every platform.

A new platform is one subclass, added to BACKENDS in backends/__init__.py. Implement what the
platform can do; anything left raises Unsupported, which the front reports as exit 3.
"""
import subprocess
import sys


class Unsupported(Exception):
    pass


class Backend:
    name = ""

    def __init__(self, serial):
        self.serial = serial

    @classmethod
    def discover(cls):
        """serial -> model for every attached device of this platform. Cheap and quiet."""
        return {}

    def _no(self, what):
        raise Unsupported(what)

    # --- app -----------------------------------------------------------------------------------
    def app_id(self): self._no("app id")
    def installed(self): self._no("installed")
    def version(self):
        """Lines describing the installed build, printed by `version`."""
        self._no("version")
    def install(self, path): self._no("install")
    def launch(self, environment=None, url=None):
        """Start the app in front. `environment` is passed as env vars (iOS) or string extras (Android);
        `url` is a deep link handed to the app as it opens."""
        self._no("launch")
    def stop(self):
        """Ask the app to stop; the caller polls pid() to prove it."""
        self._no("stop")
    def pid(self):
        """The app's pid, or None when it is not running."""
        self._no("pid")
    def focus(self):
        """(app is in front, description of what is in front)."""
        self._no("focus")
    def background(self): self._no("bg")
    def build_symbol_file(self, build_path):
        """Bytes of the file inside a build that a symbol check greps."""
        self._no("build symbol check")

    # --- device --------------------------------------------------------------------------------
    def locked(self): self._no("lock state")
    def keep_awake(self):
        """Keep the screen on while plugged in; returns a note for preflight."""
        return "stay-awake not controllable on this platform"
    def release_awake(self):
        """Undo keep_awake where it holds something; a platform setting that is harmless left on stays."""
        return "stay-awake: nothing held"
    def size(self):
        """Screen (width, height) in pixels, the space tap and nav coordinates use."""
        self._no("screen size")
    def density(self):
        """dpi-equivalent, so `dp` in a nav profile means the same on every platform."""
        self._no("density")
    def screenshot(self, out): self._no("shot")

    # --- input ---------------------------------------------------------------------------------
    def tap(self, x, y): self._no("tap")
    def swipe(self, x1, y1, x2, y2, ms): self._no("swipe")
    def key(self, keycode):
        """An Android-style KEYCODE_* name or number; backends map what they can."""
        self._no(f"key {keycode}")
    def type_text(self, text): self._no("type")

    # --- native UI (opt-in, iOS WebDriverAgent) ------------------------------------------------
    def native_start(self): self._no("native start")
    def native_stop(self): self._no("native stop")
    def native_status(self): self._no("native status")
    def native_build(self): self._no("native build")
    def native_install(self): self._no("native install")
    def native_tap(self, x, y): self._no("native tap")
    def native_hold(self, x, y, ms): self._no("native hold")
    def native_swipe(self, x1, y1, x2, y2, ms, hold_ms=50): self._no("native swipe")
    def native_press(self, label, hold_ms=None): self._no("native press")
    def native_source(self): self._no("native source")
    def native_keys(self, labels, gap_ms): self._no("native keys")
    def native_alert(self, action): self._no("alert")

    # --- logs ----------------------------------------------------------------------------------
    def log_prepare(self, buffer):
        """Make the log reach far enough back for a pass (ring buffer, collector)."""
    def log_clear(self): self._no("log clear")
    def clear_data(self, grants=()):
        """Wipe the app's data (a first-run state), then grant the named runtime permissions."""
        self._no("clear data")
    def log_lines(self):
        """Every app log line currently held."""
        self._no("log")

    # --- capture and state ---------------------------------------------------------------------
    def record_start(self, name, directory, segmented=False): self._no("record")
    def record_stop(self, name, directory): self._no("record")
    def prefs_text(self): self._no("prefs")

    # --- input server and appearance -----------------------------------------------------------
    def forward(self, local_port, device_port): self._no("forward")
    def system_style(self, set_to=None): self._no("appearance")
    def rotate(self, direction):
        """Turn the device 90 degrees, `left` (counterclockwise) or `right`, as if physically turned;
        returns {"orientation": ...} as the device reports it."""
        self._no("rotate")
    def clipboard_set(self, text):
        """Put text on the device clipboard."""
        self._no("clipboard")
    def clipboard_get(self):
        """The device clipboard's text."""
        self._no("clipboard")
    def keyboard_native(self):
        return {}

    # --- quiet: no notifications in a recording or a screenshot --------------------------------
    def quiet_on(self, confirmed=False):
        """Silence notifications and overlays; returns what quiet_state() would. A platform with no
        host-side switch returns manual=True and the steps for the person; `confirmed` records that
        the person did them."""
        self._no("quiet")
    def quiet_off(self):
        """Undo quiet_on; returns what quiet_state() would."""
        self._no("quiet")
    def quiet_state(self):
        """{"quiet": bool, "manual": bool, "detail": str, "steps": [..] when manual, "age_h" and
        "stale" when a confirmation is held}. Must be cheap; shot calls it."""
        self._no("quiet")

    # --- simulation, performance, crashes ------------------------------------------------------
    def location_set(self, lat, lng):
        """Report this position to every app until `location_clear`."""
        self._no("location")
    def location_route(self, points):
        """Walk [(lat, lng, seconds_from_start), ...]; returns when the last point is reached and
        leaves it simulated."""
        self._no("location route")
    def location_clear(self):
        """Stop simulating; the real GPS returns."""
        self._no("location")
    def network_list(self):
        """[(identifier, description)] of the network conditions the device can simulate."""
        self._no("network")
    def network_set(self, profile, relaunch=False, environment=None):
        """Simulate `profile`. `relaunch` restarts the app under it (iOS applies a condition only to
        processes started in its own session), with `environment` as launch env vars."""
        self._no("network")
    def network_clear(self): self._no("network")
    def perf_sample(self, seconds):
        """Sample the app for `seconds`. Returns {"rows": [{"t", "fps", "cpu", "mem_mb"}, ...],
        "frame_ms": [individual frame times] or [], "notes": [..]}; a value the platform cannot
        read is None."""
        self._no("perf")
    def crashes(self):
        """Crash reports for the app: [{"name", "time" (datetime or None), "kind", "ref"}]."""
        self._no("crashes")
    def crash_pull(self, entries, directory):
        """Write each entry from `crashes()` into `directory`; returns the paths."""
        self._no("crashes")
    def wda_expiry(self):
        """(expiry datetime in UTC or None when not installed) for the WebDriverAgent runner."""
        self._no("wda expiry")

    # --- helpers -------------------------------------------------------------------------------
    @staticmethod
    def sh(cmd, **kw):
        print("+ " + " ".join(cmd), file=sys.stderr)
        return subprocess.run(cmd, check=True, **kw)

    @staticmethod
    def out(cmd, **kw):
        return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout

    @staticmethod
    def ok(cmd, **kw):
        return subprocess.run(cmd, capture_output=True, **kw).returncode == 0
