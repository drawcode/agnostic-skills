#!/usr/bin/env python3
"""Contact sheet of a video, to check a held screen survived a trim before handing it over.

Frames are taken at evenly spaced moments (--count, default 12), every N seconds (--every) or at
explicit times (--times) and tiled left to right, top to bottom. Each frame is the one on screen at
that time, so a held screen shows up even in a variable-frame-rate recording. No text is drawn
(many ffmpeg builds lack freetype); the timestamps used are printed in tile order instead.

Examples (SKILL = the skill folder):
  python3 SKILL/scripts/frame_strip.py clip-trimmed.mp4
  python3 SKILL/scripts/frame_strip.py clip.mp4 sheet.png --every 5 --cols 8
  python3 SKILL/scripts/frame_strip.py clip.mp4 --times 1.5,30,61.2 --tile-width 320 --json

Writes OUT (default <in>-strip.png) via a temporary folder next to it, removed afterwards. Never
touches the input; refuses to replace an existing output unless --force.
Exit codes: 0 ok, 1 ffmpeg or processing failed, 2 bad input.
"""
import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class BadInput(Exception):
    pass


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def need_tools():
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        raise BadInput("required tool(s) not on PATH: " + ", ".join(missing))


def probe_duration(path):
    res = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    try:
        return float(res.stdout.strip())
    except ValueError:
        raise BadInput("ffprobe could not read a duration from %s" % path)


def pick_times(args, duration):
    last = max(0.0, duration - 0.05)
    if args.times:
        try:
            times = [float(t) for t in args.times.split(",") if t.strip()]
        except ValueError:
            raise BadInput("bad --times %r (want comma separated seconds)" % args.times)
        if not times or any(t < 0 for t in times):
            raise BadInput("--times needs one or more seconds >= 0")
    elif args.every:
        if args.every <= 0:
            raise BadInput("--every must be > 0")
        times = [i * args.every for i in range(int(last / args.every) + 1)]
    else:
        n = args.count
        if n < 1:
            raise BadInput("--count must be >= 1")
        times = [(i + 0.5) * duration / n for i in range(n)]
    return [round(min(t, last), 3) for t in times]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="input video (never modified)")
    ap.add_argument("dst", nargs="?", help="output png (default <in>-strip.png)")
    pick = ap.add_mutually_exclusive_group()
    pick.add_argument("--every", type=float, metavar="SECONDS", help="one frame every SECONDS")
    pick.add_argument("--times", metavar="T1,T2,...", help="explicit times in seconds")
    pick.add_argument("--count", type=int, default=12, help="evenly spaced frames (default 12)")
    ap.add_argument("--cols", type=int, default=6, help="tiles per row (default 6)")
    ap.add_argument("--tile-width", type=int, default=240, help="width of each tile (default 240)")
    ap.add_argument("--force", action="store_true", help="replace an existing output")
    ap.add_argument("--json", action="store_true", help="print a JSON result instead of the result line")
    args = ap.parse_args()
    try:
        return run(args)
    except BadInput as e:
        log("error: %s" % e)
        return 2


def run(args):
    need_tools()
    src = Path(args.src)
    if not src.is_file():
        raise BadInput("input not found: %s" % src)
    dst = Path(args.dst) if args.dst else src.with_name(src.stem + "-strip.png")
    if dst.suffix.lower() != ".png":
        raise BadInput("output must end in .png")
    if dst.exists() and not args.force:
        raise BadInput("%s exists (use --force to replace)" % dst)
    if args.cols < 1 or args.tile_width < 2:
        raise BadInput("--cols >= 1 and --tile-width >= 2 required")
    times = pick_times(args, probe_duration(src))
    cols = min(args.cols, len(times))
    rows = math.ceil(len(times) / cols)
    dst.parent.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix=".strip-", dir=str(dst.parent)))
    try:
        for i, t in enumerate(times):
            log("frame %d/%d at %.3fs" % (i + 1, len(times), t))
            res = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", str(t), "-i", str(src),
                                  "-an", "-frames:v", "1", "-vf", "scale=%d:-2" % (args.tile_width // 2 * 2),
                                  str(tmp / ("f%04d.png" % i))], capture_output=True, text=True)
            if res.returncode != 0 or not (tmp / ("f%04d.png" % i)).is_file():
                log("error: no frame at %.3fs: %s" % (t, res.stderr.strip()))
                return 1
        sheet = tmp / "sheet.png"
        res = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-framerate", "1",
                              "-i", str(tmp / "f%04d.png"), "-vf", "tile=%dx%d:padding=2:color=black" % (cols, rows),
                              "-frames:v", "1", str(sheet)], capture_output=True, text=True)
        if res.returncode != 0 or not sheet.is_file():
            log("error: tiling failed: %s" % res.stderr.strip())
            return 1
        sheet.replace(dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    result = {"ok": True, "input": str(src), "output": str(dst), "cols": cols, "rows": rows,
              "times": times, "tile_width": args.tile_width}
    if args.json:
        print(json.dumps(result))
    else:
        print("%s (%dx%d tiles) times: %s" % (dst, cols, rows, ", ".join("%.2f" % t for t in times)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
