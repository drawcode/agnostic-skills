"""Host-side input and video for iOS 27+, through CoreDevice's universal HID service.

Called by the iOS backend when its input route is `hid`. On iOS 26 the device refuses the service
("Remote control requires iOS 27.0 or later on this device", code 9021), so `auto` only picks this
route from iOS 27 on. All of it goes through the pymobiledevice3 CLI, like the rest of the backend.
"""
import glob
import os
import shutil
import signal
import subprocess
import sys
import time

HID = ("developer", "core-device", "universal-hid-service")
MAX = 65535
REFUSED = ("9021", "requires iOS 27")
HINT = ("iOS HID input needs iOS 27 or later (the device refused it, code 9021); "
        "set DEVICE_IOS_INPUT=server to use the app's input server instead")


def normalize(value, extent):
    """Screen pixels to the 0..65535 range the HID service takes, clamped."""
    return max(0, min(MAX, round(value / extent * MAX)))


def refused(text):
    return any(marker in (text or "") for marker in REFUSED)


def _run(backend, *args):
    res = subprocess.run(backend.pmd3(*HID, *args), capture_output=True, text=True, env=backend.env())
    print("+ " + " ".join(backend.pmd3(*HID, *args)), file=sys.stderr)
    text = (res.stderr or "") + (res.stdout or "")
    if refused(text):
        raise RuntimeError(HINT)
    if res.returncode != 0:
        raise RuntimeError(f"hid {args[0]} failed (exit {res.returncode}): {text.strip()[-300:]}")


def tap(backend, x, y):
    nx, ny = backend.to_native(x, y)
    _run(backend, "tap", str(normalize(nx, 1)), str(normalize(ny, 1)))


def swipe(backend, x1, y1, x2, y2, ms):
    """A drag (contact, streamed moves, release) over `ms`. The CLI's `swipe` is pointer motion
    with no contact, so it is not used."""
    ax, ay = backend.to_native(x1, y1)
    bx, by = backend.to_native(x2, y2)
    seconds = max(0.05, ms / 1000)
    steps = max(2, min(60, int(ms / 20)))
    _run(backend, "drag", str(normalize(ax, 1)), str(normalize(ay, 1)), str(normalize(bx, 1)),
         str(normalize(by, 1)), "--duration", f"{seconds:.3f}", "--steps", str(steps))


def type_text(backend, text):
    if not text.isascii() or not text.isprintable():
        raise RuntimeError("hid type takes printable ASCII only")
    _run(backend, "type", text)


# --- video: raw RTP/HEVC from the display stream, depacketized and encoded on stop ---------------
def _depacketizer(backend):
    """pymobiledevice3 ships misc/rtp_dump.py next to its package, not inside it."""
    python = os.path.join(os.path.dirname(backend.pmd3()[0]), "python")
    found = subprocess.run([python, "-c", "import misc.rtp_dump as m; print(m.__file__)"],
                           capture_output=True, text=True).stdout.strip()
    return python, found


def record_start(backend, name, directory):
    rtp = os.path.join(directory, f"{name}.rtp")
    log = open(os.path.join(directory, f"{name}.recorder.log"), "w")
    # The CLI stops after --duration, so ask for a long one and end it with SIGINT on stop.
    proc = subprocess.Popen(backend.pmd3("developer", "core-device", "display", "start-video-stream",
                                         rtp, "--duration", "7200"),
                            stdout=log, stderr=log, start_new_session=True, env=backend.env())
    backend.remember("recpid", proc.pid)
    started = time.strftime("%H:%M:%S")
    with open(os.path.join(directory, f"{name}.start"), "w") as f:
        f.write(started + "\n")
    time.sleep(4)
    if proc.poll() is not None:
        text = open(os.path.join(directory, f"{name}.recorder.log")).read()
        if refused(text):
            raise RuntimeError(HINT)
        raise RuntimeError(f"video stream exited -- see {directory}/{name}.recorder.log")
    return f"recording {rtp} (started {started}, HEVC video stream)"


def record_stop(backend, name, directory):
    pid = backend.state("recpid")
    if not pid:
        raise RuntimeError("no recording running")
    try:
        os.kill(pid, signal.SIGINT)
    except OSError:
        pass
    backend.remember("recpid", None)
    time.sleep(2)
    rtp = os.path.join(directory, f"{name}.rtp")
    h265 = os.path.join(directory, f"{name}.h265")
    python, script = _depacketizer(backend)
    if not script:
        raise RuntimeError("misc/rtp_dump.py not found in the pymobiledevice3 environment")
    backend.sh([python, script, rtp, h265])
    out = os.path.join(directory, f"{name}.mp4")
    backend.sh(["ffmpeg", "-y", "-loglevel", "error", "-f", "hevc", "-i", h265, "-vf",
                "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p", out])
    for path in (rtp, h265):
        os.remove(path)
    return out
