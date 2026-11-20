#!/usr/bin/env python3
"""
ios-sim-harness: build a tiny UIKit harness app around plugin .mm files, then drive it on an iOS
simulator. No app, project or company specifics. See references/ios-simulator.md.

Output is plain text; there is no --json mode. Exit codes: 0 ok, 1 failed or bad arguments.
"""
import os
import sys
import re
import subprocess
import shutil
import signal
import time
import tempfile
import textwrap

PROG = "ios-sim-harness"
# The idb CLI is not on PATH -- add the default pip install location.
os.environ["PATH"] = os.path.expanduser("~/Library/Python/3.10/bin") + ":" + os.environ.get("PATH", "")

USAGE = """\
usage: ios-sim-harness [global opts] <command> [args]

global opts (or env): --name N (IOS_SIM_NAME, default "harness")   scratch + output dir name
                      --out DIR (IOS_SIM_OUT, default ~/device-testing-scratch/<name>)
                      --udid U (IOS_SIM_UDID)   --label L (subfolder per arm/run, default "default")

commands:
  build    --main main.mm [--plugin f.mm]... --bundle-id ID [--app-name N] [--plist file]
           [--framework F]... [--define X]... [--min-ios 17.0] [--no-arc]
           [--ab --plugin-b f.mm... --bundle-id-b ID2]   second app, different id and plugins
           prints the .app path(s); -DDEBUG=1 and clang++ are always used
  boot     [--device "iPhone 17"]   boot the named/first iPhone, hardware keyboard off
  install  <app> [--bundle-id ID]    uninstall then install
  launch   <bundle-id> [--env K=V]... [--no-wait]   console to <out>/<label>/logs/console.log
  terminate <bundle-id>
  appearance dark|light
  shot     <name>                    <out>/<label>/screenshots/<name>.png
  record   start [name] | stop       <out>/<label>/video/<name>.mov
  idb      tap X Y [--duration S] | swipe X1 Y1 X2 Y2 [--delta N] | rotate DIR | describe
           | text S | key K | button B | raw <idb args...>     starts idb_companion as needed
  run      <app> --bundle-id ID [--env K=V]... [--secs N] [--no-record]
           boot, install, record, launch, shots light/dark/light, stop
  shutdown                           shut the simulator down
"""


def usage_exit(code=0):
    print(USAGE, end="")
    sys.exit(code)


def log(msg):
    from datetime import datetime
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def die(msg):
    print(f"{PROG}: {msg}", file=sys.stderr)
    sys.exit(1)


def run_cmd(args, **kwargs):
    return subprocess.run(args, **kwargs)


def check_cmd(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    return result.stdout.strip()


# --------------------------------------------------------------------------- #
# State helpers
# --------------------------------------------------------------------------- #

_UDID = None
_STATE = None
_STAGE = None
_RUN = None
_OUT = None
_LABEL = None
_NAME = None


def resolve_udid():
    global _UDID
    if _UDID:
        return
    udid_file = os.path.join(_STATE, "udid")
    if os.path.isfile(udid_file):
        with open(udid_file) as f:
            _UDID = f.read().strip()
        return
    out = check_cmd(["xcrun", "simctl", "list", "devices", "booted"])
    m = re.search(r"[0-9A-F]{8}-[0-9A-F-]{27}", out)
    if not m:
        die("no booted simulator; run boot first")
    _UDID = m.group(0)


def rundirs():
    for sub in ("logs", "screenshots", "video"):
        os.makedirs(os.path.join(_RUN, sub), exist_ok=True)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_build(args):
    main = ""
    bundle = ""
    bundleb = ""
    app = "Harness"
    plist = ""
    min_ios = "17.0"
    arc = "-fobjc-arc"
    ab = False
    plugins = []
    pluginsb = []
    fws = ["Foundation", "UIKit"]
    defs = ["DEBUG=1"]

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--main":        main = args[i+1]; i += 2
        elif a == "--plugin":    plugins.append(args[i+1]); i += 2
        elif a == "--plugin-b":  pluginsb.append(args[i+1]); i += 2
        elif a == "--bundle-id": bundle = args[i+1]; i += 2
        elif a == "--bundle-id-b": bundleb = args[i+1]; i += 2
        elif a == "--app-name":  app = args[i+1]; i += 2
        elif a == "--plist":     plist = args[i+1]; i += 2
        elif a == "--framework": fws.append(args[i+1]); i += 2
        elif a == "--define":    defs.append(args[i+1]); i += 2
        elif a == "--min-ios":   min_ios = args[i+1]; i += 2
        elif a == "--no-arc":    arc = ""; i += 1
        elif a == "--ab":        ab = True; i += 1
        else:                    die(f"build: unknown arg {a}")

    if not (os.path.isfile(main) and bundle):
        die("build needs --main FILE and --bundle-id ID")
    if ab:
        if not bundleb or bundleb == bundle:
            die("--ab needs --bundle-id-b different from --bundle-id")

    _build_one(app, bundle, main, plist, min_ios, arc, plugins, fws, defs)
    if ab:
        if not pluginsb:
            pluginsb = list(plugins)
        _build_one(app + "B", bundleb, main, plist, min_ios, arc, pluginsb, fws, defs)


def _build_one(app, bundle, main, plist, min_ios, arc, plugins, fws, defs):
    dir_ = os.path.join(_OUT, "build", app + ".app")
    if os.path.exists(dir_):
        shutil.rmtree(dir_)
    os.makedirs(dir_)

    flags = []
    for f in fws:
        flags += ["-framework", f]
    for d in defs:
        flags.append(f"-D{d}")

    log(f"compiling {app} ({bundle}) from {main} {' '.join(plugins)}")
    # clang++ at link time or ___gxx_personality_v0 is undefined; -DDEBUG=1 keeps #ifdef DEBUG logs.
    cmd = ["xcrun", "--sdk", "iphonesimulator", "clang++",
           "-std=gnu++17"]
    if arc:
        cmd.append(arc)
    cmd += ["-arch", "arm64",
            f"-mios-simulator-version-min={min_ios}"]
    cmd += flags
    cmd.append(main)
    cmd += plugins
    cmd += ["-o", os.path.join(dir_, app)]
    result = run_cmd(cmd)
    if result.returncode != 0:
        die("compile failed")

    if plist:
        shutil.copy(plist, os.path.join(dir_, "Info.plist"))
        run_cmd(["/usr/libexec/PlistBuddy",
                 "-c", f"Set :CFBundleIdentifier {bundle}",
                 "-c", f"Set :CFBundleExecutable {app}",
                 os.path.join(dir_, "Info.plist")],
                stdout=subprocess.DEVNULL)
    else:
        plist_content = textwrap.dedent(f"""\
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
            <plist version="1.0"><dict>
            <key>CFBundleIdentifier</key><string>{bundle}</string>
            <key>CFBundleExecutable</key><string>{app}</string>
            <key>CFBundleName</key><string>{app}</string>
            <key>CFBundleDisplayName</key><string>{app}</string>
            <key>CFBundlePackageType</key><string>APPL</string>
            <key>CFBundleVersion</key><string>1</string>
            <key>CFBundleShortVersionString</key><string>1.0</string>
            <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
            <key>DTPlatformName</key><string>iphonesimulator</string>
            <key>MinimumOSVersion</key><string>{min_ios}</string>
            <key>UIRequiredDeviceCapabilities</key><array><string>arm64</string></array>
            <key>UILaunchScreen</key><dict/>
            <key>UISupportedInterfaceOrientations</key><array><string>UIInterfaceOrientationPortrait</string></array>
            <key>UIUserInterfaceStyle</key><string>Automatic</string>
            </dict></plist>
            """)
        with open(os.path.join(dir_, "Info.plist"), "w") as fh:
            fh.write(plist_content)

    run_cmd(["codesign", "-s", "-", "--force", dir_],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(dir_)


def cmd_boot(args):
    global _UDID
    device = ""
    i = 0
    while i < len(args):
        if args[i] == "--device":
            device = args[i+1]; i += 2
        else:
            die(f"boot: unknown arg {args[i]}")

    # no hardware keyboard or the software keyboard never appears
    run_cmd(["defaults", "write", "com.apple.iphonesimulator",
             "ConnectHardwareKeyboard", "-bool", "false"])

    if not _UDID:
        pat = device or "iPhone"
        booted = check_cmd(["xcrun", "simctl", "list", "devices", "booted"])
        m = re.search(r"(" + re.escape(pat) + r"[^\n]*)\s+\(([0-9A-F]{8}-[0-9A-F-]{27})\)", booted)
        if m:
            _UDID = m.group(2)
        else:
            available = check_cmd(["xcrun", "simctl", "list", "devices", "available"])
            m = re.search(r"(" + re.escape(pat) + r"[^\n]*)\s+\(([0-9A-F]{8}-[0-9A-F-]{27})\)", available)
            if m:
                _UDID = m.group(2)

    if not _UDID:
        die(f"no simulator matches {device or 'iPhone'}")

    devices_out = check_cmd(["xcrun", "simctl", "list", "devices"])
    if _UDID in devices_out and "Booted" in devices_out[devices_out.find(_UDID):devices_out.find(_UDID)+200]:
        log(f"{_UDID} already booted")
    else:
        result = run_cmd(["xcrun", "simctl", "boot", _UDID])
        if result.returncode != 0:
            die("boot failed")

    run_cmd(["xcrun", "simctl", "bootstatus", _UDID, "-b"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(os.path.join(_STATE, "udid"), "w") as f:
        f.write(_UDID)
    print(_UDID)


def cmd_install(args):
    if not args:
        die("install <app>")
    app = args[0]
    bundle = ""
    if len(args) >= 3 and args[1] == "--bundle-id":
        bundle = args[2]

    resolve_udid()
    if not bundle:
        bundle = check_cmd(["/usr/libexec/PlistBuddy", "-c",
                            "Print :CFBundleIdentifier",
                            os.path.join(app, "Info.plist")])
    run_cmd(["xcrun", "simctl", "uninstall", _UDID, bundle],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = run_cmd(["xcrun", "simctl", "install", _UDID, app])
    if result.returncode != 0:
        die("install failed")
    log(f"installed {bundle}")


def cmd_launch(args):
    if not args:
        die("launch <bundle-id>")
    bundle = args[0]
    args = args[1:]
    wait = True
    envs = []
    i = 0
    while i < len(args):
        if args[i] == "--env":
            envs.append(f"SIMCTL_CHILD_{args[i+1]}"); i += 2
        elif args[i] == "--no-wait":
            wait = False; i += 1
        else:
            die(f"launch: unknown arg {args[i]}")

    resolve_udid()
    rundirs()
    logf = os.path.join(_RUN, "logs", "console.log")

    env = os.environ.copy()
    for ev in envs:
        k, _, v = ev.partition("=")
        env[k] = v

    cmd = ["xcrun", "simctl", "launch", "--console-pty",
           "--terminate-running-process", _UDID, bundle]
    with open(logf, "w") as fh:
        proc = subprocess.Popen(cmd, stdout=fh, stderr=fh, env=env)
    with open(os.path.join(_STATE, "launch.pid"), "w") as f:
        f.write(str(proc.pid))
    if wait:
        time.sleep(2)
    log(f"launched {bundle}, console -> {logf}")


def cmd_terminate(args):
    if not args:
        die("terminate <bundle-id>")
    resolve_udid()
    run_cmd(["xcrun", "simctl", "terminate", _UDID, args[0]],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid_file = os.path.join(_STATE, "launch.pid")
    if os.path.isfile(pid_file):
        with open(pid_file) as f:
            pid = f.read().strip()
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (ProcessLookupError, ValueError):
            pass
        os.remove(pid_file)


def cmd_appearance(args):
    if not args:
        die("appearance dark|light")
    resolve_udid()
    run_cmd(["xcrun", "simctl", "ui", _UDID, "appearance", args[0]])
    time.sleep(1)


def cmd_shot(args):
    if not args:
        die("shot <name>")
    n = args[0]
    resolve_udid()
    rundirs()
    stage_png = os.path.join(_STAGE, f"{n}.png")
    out_png = os.path.join(_RUN, "screenshots", f"{n}.png")
    result = run_cmd(["xcrun", "simctl", "io", _UDID, "screenshot", stage_png],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        die("screenshot failed")
    shutil.copy(stage_png, out_png)
    os.remove(stage_png)
    print(out_png)


def cmd_record(args):
    if not args:
        die("record start|stop")
    resolve_udid()
    rundirs()
    verb = args[0]
    if verb == "start":
        n = args[1] if len(args) > 1 else _LABEL
        stage_mov = os.path.join(_STAGE, f"{n}.mov")
        proc = subprocess.Popen(
            ["xcrun", "simctl", "io", _UDID, "recordVideo", "--force", stage_mov],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(os.path.join(_STATE, "record.pid"), "w") as f:
            f.write(str(proc.pid))
        with open(os.path.join(_STATE, "record.name"), "w") as f:
            f.write(n)
        time.sleep(1)
        log(f"recording {n}")
    elif verb == "stop":
        pid_file = os.path.join(_STATE, "record.pid")
        name_file = os.path.join(_STATE, "record.name")
        if not os.path.isfile(pid_file):
            die("no recording")
        with open(pid_file) as f:
            pid = int(f.read().strip())
        with open(name_file) as f:
            n = f.read().strip()
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        # wait up to 15 s
        for _ in range(30):
            try:
                os.kill(pid, 0)
                time.sleep(0.5)
            except ProcessLookupError:
                break
        stage_mov = os.path.join(_STAGE, f"{n}.mov")
        out_mov = os.path.join(_RUN, "video", f"{n}.mov")
        shutil.copy(stage_mov, out_mov)
        os.remove(stage_mov)
        os.remove(pid_file)
        print(out_mov)
    else:
        die("record start|stop")


def _idb_up():
    resolve_udid()
    if not shutil.which("idb"):
        die("idb CLI not found (pip3 install fb-idb; it lands in ~/Library/Python/3.10/bin)")
    if not shutil.which("idb_companion"):
        die("idb_companion missing (brew install facebook/fb/idb-companion)")
    # check if already running
    result = subprocess.run(["pgrep", "-f", f"idb_companion --udid {_UDID}"],
                            capture_output=True)
    if result.returncode != 0:
        log_path = os.path.join(_STATE, "idb_companion.log")
        with open(log_path, "w") as fh:
            subprocess.Popen(["idb_companion", "--udid", _UDID],
                             stdout=fh, stderr=fh,
                             start_new_session=True)
        time.sleep(3)
    subprocess.run(["idb", "connect", "localhost", "10882"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_idb(args):
    if not args:
        die("idb <verb>")
    v = args[0]
    rest = args[1:]
    _idb_up()
    if v == "tap":
        run_cmd(["idb", "ui", "tap", "--udid", _UDID, "--api", "hid"] + rest)
    elif v == "describe":
        run_cmd(["idb", "ui", "describe-all", "--udid", _UDID] + rest)
    elif v in ("swipe", "rotate", "text", "key", "button", "pinch",
               "multi-tap", "key-sequence", "drag-and-drop"):
        run_cmd(["idb", "ui", v, "--udid", _UDID] + rest)
    elif v == "raw":
        run_cmd(["idb"] + rest + ["--udid", _UDID])
    else:
        die(f"idb: unknown verb {v}")


def cmd_run(args):
    if not args:
        die("run <app>")
    app = args[0]
    args = args[1:]
    bundle = ""
    secs = 6
    rec = True
    envs = []
    i = 0
    while i < len(args):
        if args[i] == "--bundle-id":   bundle = args[i+1]; i += 2
        elif args[i] == "--env":       envs += ["--env", args[i+1]]; i += 2
        elif args[i] == "--secs":      secs = int(args[i+1]); i += 2
        elif args[i] == "--no-record": rec = False; i += 1
        else:                          die(f"run: unknown arg {args[i]}")

    if not bundle:
        bundle = check_cmd(["/usr/libexec/PlistBuddy", "-c",
                            "Print :CFBundleIdentifier",
                            os.path.join(app, "Info.plist")])

    cmd_boot([])
    cmd_appearance(["light"])
    cmd_shot(["00-preinstall-light"])
    cmd_install([app, "--bundle-id", bundle])
    if rec:
        cmd_record(["start", _LABEL])
    cmd_launch([bundle] + envs)
    cmd_shot(["01-launched"])
    cmd_appearance(["dark"]);  cmd_shot(["02-dark"])
    cmd_appearance(["light"]); cmd_shot(["03-light"])
    time.sleep(secs)
    cmd_shot(["04-end"])
    if rec:
        cmd_record(["stop"])
    cmd_terminate([bundle])
    log(f"done: {_RUN}")


def cmd_shutdown(_args):
    resolve_udid()
    subprocess.run(["pkill", "-f", f"idb_companion --udid {_UDID}"],
                   stderr=subprocess.DEVNULL)
    run_cmd(["xcrun", "simctl", "shutdown", _UDID])
    if os.path.exists(_STAGE):
        shutil.rmtree(_STAGE)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

COMMANDS = {
    "build":      cmd_build,
    "boot":       cmd_boot,
    "install":    cmd_install,
    "launch":     cmd_launch,
    "terminate":  cmd_terminate,
    "appearance": cmd_appearance,
    "shot":       cmd_shot,
    "record":     cmd_record,
    "idb":        cmd_idb,
    "run":        cmd_run,
    "shutdown":   cmd_shutdown,
}


def main():
    global _NAME, _OUT, _UDID, _LABEL, _STATE, _STAGE, _RUN

    _NAME  = os.environ.get("IOS_SIM_NAME",  "harness")
    _OUT   = os.environ.get("IOS_SIM_OUT",   "")
    _UDID  = os.environ.get("IOS_SIM_UDID",  "")
    _LABEL = os.environ.get("IOS_SIM_LABEL", "default")

    argv = sys.argv[1:]
    while argv:
        if argv[0] == "--name":   _NAME = argv[1]; argv = argv[2:]
        elif argv[0] == "--out":  _OUT  = argv[1]; argv = argv[2:]
        elif argv[0] == "--udid": _UDID = argv[1]; argv = argv[2:]
        elif argv[0] == "--label": _LABEL = argv[1]; argv = argv[2:]
        elif argv[0] in ("-h", "--help", "help"):
            usage_exit(0)
        else:
            break

    if not argv:
        usage_exit(1)

    cmd = argv[0]
    rest = argv[1:]

    if not _OUT:
        _OUT = os.path.join(os.path.expanduser("~/device-testing-scratch"), _NAME)

    _RUN   = os.path.join(_OUT, _LABEL)
    # simctl io cannot write to a removable volume (TCC 513): stage on the main drive, then cp across.
    _STAGE = os.path.join(tempfile.gettempdir(), PROG, _NAME, _LABEL)
    _STATE = os.path.join(_OUT, ".state")
    os.makedirs(_STATE, exist_ok=True)
    os.makedirs(_STAGE, exist_ok=True)

    if cmd not in COMMANDS:
        usage_exit(1)

    COMMANDS[cmd](rest)


if __name__ == "__main__":
    main()
