#!/usr/bin/env python3
"""Cut the static stretches out of a device capture.

usage: trim.py <in.mp4> [out.mp4] [--min 1.5] [--keep-in 0.6] [--keep-out 0.4]
               [--crop W:H:X:Y | --no-crop]

Any freeze longer than --min keeps its first --keep-in and last --keep-out seconds and loses the
middle, so the viewer still sees each screen settle. The frame is also cropped to the app's own
rectangle (app_crop.py) in the same encode, so a desktop around a window never reaches a viewer;
--no-crop keeps the full frame, --crop W:H:X:Y names the box. Writes:
  <out>.mp4          CRF 30 H.264 with a subtitle track showing source time, so a trimmed moment can
                     still be matched to logcat
  <out>.trimmap.txt  the crop box, then kept segments as source start/end seconds, plus the recording's wall-clock start
                     when <in>.start exists (written by `device record start`)
  <out>.crop-before.png / .crop-after.png  the cut, to check, when a crop was applied

Needs ffmpeg and ffprobe on PATH. --json prints one JSON object instead of the summary line.
Exit codes: 0 ok, 1 failed (ffmpeg or ffprobe missing or erroring), 2 bad input (no such file, bad
arguments), 3 (crop detection ambiguous: pass --crop or --no-crop).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app_crop  # noqa: E402


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def detect_freezes(path, min_dur):
    # scale down first: detection only needs coarse change and this is several times faster
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-an",
         "-vf", "scale=270:-2,freezedetect=n=-50dB:d=%s" % min_dur, "-f", "null", "-"],
        capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"freeze_start: ([0-9.]+)", res.stderr)]
    ends = [float(x) for x in re.findall(r"freeze_end: ([0-9.]+)", res.stderr)]
    return starts, ends


def fmt(t):
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return "%02d:%02d:%06.3f" % (h, m, s)


def main():
    ap = argparse.ArgumentParser(
        description="Cut the static stretches out of a recording. Exit codes: 0 ok, 1 failed, 2 bad input.")
    ap.add_argument("src")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--min", type=float, default=1.5)
    ap.add_argument("--keep-in", type=float, default=0.6)
    ap.add_argument("--keep-out", type=float, default=0.4)
    ap.add_argument("--json", action="store_true", help="print one JSON object instead of the summary line")
    app_crop.add_flags(ap)
    a = ap.parse_args()
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        print("trim.py: %s not found on PATH (install ffmpeg, e.g. `brew install ffmpeg`)" % " and ".join(missing),
              file=sys.stderr)
        return 1
    if not os.path.isfile(a.src):
        print("trim.py: no such file: %s" % a.src, file=sys.stderr)
        return 2
    if a.crop and a.no_crop:
        print("trim.py: --crop and --no-crop together", file=sys.stderr)
        return 2
    dst = a.dst or re.sub(r"(-small)?\.mp4$", "", a.src) + "-trimmed.mp4"

    try:
        crop = app_crop.resolve(a.src, a.crop, a.no_crop)
        return run(a, dst, crop)
    except app_crop.BadInput as e:
        print("trim.py: %s" % e, file=sys.stderr)
        return 2
    except app_crop.Ambiguous as e:
        print("trim.py: ambiguous crop: %s\npass --crop W:H:X:Y to name the app box, or --no-crop to keep "
              "the full frame" % e, file=sys.stderr)
        return 3
    except app_crop.FfmpegFailed as e:
        print("trim.py: %s" % e, file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print("trim.py: %s failed (exit %s)" % (os.path.basename(str(e.cmd[0])), e.returncode), file=sys.stderr)
        return 1


def run(a, dst, crop):
    crop_f = app_crop.filter_expr(crop)
    print(app_crop.describe(crop), file=sys.stderr)
    total = probe_duration(a.src)
    starts, ends = detect_freezes(a.src, a.min)
    if len(ends) < len(starts):
        ends.append(total)  # still frozen at the end of the file

    keeps, cursor = [], 0.0
    for fs, fe in zip(starts, ends):
        cut_from, cut_to = fs + a.keep_in, fe - a.keep_out
        if cut_to - cut_from < 0.5:
            continue
        keeps.append((cursor, cut_from))
        cursor = cut_to
    keeps.append((cursor, total))
    keeps = [(s, e) for s, e in keeps if e - s > 0.05]

    parts, labels = [], []
    for i, (s, e) in enumerate(keeps):
        parts.append("[0:v]%strim=start=%.3f:end=%.3f,setpts=PTS-STARTPTS[v%d]"
                     % (crop_f + "," if crop_f else "", s, e, i))
        labels.append("[v%d]" % i)
    graph = ";".join(parts) + ";" + "".join(labels) + "concat=n=%d:v=1:a=0[out]" % len(keeps)

    wall = None
    start_file = re.sub(r"(-small)?\.mp4$", "", a.src) + ".start"
    if os.path.exists(start_file):
        with open(start_file, encoding="utf-8") as f:
            wall = f.read().strip()

    srt, t_out = [], 0.0
    for i, (s, e) in enumerate(keeps, 1):
        d = e - s
        label = "src %s" % fmt(s)[3:8]
        if wall:
            label += "  (rec started %s)" % wall
        srt.append("%d\n%s --> %s\n%s\n" % (i, fmt(t_out).replace(".", ","),
                                            fmt(t_out + d).replace(".", ","), label))
        t_out += d
    srt_path = dst[:-4] + ".srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt))

    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", a.src, "-i", srt_path,
         "-filter_complex", graph, "-map", "[out]", "-map", "1:s",
         "-c:v", "libx264", "-crf", "30", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-c:s", "mov_text", "-metadata:s:s:0", "language=eng", dst],
        check=True)

    with open(dst[:-4] + ".trimmap.txt", "w", encoding="utf-8") as f:
        if wall:
            f.write("recording started (device clock): %s\n" % wall)
        f.write("source %.1fs -> trimmed %.1fs, %d segments kept (src_start src_end)\n"
                % (total, t_out, len(keeps)))
        f.write(app_crop.describe(crop) + "\n")
        for s, e in keeps:
            f.write("%.3f %.3f\n" % (s, e))
    os.remove(srt_path)
    app_crop.write_stills(a.src, crop, dst[:-4])
    size = os.path.getsize(dst) / 1e6
    if a.json:
        print(json.dumps({"ok": True, "out": dst, "source_seconds": round(total, 1),
                          "trimmed_seconds": round(t_out, 1), "segments": len(keeps), "megabytes": round(size, 1)}))
    else:
        print("%s: %.1fs -> %.1fs (%d segments, %d freezes cut), %.1f MB"
              % (dst, total, t_out, len(keeps), len(keeps) - 1, size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
