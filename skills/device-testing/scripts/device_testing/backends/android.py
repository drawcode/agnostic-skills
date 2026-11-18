"""Android over adb. Primitives only; the commands live in commands.py."""
import datetime
import os
import re
import shutil
import subprocess
import time
import zipfile

from .. import config, inputserver
from .base import Backend

REMOTE_VIDEO = "/sdcard/device-testing-run.mp4"
REMOTE_SEGMENT = "/sdcard/device-testing-run_%s.mp4"
REMOTE_PID = "/sdcard/device-testing-run.pid"
SEGMENT_SECONDS = 170
MAX_SEGMENTS = 12

# `input text` reads %s as a space and needs these escaped for the device shell.
_SHELL_SPECIAL = re.compile(r"([\\'\"`$&|;<>()*?!#~])")


class AndroidBackend(Backend):
    name = "android"

    @classmethod
    def discover(cls):
        if not shutil.which("adb"):
            return {}
        found = {}
        for line in cls.out(["adb", "devices", "-l"]).splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                found[parts[0]] = next((p.split(":", 1)[1] for p in parts if p.startswith("model:")), "")
        return found

    def adb(self, *args):
        return ["adb", "-s", self.serial, *args]

    def shell(self, *args):
        return self.out(self.adb("shell", *args)).replace("\r", "")

    def app_id(self):
        return config.require("APP_ANDROID_PACKAGE")

    # --- app -----------------------------------------------------------------------------------
    def installed(self):
        return self.ok(self.adb("shell", "pm", "path", self.app_id()))

    def _package_field(self, field):
        m = re.search(rf"{field}=(\S+(?: \S+)?)", self.shell("dumpsys", "package", self.app_id()))
        return m.group(1).replace(" ", "") if m else ""

    def version(self):
        return [f"versionName={self._package_field('versionName')}",
                f"lastUpdateTime={self._package_field('lastUpdateTime')}"]

    def last_update(self):
        return self._package_field("lastUpdateTime")

    def install(self, path):
        self.sh(self.adb("install", "-r", path), stdout=subprocess.DEVNULL)

    def launch(self, environment=None, url=None):
        package = self.app_id()
        if url:
            extras = [arg for key, value in (environment or {}).items() for arg in ("--es", key, str(value))]
            self.sh(self.adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url, "-p", package,
                             *extras), stdout=subprocess.DEVNULL)
            return
        if not environment:
            self.ok(self.adb("shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"))
            return
        activity = self.shell("cmd", "package", "resolve-activity", "--brief", package).strip().splitlines()[-1]
        extras = [arg for key, value in environment.items() for arg in ("--es", key, str(value))]
        self.sh(self.adb("shell", "am", "start", "-n", activity, *extras), stdout=subprocess.DEVNULL)

    def stop(self):
        self.ok(self.adb("shell", "am", "force-stop", self.app_id()))

    def pid(self):
        out = subprocess.run(self.adb("shell", "pidof", self.app_id()), capture_output=True, text=True).stdout
        found = re.findall(r"\d+", out)
        return int(found[0]) if found else None

    def focus(self):
        line = next((l.strip() for l in self.shell("dumpsys", "window").splitlines() if "mCurrentFocus" in l), "")
        return self.app_id() in line, line

    def background(self):
        self.key("KEYCODE_HOME")

    def build_symbol_file(self, build_path):
        inner = config.require("APP_ANDROID_SYMBOL_FILE")
        with zipfile.ZipFile(build_path) as z:
            return z.read(inner)

    # --- device --------------------------------------------------------------------------------
    def locked(self):
        return "deviceLocked=1" in self.shell("dumpsys", "trust")

    def keep_awake(self):
        self.ok(self.adb("shell", "settings", "put", "global", "stay_on_while_plugged_in", "7"))
        return "stay-awake on"

    def size(self):
        sizes = re.findall(r"(\d+)x(\d+)", self.shell("wm", "size"))
        return tuple(int(v) for v in sizes[-1])

    def density(self):
        found = re.findall(r":\s*(\d+)", self.shell("wm", "density"))
        return int(found[-1]) if found else 160

    def screenshot(self, out):
        # exec-out, not shell: shell corrupts the PNG with CRLF.
        with open(out, "wb") as f:
            subprocess.run(self.adb("exec-out", "screencap", "-p"), stdout=f, check=True)

    # --- input ---------------------------------------------------------------------------------
    def tap(self, x, y):
        self.sh(self.adb("shell", "input", "tap", str(int(x)), str(int(y))))

    def swipe(self, x1, y1, x2, y2, ms):
        self.sh(self.adb("shell", "input", "swipe", *(str(int(v)) for v in (x1, y1, x2, y2, ms))))

    def key(self, keycode):
        self.sh(self.adb("shell", "input", "keyevent", str(keycode)))

    def type_text(self, text):
        self.sh(self.adb("shell", "input", "text", _SHELL_SPECIAL.sub(r"\\\1", text).replace(" ", "%s")))

    # --- logs ----------------------------------------------------------------------------------
    def log_prepare(self, buffer):
        # Grow the ring buffer before anything streams, or app lines are evicted within seconds.
        self.ok(self.adb("logcat", "-G", buffer))

    def log_clear(self):
        self.ok(self.adb("logcat", "-c"))

    def clear_data(self, grants=()):
        app = self.app_id()
        self.ok(self.adb("shell", "am", "force-stop", app))
        if not self.ok(self.adb("shell", "pm", "clear", app)):
            raise RuntimeError(f"pm clear {app} failed")
        for permission in grants:
            self.ok(self.adb("shell", "pm", "grant", app, permission))

    def log_lines(self):
        return subprocess.run(self.adb("logcat", "-d"), capture_output=True, text=True,
                              errors="replace").stdout.splitlines()

    # --- capture and state ---------------------------------------------------------------------
    def record_start(self, name, directory, segmented=False):
        """segmented: back-to-back SEGMENT_SECONDS clips in a device-side loop, because screenrecord
        ends a clip at 3 minutes whatever --time-limit says. record_stop joins them."""
        if self.ok(self.adb("shell", "pgrep", "screenrecord")):
            raise RuntimeError("a screenrecord is already running")
        started = self.shell("date", "+%H:%M:%S.%3N").strip()
        with open(f"{directory}/{name}.start", "w") as f:
            f.write(started + "\n")
        if segmented:
            loop = (f"echo $$ > {REMOTE_PID}; i=0; while [ $i -lt {MAX_SEGMENTS} ]; do i=$((i+1)); "
                    f"screenrecord --bit-rate 8000000 --time-limit {SEGMENT_SECONDS} {REMOTE_SEGMENT % '$i'} || break; done")
            self.ok(self.adb("shell", f"rm -f {REMOTE_SEGMENT % '*'} {REMOTE_PID}; nohup sh -c '{loop}' >/dev/null 2>&1 &"))
        else:
            # nohup and the redirects matter: without them `adb shell '... &'` never returns.
            self.ok(self.adb("shell", f"rm -f {REMOTE_VIDEO}; nohup screenrecord --time-limit 0 "
                                      f"--bit-rate 8M {REMOTE_VIDEO} >/dev/null 2>&1 &"))
        time.sleep(1)
        if not self.ok(self.adb("shell", "pgrep", "screenrecord")):
            raise RuntimeError("screenrecord did not start")
        return f"recording {REMOTE_SEGMENT % 'N' if segmented else REMOTE_VIDEO} (started {started})"

    def record_stop(self, name, directory):
        # Kill the segment loop first, or it starts another clip as the live one ends.
        self.ok(self.adb("shell", f"kill $(cat {REMOTE_PID}) 2>/dev/null; rm -f {REMOTE_PID}"))
        self.ok(self.adb("shell", "pkill", "-INT", "screenrecord"))
        time.sleep(2)  # the encoder needs a beat to finalize the container
        out = f"{directory}/{name}.mp4"
        segments = sorted(self.shell(f"ls {REMOTE_SEGMENT % '*'} 2>/dev/null; true").split(),
                          key=lambda p: int(re.search(r"_(\d+)\.mp4$", p).group(1))
                          if re.search(r"_(\d+)\.mp4$", p) else 0)
        segments = [p for p in segments if p.endswith(".mp4")]
        if segments:
            return self._join_segments(segments, out)
        if not self.ok(self.adb("pull", REMOTE_VIDEO, out)):
            raise RuntimeError("nothing to pull")
        self.ok(self.adb("shell", "rm", "-f", REMOTE_VIDEO))
        return out

    def _join_segments(self, segments, out):
        parts = []
        for remote in segments:
            local = f"{out}.{os.path.basename(remote)}"
            if not self.ok(self.adb("pull", remote, local)):
                raise RuntimeError(f"could not pull {remote}")
            parts.append(local)
        if len(parts) == 1:
            os.replace(parts[0], out)
        else:
            listing = out + ".txt"
            with open(listing, "w") as f:
                f.write("".join(f"file '{p}'\n" for p in parts))
            self.sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", out])
            for p in parts + [listing]:
                os.unlink(p)
        self.ok(self.adb("shell", f"rm -f {REMOTE_SEGMENT % '*'}"))
        return f"{out} ({len(segments)} segment(s))"

    def prefs_text(self):
        path = config.require("APP_ANDROID_PREFS_FILE").format(package=self.app_id())
        result = subprocess.run(self.adb("shell", "run-as", self.app_id(), "cat", path),
                                capture_output=True, text=True)
        body = result.stdout + result.stderr
        if "not debuggable" in body or "unknown package" in body:
            raise RuntimeError(f"run-as refused: {body.strip()}")
        return body

    # --- input server and appearance -----------------------------------------------------------
    def forward(self, local_port, device_port):
        self.sh(self.adb("forward", f"tcp:{local_port}", f"tcp:{device_port}"))

    def system_style(self, set_to=None):
        if set_to:
            self.ok(self.adb("shell", "cmd", "uimode", "night", "yes" if set_to == "dark" else "no"))
        return "dark" if "yes" in self.shell("cmd", "uimode", "night") else "light"

    # --- quiet ---------------------------------------------------------------------------------
    # AOSP `cmd notification set_dnd [on|none|priority|alarms|all|off]` (Android 13-16), read back
    # as global zen_mode: 0 off, 1 priority, 2 none, 3 alarms. SystemUI demo mode hides the status
    # bar notification icons; it needs sysui_demo_allowed and ends on reboot.
    _ZEN_NAMES = {"0": "off", "1": "priority", "2": "none", "3": "alarms"}
    _DEMO = ["am", "broadcast", "-a", "com.android.systemui.demo", "-e", "command"]

    def _setting(self, key):
        value = self.shell("settings", "get", "global", key).strip()
        return None if value in ("", "null") else value

    def _put_setting(self, key, value):
        if value is None:
            self.ok(self.adb("shell", "settings", "delete", "global", key))
        else:
            self.ok(self.adb("shell", "settings", "put", "global", key, str(value)))

    def _quiet_saved(self):
        return inputserver.load_state().get("quiet", {}).get(self.serial)

    def _quiet_store(self, entry):
        state = inputserver.load_state()
        quiet = state.setdefault("quiet", {})
        if entry is None:
            quiet.pop(self.serial, None)
        else:
            quiet[self.serial] = entry
        inputserver.save_state(state)

    def quiet_on(self, confirmed=False):
        saved = self._quiet_saved()
        if not saved:
            # First time: keep what the person had, so `off` puts it back. Never overwritten by a
            # second `on`, which would save the quiet values as the originals.
            saved = {"zen_mode": self._setting("zen_mode") or "0",
                     "heads_up": self._setting("heads_up_notifications_enabled"),
                     "demo_allowed": self._setting("sysui_demo_allowed"), "demo": False}
            self._quiet_store(saved)
        self.sh(self.adb("shell", "cmd", "notification", "set_dnd", "priority"))
        self._put_setting("heads_up_notifications_enabled", 0)
        self._put_setting("sysui_demo_allowed", 1)
        self.ok(self.adb("shell", *self._DEMO, "enter"))
        self.ok(self.adb("shell", *self._DEMO, "notifications", "-e", "visible", "false"))
        saved["demo"] = True
        self._quiet_store(saved)
        return self.quiet_state()

    def quiet_off(self):
        saved = self._quiet_saved()
        if saved:
            self.ok(self.adb("shell", *self._DEMO, "exit"))
            self._put_setting("sysui_demo_allowed", saved.get("demo_allowed"))
            self._put_setting("heads_up_notifications_enabled", saved.get("heads_up"))
            self.ok(self.adb("shell", "cmd", "notification", "set_dnd", self._ZEN_NAMES.get(saved.get("zen_mode"), "off")))
            self._quiet_store(None)
        return self.quiet_state()

    def quiet_state(self):
        saved = self._quiet_saved() or {}
        zen = self._setting("zen_mode") or "0"
        heads_up = self._setting("heads_up_notifications_enabled")
        dnd, heads_off = zen != "0", heads_up == "0"
        icons_hidden = bool(saved.get("demo")) and self._setting("sysui_demo_allowed") == "1"
        quiet = dnd and heads_off and icons_hidden
        parts = [f"do not disturb {'on' if dnd else 'off'}", f"heads-up {'off' if heads_off else 'on'}",
                 f"notification icons {'hidden' if icons_hidden else 'shown'}"]
        if quiet:
            parts = ["do not disturb", "heads-up off", "notification icons hidden"]
        return {"quiet": quiet, "manual": False, "detail": ", ".join(parts), "dnd": dnd,
                "heads_up_off": heads_off, "icons_hidden": icons_hidden}

    def rotate(self, direction):
        # Auto-rotate off, then the user rotation a quarter turn on; named as iOS names them.
        # A portrait-locked app keeps its layout here, unlike iOS where the device itself turns.
        names = ["portrait", "landscapeLeft", "portraitUpsideDown", "landscapeRight"]
        current = int(self.shell("settings", "get", "system", "user_rotation").strip() or 0)
        turned = (current + (1 if direction == "left" else 3)) % 4
        self.ok(self.adb("shell", "settings", "put", "system", "accelerometer_rotation", "0"))
        self.ok(self.adb("shell", "settings", "put", "system", "user_rotation", str(turned)))
        return {"orientation": names[turned], "orientationLocked": True}

    def keyboard_native(self):
        ime =self.shell("settings", "get", "secure", "default_input_method").strip()
        shown = re.search(r"mInputShown=(\w+)", self.shell("dumpsys", "input_method"))
        return {"inputMode": ime, "inputShown": bool(shown) and shown.group(1) == "true"}

    # --- location: a mock GPS provider through `cmd location`, no helper app -----------------------
    def _provider(self, *args):
        self.out(self.adb("shell", "cmd", "location", "providers", *args))

    def location_set(self, lat, lng):
        # The shell uid may add a test provider once it holds the mock-location appop.
        self.ok(self.adb("shell", "appops", "set", "2000", "android:mock_location", "allow"))
        if not self.ok(self.adb("shell", "cmd", "location", "providers", "set-test-provider-enabled", "gps", "true")):
            self._provider("add-test-provider", "gps", "--supportsAltitude", "--supportsSpeed", "--supportsBearing")
            self._provider("set-test-provider-enabled", "gps", "true")
        self._provider("set-test-provider-location", "gps", "--location", f"{lat},{lng}", "--accuracy", "5")

    def location_route(self, points):
        start = time.time()
        for lat, lng, at in points:
            time.sleep(max(0, start + at - time.time()))
            self.location_set(lat, lng)

    def location_clear(self):
        self.ok(self.adb("shell", "cmd", "location", "providers", "remove-test-provider", "gps"))
        self.ok(self.adb("shell", "appops", "set", "2000", "android:mock_location", "default"))

    # --- performance ---------------------------------------------------------------------------
    def _frame_layer(self):
        """The app's SurfaceView layer, where a game engine's frames are posted."""
        names = re.findall(rf"[0-9a-f]+ SurfaceView\[{re.escape(self.app_id())}[^\]]*\]\(BLAST\)#\d+",
                           self.shell("dumpsys", "SurfaceFlinger", "--list"))
        return names[0] if names else None

    def _present_times(self, layer):
        out = subprocess.run(self.adb("shell", "dumpsys", "SurfaceFlinger", "--latency", f"'{layer}'"),
                             capture_output=True, text=True).stdout.splitlines()
        times = []
        for line in out[1:]:
            parts = line.split()
            if len(parts) == 3 and parts[1].isdigit() and 0 < int(parts[1]) < 2 ** 62:
                times.append(int(parts[1]))
        return times

    def perf_sample(self, seconds):
        pid = self.pid()
        pkg = self.app_id()
        layer = self._frame_layer()
        hz = 100  # kernel clock ticks per second on Android
        rows, frame_ms, seen = [], [], set()
        last_cpu = last_wall = None
        if layer:
            seen.update(self._present_times(layer))  # frames from before the window
        begin = last_tick = time.time()
        while time.time() - begin < seconds:
            tick = time.time()
            out = subprocess.run(self.adb("shell", f"cat /proc/{pid}/stat; dumpsys meminfo {pid} | grep -m1 'TOTAL'"),
                                 capture_output=True, text=True).stdout
            stat = re.search(r"\) \S (.*)", out.splitlines()[0]) if out else None
            cpu = None
            if stat:
                fields = stat.group(1).split()
                ticks = int(fields[11]) + int(fields[12])  # utime + stime
                if last_cpu is not None:
                    cpu = (ticks - last_cpu) / hz / (tick - last_wall) * 100
                last_cpu, last_wall = ticks, tick
            mem = re.search(r"TOTAL(?: PSS)?:?\s+(\d+)", out)
            fps = None
            if layer:
                times = sorted(t for t in self._present_times(layer) if t not in seen)
                seen.update(times)
                fps = len(times) / max(time.time() - last_tick, 0.1)
                last_tick = time.time()
                frame_ms += [(b - a) / 1e6 for a, b in zip(times, times[1:])]
            rows.append({"t": time.time() - begin, "fps": fps, "cpu": cpu,
                         "mem_mb": int(mem.group(1)) / 1024 if mem else None})
            time.sleep(max(0, 1 - (time.time() - tick)))
        notes = []
        if not layer:
            notes.append("no SurfaceView layer for the app; fps unavailable")
        elif not frame_ms:
            notes.append("the app posted no frames in the window (a still screen presents none)")
        return {"rows": rows, "frame_ms": frame_ms, "notes": notes}

    # --- crashes: the system's dropbox records for crashes, ANRs and native crashes ----------------
    DROPBOX_TAGS = ("data_app_crash", "data_app_anr", "data_app_native_crash")

    def _dropbox(self):
        entries = []
        header = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (\S+) \(")
        for tag in self.DROPBOX_TAGS:
            current = None
            for line in self.shell("dumpsys", "dropbox", "--print", tag).splitlines():
                m = header.match(line)
                if m:
                    current = {"time": datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), "kind": tag,
                               "name": tag, "text": [line], "ref": None}
                    entries.append(current)
                elif current is not None:
                    current["text"].append(line)
        return entries

    def crashes(self):
        pkg = self.app_id()
        found = []
        for e in self._dropbox():
            body = "\n".join(e["text"])
            if re.search(rf"Process: {re.escape(pkg)}\b|Package: {re.escape(pkg)}\b", body):
                e["name"] = next((l.strip() for l in e["text"] if l.startswith(("Subject:", "Cmdline:"))), e["kind"])
                found.append(e)
        return found

    def crash_pull(self, entries, directory):
        paths = []
        for e in entries:
            path = os.path.join(directory, f"{e['kind']}-{e['time']:%Y%m%d-%H%M%S}.txt")
            with open(path, "w") as f:
                f.write("\n".join(e["text"]) + "\n")
            paths.append(path)
        return paths
