#!/usr/bin/env python3
"""Keep explicit source windows of a recording so static screens hold.

Freeze trimming (trim.py) cuts a held result screen down to its keep-in beat. When a screen has to
stay visible, name the windows to keep instead. The recording is converted to a constant frame rate
first (fps before trim), so a held screen that has no frames of its own in the source still gets some.

Window sources, all combined, clipped to [0, duration or --limit], sorted, and merged when they
overlap or sit within --merge-gap:
  --window S-E            explicit source seconds, repeatable
  --from-trimmap FILE     the kept segments of a trim.py .trimmap.txt
  --log FILE --match RE   one window per log line matching RE, from (moment - --before) to
                          (moment + --after); the moment is the line's wall-clock time minus the
                          recording's start time from the .start file (one line, either
                          "YYYY-MM-DD HH:MM:SS[.fff]" or "HH:MM:SS[.fff]")

Examples (SKILL = the skill folder):
  python3 SKILL/scripts/cut_windows.py take.mp4 --window 0-9.5 --window 31-45
  python3 SKILL/scripts/cut_windows.py take.mp4 out.mp4 --from-trimmap take-trimmed.trimmap.txt \\
      --log take.logcat --match 'screen bound' --start-file take.start --limit 228 --crop-bottom 0.055
  python3 SKILL/scripts/cut_windows.py take.mp4 --window 3-8 --json

Writes OUT (default <in>-cut.mp4: H.264, yuv420p, faststart, no audio) and <OUT>.segments.txt
(a header with the source and start time, then "src_start src_end" lines). Never touches the input,
and refuses to replace an existing output unless --force.
The frame is cropped to the app's own rectangle first (app_crop.py; --no-crop or --crop W:H:X:Y to
override) and the box is recorded in the segments file; before/after stills sit next to the output.
Exit codes: 0 ok, 1 ffmpeg or processing failed, 2 bad input, 3 crop detection ambiguous.
"""
import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app_crop  # noqa: E402

DEFAULT_LOG_TIME_REGEX = r"(\d\d-\d\d \d\d:\d\d:\d\d\.\d+)"
DEFAULT_LOG_TIME_FORMAT = "%m-%d %H:%M:%S.%f"
FLOAT_PAIR = re.compile(r"^\s*(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*$")


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


def parse_window(text):
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*", text)
    if not m or float(m.group(2)) <= float(m.group(1)):
        raise BadInput("bad --window %r (want S-E in seconds, E > S)" % text)
    return float(m.group(1)), float(m.group(2))


def read_trimmap(path):
    segs, wall = [], None
    for line in Path(path).read_text(errors="replace").splitlines():
        m = FLOAT_PAIR.match(line)
        if m:
            segs.append((float(m.group(1)), float(m.group(2))))
        elif "recording started" in line and ": " in line:
            wall = line.split(": ", 1)[1].strip()
    if not segs:
        raise BadInput("no 'src_start src_end' lines in %s" % path)
    return segs, wall


def parse_start(text):
    """Return (datetime or None, time-of-day seconds)."""
    text = text.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            d = dt.datetime.strptime(text, fmt)
            return d, d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6
        except ValueError:
            pass
    for fmt in ("%H:%M:%S.%f", "%H:%M:%S"):
        try:
            d = dt.datetime.strptime(text, fmt)
            return None, d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6
        except ValueError:
            pass
    raise BadInput("unreadable start time %r (want 'YYYY-MM-DD HH:MM:SS[.fff]' or 'HH:MM:SS[.fff]')" % text)


def log_offset(stamp, fmt, start_dt, start_tod):
    """Seconds from the recording start to a log timestamp string."""
    d = dt.datetime.strptime(stamp, fmt)
    has_date = ("%d" in fmt or "%j" in fmt) and ("%m" in fmt or "%b" in fmt or "%j" in fmt)
    has_year = "%Y" in fmt or "%y" in fmt
    if has_date and start_dt is not None:
        if not has_year:
            d = d.replace(year=start_dt.year)
        return (d - start_dt).total_seconds()
    tod = d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6
    diff = tod - start_tod
    return diff + 86400 if diff < -43200 else diff


def log_moments(args, start_dt, start_tod):
    try:
        match = re.compile(args.match)
        stamp_re = re.compile(args.log_time_regex)
    except re.error as e:
        raise BadInput("bad regex: %s" % e)
    moments = []
    for line in Path(args.log).read_text(errors="replace").splitlines():
        if not match.search(line):
            continue
        sm = stamp_re.search(line)
        if not sm:
            continue
        try:
            moments.append(log_offset(sm.group(1), args.log_time_format, start_dt, start_tod))
        except ValueError:
            continue
    return moments


def merge(windows, gap):
    out = []
    for a, b in sorted(windows):
        if out and a <= out[-1][1] + gap:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def build_graph(wins, fps, crop_top, crop_bottom, scale_width, app_crop_f=""):
    n = len(wins)
    graph = "[0:v]"
    if app_crop_f:
        graph += app_crop_f + ","
    graph += "fps=%s," % fps
    if n > 1:
        graph += "split=%d%s;" % (n, "".join("[s%d]" % i for i in range(n)))
    else:
        graph += "null[s0];"
    for i, (a, b) in enumerate(wins):
        graph += "[s%d]trim=start=%.3f:end=%.3f,setpts=PTS-STARTPTS[v%d];" % (i, a, b, i)
    graph += "".join("[v%d]" % i for i in range(n)) + "concat=n=%d:v=1:a=0" % n
    if crop_top or crop_bottom:
        keep = 1.0 - crop_top - crop_bottom
        graph += ",crop=iw:trunc(ih*%.5f/2)*2:0:trunc(ih*%.5f/2)*2" % (keep, crop_top)
    if scale_width:
        graph += ",scale=%d:-2" % scale_width
    return graph + ",format=yuv420p[out]"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="input video (never modified)")
    ap.add_argument("dst", nargs="?", help="output mp4 (default <in>-cut.mp4)")
    ap.add_argument("--window", action="append", default=[], metavar="S-E", help="source seconds to keep, repeatable")
    ap.add_argument("--from-trimmap", metavar="FILE", help="keep the segments of a trim.py .trimmap.txt")
    ap.add_argument("--log", metavar="FILE", help="log file holding evidence moments")
    ap.add_argument("--match", metavar="REGEX", help="log lines to take as evidence moments")
    ap.add_argument("--log-time-regex", default=DEFAULT_LOG_TIME_REGEX,
                    help="regex whose first group is the timestamp (default: logcat MM-DD HH:MM:SS.fff)")
    ap.add_argument("--log-time-format", default=DEFAULT_LOG_TIME_FORMAT,
                    help="strptime format of that group (default %(default)s)")
    ap.add_argument("--start-file", metavar="FILE", help="recording start wall clock (default <in>.start if present)")
    ap.add_argument("--before", type=float, default=0.5, help="seconds kept before a log moment (default 0.5)")
    ap.add_argument("--after", type=float, default=6.0, help="seconds kept after a log moment (default 6.0)")
    ap.add_argument("--limit", type=float, help="drop everything after this source second")
    ap.add_argument("--crop-bottom", type=float, default=0.0, metavar="FRACTION", help="crop this fraction off the bottom")
    ap.add_argument("--crop-top", type=float, default=0.0, metavar="FRACTION", help="crop this fraction off the top")
    ap.add_argument("--scale-width", type=int, default=540, help="output width, 0 = keep (default 540)")
    ap.add_argument("--fps", type=float, default=30, help="constant frame rate applied first (default 30)")
    ap.add_argument("--crf", type=int, default=28, help="x264 CRF (default 28)")
    ap.add_argument("--merge-gap", type=float, default=0.05, help="merge windows closer than this (default 0.05 s)")
    ap.add_argument("--force", action="store_true", help="replace an existing output")
    ap.add_argument("--json", action="store_true", help="print a JSON result instead of the result line")
    app_crop.add_flags(ap)
    args = ap.parse_args()
    try:
        return run(args)
    except (BadInput, app_crop.BadInput) as e:
        log("error: %s" % e)
        return 2
    except app_crop.Ambiguous as e:
        log("ambiguous crop: %s" % e)
        log("pass --crop W:H:X:Y to name the app box, or --no-crop to keep the full frame")
        return 3
    except app_crop.FfmpegFailed as e:
        log("error: %s" % e)
        return 1


def run(args):
    need_tools()
    src = Path(args.src)
    if not src.is_file():
        raise BadInput("input not found: %s" % src)
    dst = Path(args.dst) if args.dst else src.with_name(src.stem + "-cut.mp4")
    seg_path = dst.with_name(dst.name + ".segments.txt")
    if dst.resolve() == src.resolve():
        raise BadInput("output would overwrite the input")
    if not args.force:
        for p in (dst, seg_path):
            if p.exists():
                raise BadInput("%s exists (use --force to replace)" % p)
    if not (0 <= args.crop_top < 1 and 0 <= args.crop_bottom < 1 and args.crop_top + args.crop_bottom < 1):
        raise BadInput("crop fractions must be in [0,1) and sum below 1")
    if args.log and not args.match:
        raise BadInput("--log needs --match")
    if args.match and not args.log:
        raise BadInput("--match needs --log")
    if not (args.window or args.from_trimmap or args.log):
        raise BadInput("no windows: give --window, --from-trimmap or --log/--match")

    if args.crop and args.no_crop:
        raise BadInput("--crop and --no-crop together")
    crop = app_crop.resolve(str(src), args.crop, args.no_crop)
    log(app_crop.describe(crop))

    duration = probe_duration(src)
    top = min(duration, args.limit) if args.limit else duration
    wins, wall = [parse_window(w) for w in args.window], None
    if args.from_trimmap:
        if not Path(args.from_trimmap).is_file():
            raise BadInput("trimmap not found: %s" % args.from_trimmap)
        segs, wall = read_trimmap(args.from_trimmap)
        wins += segs
    n_moments = 0
    if args.log:
        if not Path(args.log).is_file():
            raise BadInput("log not found: %s" % args.log)
        sf = Path(args.start_file) if args.start_file else src.with_suffix(".start")
        if sf.is_file():
            start_text = sf.read_text().strip().splitlines()[0] if sf.read_text().strip() else ""
        elif args.start_file:
            raise BadInput("start file not found: %s" % sf)
        elif wall:
            start_text = wall
        else:
            raise BadInput("no start time: pass --start-file or have <in>.start next to the video")
        wall = start_text
        start_dt, start_tod = parse_start(start_text)
        moments = [m for m in log_moments(args, start_dt, start_tod) if 0 <= m < top]
        n_moments = len(moments)
        wins += [(m - args.before, m + args.after) for m in moments]
        log("log: %d evidence moment(s) inside the video" % n_moments)

    clipped = [(max(0.0, a), min(top, b)) for a, b in wins]
    kept = [w for w in merge([w for w in clipped if w[1] - w[0] > 0.05], args.merge_gap) if w[1] - w[0] > 0.05]
    if not kept:
        raise BadInput("no window left inside [0, %.1f]s" % top)
    total = sum(b - a for a, b in kept)
    log("keeping %d window(s), %.1fs of %.1fs" % (len(kept), total, duration))

    graph = build_graph(kept, args.fps, args.crop_top, args.crop_bottom, args.scale_width,
                        app_crop.filter_expr(crop))
    tmp = dst.with_name(dst.stem + ".partial.mp4")
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-stats", "-y", "-i", str(src), "-filter_complex", graph,
           "-map", "[out]", "-an", "-c:v", "libx264", "-crf", str(args.crf), "-preset", "medium",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp)]
    res = subprocess.run(cmd)
    if res.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        log("error: ffmpeg failed (exit %d)" % res.returncode)
        return 1
    tmp.replace(dst)
    header = "source %s%s; kept src_start src_end\n" % (
        src.name, (", recording started %s" % wall) if wall else "")
    header += app_crop.describe(crop) + "\n"
    stills = app_crop.write_stills(str(src), crop, str(dst.with_suffix("")))
    with open(str(seg_path), "w", encoding="utf-8", newline="\n") as f:
        f.write(header + "".join("%.3f %.3f\n" % w for w in kept))

    result = {"ok": True, "input": str(src), "output": str(dst), "segments_file": str(seg_path),
              "source_seconds": round(duration, 3), "kept_seconds": round(total, 3),
              "windows": [[round(a, 3), round(b, 3)] for a, b in kept], "log_moments": n_moments, "crop": crop,
              "crop_stills": stills,
              "size_bytes": dst.stat().st_size}
    if args.json:
        print(json.dumps(result))
    else:
        print("%s: %.1fs kept of %.1fs (%d windows), %.1f MB" % (dst, total, duration, len(kept),
                                                                  result["size_bytes"] / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
