#!/usr/bin/env python3
"""Cut system overlays (notification banners) out of a screen recording.

scrub_overlays.py VIDEO... [--in-place] [--dry-run] [--top 0.22] [--ocr auto|vision|tesseract]
                  [--extra-pattern REGEX ...] [--json]

Examples (SKILL = the skill folder):
  python3 SKILL/scripts/scrub_overlays.py take.mp4 --dry-run
  python3 SKILL/scripts/scrub_overlays.py take.mp4 --ocr tesseract
  python3 SKILL/scripts/scrub_overlays.py a.mp4 b.mp4 --in-place --extra-pattern 'just now'

A banner from another app (a payment, a doorbell, a message) leaks personal info into evidence and
covers the UI under test. Banners drop in at the top and carry a relative time ("now", "2m ago"),
so the top strip is read with an OCR backend: a 2 fps pass finds candidates, a 10 fps pass around
each one finds the span, and the span plus a slide-in/slide-out margin is cut. The output is
re-scanned and must come back clean, or it is not written.

OCR backends (--ocr, default auto):
  vision     macOS Vision text recognizer, compiled once with swiftc (needs Xcode command line tools).
             The compiled helper is cached in $VIDEO_TRIM_CACHE, else the per-user cache folder.
  tesseract  any OS; needs the `tesseract` program on PATH (macOS: brew install tesseract; Debian or
             Ubuntu: apt install tesseract-ocr; Windows: the UB Mannheim installer or
             choco install tesseract). Banner words are also matched at the end of a line.
  auto       vision on macOS when swiftc exists, else tesseract, else exit 2.

Banner words are the module constant STAMP_PATTERNS (a whole text line must match); add more with
--extra-pattern REGEX, repeatable. Matching ignores case.

Without --in-place it writes VIDEO_clean.mp4 next to the source; with --in-place it replaces the
source (the point when the source itself holds personal info).
Exit codes: 0 ok (clean or scrubbed), 1 a banner survived the cut or ffmpeg failed, 2 bad input or
no OCR backend.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
W = 642
# Whole-line patterns that mark a notification banner (relative times). Extend with --extra-pattern.
STAMP_PATTERNS = [
    r"now",
    r"\d+\s?(s|m|h|min|mins|hr|hrs)\s+ago",
]
BEFORE, AFTER = 0.3, 0.7   # slide-in before the first readable frame, slide-out after the last
TESSERACT_WORKERS = 4


class BadInput(Exception):
    pass


def cache_dir():
    env = os.environ.get("VIDEO_TRIM_CACHE")
    if env:
        return Path(env)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "video-trim"
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "video-trim"


def compile_stamp(extra):
    try:
        pats = STAMP_PATTERNS + list(extra)
        return re.compile("^(?:" + "|".join("(?:%s)" % p for p in pats) + ")$", re.I)
    except re.error as e:
        raise BadInput("bad pattern: %s" % e)


def pick_backend(choice):
    have_vision = sys.platform == "darwin" and shutil.which("swiftc") is not None
    have_tess = shutil.which("tesseract") is not None
    if choice == "vision":
        if not have_vision:
            raise BadInput("--ocr vision needs macOS with swiftc (install the Xcode command line tools)")
        return "vision"
    if choice == "tesseract":
        if not have_tess:
            raise BadInput("--ocr tesseract needs `tesseract` on PATH (macOS: brew install tesseract; "
                           "Debian/Ubuntu: apt install tesseract-ocr; Windows: choco install tesseract)")
        return "tesseract"
    if have_vision:
        return "vision"
    if have_tess:
        return "tesseract"
    raise BadInput("no OCR backend: use the macOS Vision backend (needs macOS and swiftc, i.e. Xcode "
                   "command line tools) or install tesseract (macOS: brew install tesseract; "
                   "Debian/Ubuntu: apt install tesseract-ocr; Windows: choco install tesseract)")


def build_vision():
    out = cache_dir() / "ocr_frames"
    src = HERE / "ocr_frames.swift"
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["swiftc", "-O", str(src), "-o", str(out)], check=True)
    return out


def probe(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", str(video)],
                         capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    s = j["streams"][0]
    return s["width"], s["height"], float(j["format"]["duration"])


def strip_filter(video, top, fps):
    w, h, _ = probe(video)
    sh = int(W * h * top / w) // 2 * 2
    return f"fps={fps},crop=iw:ih*{top}:0:0,scale={W}:{sh}", sh


def input_args(video, start, length):
    cmd = ["-ss", str(start)]
    if length:
        cmd += ["-t", str(length)]
    return cmd + ["-i", str(video)]


def ocr_vision(video, top, fps, start, length, state):
    """(seconds, text) for every recognized line of the top strip, via the Vision helper."""
    vf, sh = strip_filter(video, top, fps)
    cmd = ["ffmpeg", "-v", "error"] + input_args(video, start, length) + [
        "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    ff = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    out = subprocess.run([str(state["vision"]), str(W), str(sh), str(fps)], stdin=ff.stdout,
                         capture_output=True, text=True).stdout
    ff.stdout.close()
    ff.wait()
    lines = []
    for line in out.splitlines():
        t, _, text = line.partition("\t")
        lines.append((float(t), text))
    return lines


def ocr_tesseract(video, top, fps, start, length, state):
    """Same shape as ocr_vision, from tesseract run on PNG frames of the top strip."""
    vf, _ = strip_filter(video, top, fps)
    with tempfile.TemporaryDirectory(prefix="vt-ocr-") as tmp:
        cmd = ["ffmpeg", "-v", "error"] + input_args(video, start, length) + [
            "-vf", vf, "-start_number", "0", str(Path(tmp) / "f%06d.png")]
        subprocess.run(cmd, check=True)
        frames = sorted(Path(tmp).glob("f*.png"))
        env = dict(os.environ, OMP_THREAD_LIMIT="1")

        def one(item):
            i, frame = item
            res = subprocess.run(["tesseract", str(frame), "stdout", "--psm", "11"],
                                 capture_output=True, text=True, env=env)
            return [(i / fps, ln) for ln in res.stdout.splitlines() if ln.strip()]

        with ThreadPoolExecutor(TESSERACT_WORKERS) as pool:
            results = list(pool.map(one, enumerate(frames)))
    return [r for chunk in results for r in chunk]


BACKENDS = {"vision": ocr_vision, "tesseract": ocr_tesseract}


def is_stamp(text, stamp, backend):
    text = text.strip()
    if stamp.match(text):
        return True
    if backend != "tesseract":
        return False
    words = text.split()   # sparse OCR often joins the banner title and its time on one line
    return any(stamp.match(" ".join(words[-n:])) for n in (1, 2, 3) if len(words) >= n)


def stamp_times(video, top, fps, ctx, start=0.0, length=None):
    """Times (s) of frames whose top strip shows a banner time stamp."""
    hits = set()
    for t, text in BACKENDS[ctx["backend"]](video, top, fps, start, length, ctx):
        if is_stamp(text, ctx["stamp"], ctx["backend"]):
            hits.add(round(start + t, 2))
    return sorted(hits)


def spans(video, top, ctx):
    _, _, duration = probe(video)
    coarse = stamp_times(video, top, 2, ctx)
    fine = set()
    for t in coarse:
        a = max(0.0, t - 1.0)
        fine.update(stamp_times(video, top, 10, ctx, a, 2.5))
    merged = []
    for t in sorted(fine):
        a, b = max(0.0, t - BEFORE), min(duration, t + AFTER)
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged, duration


def has_audio(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                          "stream=index", "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout
    return bool(out.strip())


def cut(video, out, cuts):
    sel = "not(" + " + ".join(f"between(t,{a:.2f},{b:.2f})" for a, b in cuts) + ")"
    audio = ["-af", f"aselect='{sel}',asetpts=N/SR/TB"] if has_audio(video) else ["-an"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf",
                    f"select='{sel}',setpts=N/FRAME_RATE/TB", *audio, "-c:v", "libx264", "-crf", "20",
                    "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)], check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("videos", nargs="+", metavar="VIDEO")
    ap.add_argument("--in-place", action="store_true", help="replace the source instead of writing VIDEO_clean.mp4")
    ap.add_argument("--dry-run", action="store_true", help="report the spans, write nothing")
    ap.add_argument("--top", type=float, default=0.22, help="fraction of the frame height scanned (default 0.22)")
    ap.add_argument("--ocr", choices=["auto", "vision", "tesseract"], default="auto",
                    help="text recognizer (default auto)")
    ap.add_argument("--extra-pattern", action="append", default=[], metavar="REGEX",
                    help="extra whole-line banner pattern, repeatable")
    ap.add_argument("--json", action="store_true", help="print one JSON object instead of the per-video lines")
    args = ap.parse_args()
    try:
        return run(args)
    except BadInput as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as e:
        print(f"error: command failed: {' '.join(map(str, e.cmd))}", file=sys.stderr)
        return 1


def run(args):
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        raise BadInput("required tool(s) not on PATH: " + ", ".join(missing))
    if not 0 < args.top <= 1:
        raise BadInput("--top must be in (0, 1]")
    for v in args.videos:
        if not Path(v).is_file():
            raise BadInput(f"input not found: {v}")
    backend = pick_backend(args.ocr)
    ctx = {"backend": backend, "stamp": compile_stamp(args.extra_pattern)}
    if backend == "vision":
        ctx["vision"] = build_vision()
    print(f"ocr      {backend}", file=sys.stderr)
    bad = 0
    results = []

    def say(status, video, text, **extra):
        results.append({"status": status, "video": video, **extra})
        if not args.json:
            print(text)

    for video in args.videos:
        cuts, duration = spans(video, args.top, ctx)
        if not cuts:
            say("clean", video, f"clean    {video}")
            continue
        listing = ", ".join(f"{a:.1f}-{b:.1f}s" for a, b in cuts)
        say("overlay", video, f"overlay  {video}: {listing} of {duration:.1f}s",
            spans=[[round(a, 1), round(b, 1)] for a, b in cuts], duration=round(duration, 1))
        if args.dry_run:
            continue
        root, _ = os.path.splitext(video)
        out = root + "_clean.mp4"
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", dir=os.path.dirname(os.path.abspath(video)),
                                          delete=False).name
        cut(video, tmp, cuts)
        left = stamp_times(tmp, args.top, 10, ctx)
        if left:
            os.remove(tmp)
            say("fail", video, f"FAIL     {video}: banner still at {left[:5]} after the cut, nothing written")
            bad += 1
            continue
        target = video if args.in_place else out
        if args.in_place and not video.endswith(".mp4"):
            target = out
        os.replace(tmp, target)
        if args.in_place and target != video:
            os.remove(video)
        say("scrubbed", video, f"scrubbed {target} ({sum(b - a for a, b in cuts):.1f}s cut, re-scan clean)",
            out=str(target))
    if args.json:
        print(json.dumps({"ok": not bad, "results": results}))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
