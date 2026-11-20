#!/usr/bin/env python3
"""Join several per-feature screen recordings into ONE review video plus a chapter list.

usage: stitch.py <out.mp4> <segment> [<segment> ...] [--trim] [--crf N] [--force] [--json]

A segment is `Name=path` or just `path` (the name then comes from the file stem: dashes and
underscores become spaces, first letter capitalised). Each segment is normalised to the FIRST
segment's display size (letterboxed, aspect kept), 30 fps, square pixels, H.264 yuv420p, audio
dropped, then joined with the concat demuxer (no second encode). Writes:
  <out>.mp4                 the joined video
  <out minus .mp4>.chapters.txt   one `M:SS Name` line per segment, for pasting into review notes

--trim runs the sibling trim.py on each segment first (copies in a temp dir; sources are never
touched). Needs ffmpeg and ffprobe on PATH. --json prints one JSON object instead of the summary.
Exit codes: 0 ok, 1 errors found (a segment has no video stream), 2 bad usage (no segments, missing
or empty segment, <out> exists without --force or equals a segment), 3 failed (ffmpeg or ffprobe
missing or erroring).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class Refuse(Exception):
    def __init__(self, code, msg):
        Exception.__init__(self, msg)
        self.code = code


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def display_size(info):
    """Rotation-corrected (width, height) of the first video stream, or None."""
    for s in info.get("streams", []):
        if s.get("codec_type") != "video":
            continue
        w, h = int(s["width"]), int(s["height"])
        rot = (s.get("tags") or {}).get("rotate")
        for sd in s.get("side_data_list") or []:
            if "rotation" in sd:
                rot = sd["rotation"]
        try:
            if rot is not None and int(float(rot)) % 180 == 90:
                w, h = h, w
        except ValueError:
            pass
        return w, h
    return None


def default_name(path):
    name = Path(path).stem.replace("-", " ").replace("_", " ").strip()
    return name[:1].upper() + name[1:]


def parse_segment(text):
    head, sep, tail = text.partition("=")
    if sep and head and not os.path.isfile(text):
        return head.strip(), tail
    return default_name(text), text


def stamp(t):
    t = int(t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%d:%02d" % (m, s)


def check_inputs(a):
    out = Path(a.out)
    segs = []
    for text in a.segments:
        name, path = parse_segment(text)
        if not os.path.isfile(path):
            raise Refuse(2, "no such file: %s" % path)
        if os.path.getsize(path) == 0:
            raise Refuse(2, "zero-byte segment: %s" % path)
        if out.exists() and os.path.samefile(path, a.out):
            raise Refuse(2, "output is also a segment: %s" % path)
        segs.append((name, path))
    if os.path.abspath(a.out) in [os.path.abspath(p) for _, p in segs]:
        raise Refuse(2, "output is also a segment")
    if out.exists() and not a.force:
        raise Refuse(2, "%s exists (use --force to replace it)" % a.out)
    if not segs:
        raise Refuse(2, "need at least one segment")
    return segs


def run(a, segs, tmp):
    here = Path(__file__).resolve().parent
    sources = [p for _, p in segs]
    for _, p in segs:
        try:
            size = display_size(probe(p))
        except subprocess.CalledProcessError:
            size = None  # ffprobe cannot read it at all: not a video
        if size is None:
            raise Refuse(1, "no video stream in %s" % p)
    if a.trim:
        trimmed = []
        for i, p in enumerate(sources):
            dst = os.path.join(tmp, "trim%d.mp4" % i)
            r = subprocess.run([sys.executable, str(here / "trim.py"), p, dst],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise Refuse(3, "trim.py failed on %s: %s" % (p, r.stderr.strip()))
            trimmed.append(dst)
        sources = trimmed

    w, h = display_size(probe(sources[0]))
    w -= w % 2
    h -= h % 2
    vf = ("scale=%d:%d:force_original_aspect_ratio=decrease:force_divisible_by=2,"
          "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30,format=yuv420p" % (w, h, w, h))
    norm, durations = [], []
    for i, p in enumerate(sources):
        dst = os.path.join(tmp, "norm%d.mp4" % i)
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", p, "-an", "-vf", vf,
             "-c:v", "libx264", "-crf", str(a.crf), "-preset", "veryfast", "-pix_fmt", "yuv420p", dst],
            check=True)
        norm.append(dst)
        durations.append(float(probe(dst)["format"]["duration"]))

    listing = os.path.join(tmp, "list.txt")
    with open(listing, "w", encoding="utf-8", newline="\n") as f:
        for p in norm:
            f.write("file '%s'\n" % p.replace("'", "'\\''"))
    out = Path(a.out)
    part = os.path.join(str(out.resolve().parent), ".%s.part.mp4" % out.name)
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing,
             "-c", "copy", "-movflags", "+faststart", part], check=True)
        os.replace(part, a.out)
    finally:
        if os.path.exists(part):
            os.remove(part)

    chapters, cursor, rows = [], 0.0, []
    for (name, src), d in zip(segs, durations):
        chapters.append("%s %s" % (stamp(cursor), name))
        rows.append({"name": name, "source": src, "start_seconds": round(cursor, 2),
                     "duration_seconds": round(d, 2)})
        cursor += d
    ch_path = str(out.with_suffix("")) + ".chapters.txt"
    with open(ch_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(chapters) + "\n")
    size = os.path.getsize(a.out)
    total = float(probe(a.out)["format"]["duration"])
    if a.json:
        print(json.dumps({"ok": True, "out": a.out, "size_bytes": size,
                          "total_duration_seconds": round(total, 2), "segments": rows,
                          "chapters_file": ch_path}))
    else:
        print("%s: %d segments, %s, %.1f MB, chapters in %s"
              % (a.out, len(segs), stamp(total), size / 1e6, ch_path))
        for line in chapters:
            print("  " + line)
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Join per-feature recordings into one review video plus a chapter list. "
                    "Segment is Name=path or path. Exit codes: 0 ok, 1 errors found, 2 bad usage, 3 failed.")
    ap.add_argument("out", help="output .mp4")
    ap.add_argument("segments", nargs="*", help="Name=path or path, in play order")
    ap.add_argument("--trim", action="store_true", help="run trim.py on each segment first")
    ap.add_argument("--crf", type=int, default=26, help="H.264 CRF (default 26)")
    ap.add_argument("--force", action="store_true", help="replace an existing output")
    ap.add_argument("--json", action="store_true", help="print one JSON object instead of the summary")
    a = ap.parse_args()
    try:
        segs = check_inputs(a)
        missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
        if missing:
            raise Refuse(3, "%s not found on PATH (install ffmpeg, e.g. `brew install ffmpeg`)" % " and ".join(missing))
        with tempfile.TemporaryDirectory() as tmp:
            return run(a, segs, tmp)
    except Refuse as e:
        print("stitch.py: %s" % e, file=sys.stderr)
        return e.code
    except subprocess.CalledProcessError as e:
        print("stitch.py: %s failed (exit %s)" % (os.path.basename(str(e.cmd[0])), e.returncode), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
