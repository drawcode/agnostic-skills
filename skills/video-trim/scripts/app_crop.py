#!/usr/bin/env python3
"""Find the app's own rectangle in a recording, so the desktop around it can be cropped away.

A capture of a desktop, an emulator or an engine's game view inside a larger screen shows more than
the app: a menu bar, a dock, other windows, notifications, file names, chat. Viewers should not see
that unless it is the evidence. This module detects the app rectangle and is imported by trim.py and
cut_windows.py (crop-to-app is on by default there); it also runs on its own.

usage:
  app_crop.py IN.mp4                        detect, print the verdict, write a before/after still
  app_crop.py IN.mp4 --crop W:H:X:Y         use this box (checked against the frame)
  app_crop.py IN.mp4 --no-crop              keep the full frame (verdict "full-frame", forced)
  app_crop.py IN.mp4 --json --still-prefix OUT/name

How it detects (frames sampled evenly across the whole video, decoded small):
  border   ffmpeg cropdetect (black bars) intersected with a uniform-border scan: outer rows and
           columns that hold one flat color in every sampled frame are not the app.
  motion   the box around the pixels that change between samples (a static desktop never does).
  A crop is only taken when the evidence agrees: a flat border box, or a motion box whose edges are
  real window edges (a contrast step between inside and outside on every cropped side). A video that
  already fills its frame (a phone screenrecord) is "app-only" and is left alone.

Verdicts (field "mode"): app-only | crop | forced-crop | full-frame (forced by --no-crop).
Setting: VIDEO_TRIM_CROP=off (environment, or in ~/.config/video-trim/config.env as KEY=VALUE) keeps
the full frame unless --crop is given; the default is auto (detect). --no-crop overrides per run.
Exit codes: 0 ok, 1 ffmpeg failed, 2 bad input, 3 ambiguous -- detection would have to guess
(two windows, a lone small animation, a box with no edge evidence); pass --crop W:H:X:Y or --no-crop.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

SAMPLES = 16              # frames sampled across the video
DETECT_WIDTH = 1280       # detection decode width (never upscaled)
MOTION_LEVEL = 24         # per-pixel range across samples that counts as "changed" (0-255)
LINE_HITS = 0.012         # share of a row/column that must change to count it as moving
FLAT_SPREAD = 10          # a row/column whose max-min is below this is flat
EDGE_STEP = 10            # inside/outside contrast that makes a window edge (0-255)
EDGE_SHARE = 0.7          # share of the edge that must show the step
GAP_FRACTION = 0.03       # a side within this share of the frame counts as touching it
BIG_BOX = 0.60            # a motion box this large with no edge evidence is just the app
MIN_BOX = 0.10            # a lone box smaller than this is not trusted
SPLIT_GAP = 0.08          # motion split by a gap this wide is two regions (two windows)


class BadInput(Exception):
    pass


class Ambiguous(Exception):
    pass


class FfmpegFailed(Exception):
    pass


def config_value(key, default=""):
    """Environment first, then KEY=VALUE lines in ~/.config/video-trim/config.env."""
    if os.environ.get(key):
        return os.environ[key]
    path = Path.home() / ".config" / "video-trim" / "config.env"
    try:
        with open(str(path), encoding="utf-8", newline="\n") as f:
            for line in f:
                k, sep, v = line.strip().partition("=")
                if sep and k.strip() == key:
                    return v.strip().strip("\"'")
    except OSError:
        pass
    return default


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def need_tools():
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        raise BadInput("required tool(s) not on PATH: " + ", ".join(missing))


def probe(path):
    res = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True)
    try:
        info = json.loads(res.stdout)
        w, h = info["streams"][0]["width"], info["streams"][0]["height"]
        dur = float(info["format"]["duration"])
    except (ValueError, KeyError, IndexError):
        raise BadInput("ffprobe could not read the video stream of %s" % path)
    return w, h, dur


def parse_box(text, fw, fh):
    m = re.fullmatch(r"\s*(\d+):(\d+):(\d+):(\d+)\s*", text or "")
    if not m:
        raise BadInput("bad --crop %r (want W:H:X:Y in pixels)" % text)
    w, h, x, y = (int(g) for g in m.groups())
    if w < 16 or h < 16 or x + w > fw or y + h > fh:
        raise BadInput("--crop %s does not fit the %dx%d frame" % (text, fw, fh))
    return even_box(x, y, w, h)


def even_box(x, y, w, h):
    """Snap to even sizes and origin (yuv420p needs them). Origin floors, size shrinks."""
    x, y = x - x % 2, y - y % 2
    w, h = w - w % 2, h - h % 2
    return x, y, w, h


def grab_gray(path, t, w, h):
    """One frame at time t as w*h gray bytes."""
    res = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-ss", "%.3f" % t, "-i", str(path),
                          "-frames:v", "1", "-vf", "scale=%d:%d:flags=area,format=gray" % (w, h),
                          "-f", "rawvideo", "-"], capture_output=True)
    if len(res.stdout) != w * h:
        return None
    return res.stdout


def sample_times(duration):
    n = SAMPLES
    return [duration * (i + 0.5) / n for i in range(n)]


def cropdetect_box(path, fw, fh):
    """Black-bar box from ffmpeg cropdetect over 1 fps, as the most common reading, or None."""
    res = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf",
                          "fps=1,cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"],
                         capture_output=True, text=True)
    reads = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", res.stderr)
    if not reads:
        return None
    w, h, x, y = (int(v) for v in Counter(reads).most_common(1)[0][0])
    return (x, y, w, h)


def line_spreads(frame, w, h):
    """Per-row and per-column max-min of one gray frame."""
    rows = [max(frame[r * w:(r + 1) * w]) - min(frame[r * w:(r + 1) * w]) for r in range(h)]
    cols = [max(frame[c::w]) - min(frame[c::w]) for c in range(w)]
    return rows, cols


def span(flags):
    idx = [i for i, f in enumerate(flags) if f]
    return (idx[0], idx[-1] + 1) if idx else None


def runs(flags, min_gap):
    """Runs of true flags, joined across gaps shorter than min_gap."""
    out = []
    for i, f in enumerate(flags):
        if not f:
            continue
        if out and i - out[-1][1] <= min_gap:
            out[-1][1] = i + 1
        else:
            out.append([i, i + 1])
    return out


def edge_score(frame, w, h, side, pos, a, b):
    """Share of the edge (side at pos, spanning a..b along it) showing an inside/outside step."""
    hits = total = 0
    for k in range(a, b):
        if side in ("left", "right"):
            d = 1 if side == "left" else -1
            ins, out = pos + (0 if side == "left" else -1), pos + (-1 if side == "left" else 0)
            i2, o2 = ins + d, out - d
            if min(i2, ins, out, o2) < 0 or max(i2, ins, out, o2) >= w:
                continue
            inside = (frame[k * w + ins] + frame[k * w + i2]) / 2.0
            outside = (frame[k * w + out] + frame[k * w + o2]) / 2.0
        else:
            d = 1 if side == "top" else -1
            ins, out = pos + (0 if side == "top" else -1), pos + (-1 if side == "top" else 0)
            i2, o2 = ins + d, out - d
            if min(i2, ins, out, o2) < 0 or max(i2, ins, out, o2) >= h:
                continue
            inside = (frame[ins * w + k] + frame[i2 * w + k]) / 2.0
            outside = (frame[out * w + k] + frame[o2 * w + k]) / 2.0
        total += 1
        if abs(inside - outside) >= EDGE_STEP:
            hits += 1
    return hits / total if total else 0.0


def best_edge(frame, w, h, side, pos, reach, box):
    """Position within reach of pos (outward first on ties) with the best edge score."""
    x0, y0, x1, y1 = box
    a, b = (y0, y1) if side in ("left", "right") else (x0, x1)
    best = (edge_score(frame, w, h, side, pos, a, b), 0, pos)
    for off in range(-reach, reach + 1):
        p = pos + off
        if off == 0 or p < 0 or p > (w if side in ("left", "right") else h):
            continue
        s = edge_score(frame, w, h, side, p, a, b)
        key = (s, -abs(off))
        if key > (best[0], -abs(best[1])):
            best = (s, off, p)
    return best[0], best[2]


def analyse(frames, w, h):
    """Return dict with border box, motion box (small-frame coords, x0,y0,x1,y1) and facts."""
    # uniform-border scan: a line is content when it is not flat in at least one sample
    row_c, col_c = [False] * h, [False] * w
    for f in frames:
        rows, cols = line_spreads(f, w, h)
        row_c = [a or s > FLAT_SPREAD for a, s in zip(row_c, rows)]
        col_c = [a or s > FLAT_SPREAD for a, s in zip(col_c, cols)]
    ry, cx = span(row_c), span(col_c)
    border = (cx[0], ry[0], cx[1], ry[1]) if ry and cx else None
    # motion: per-pixel range over the samples
    mn, mx = frames[0], frames[0]
    for f in frames[1:]:
        mn, mx = bytes(map(min, mn, f)), bytes(map(max, mx, f))
    table = bytes(1 if i >= MOTION_LEVEL else 0 for i in range(256))
    mask = bytes(table[a - b] for a, b in zip(mx, mn))
    row_hits = [sum(mask[r * w:(r + 1) * w]) >= LINE_HITS * w for r in range(h)]
    col_hits = [sum(mask[c::w]) >= LINE_HITS * h for c in range(w)]
    ry_runs, cx_runs = runs(row_hits, int(SPLIT_GAP * h)), runs(col_hits, int(SPLIT_GAP * w))
    motion = None
    if ry_runs and cx_runs:
        motion = (cx_runs[0][0], ry_runs[0][0], cx_runs[-1][1], ry_runs[-1][1])
    return {"border": border, "motion": motion, "row_runs": ry_runs, "col_runs": cx_runs,
            "moving_pixels": sum(mask)}


def area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def intersect(a, b):
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def decide(frames, w, h, black):
    """Verdict on small-frame coordinates: (mode, box x0,y0,x1,y1, reason, facts)."""
    full = (0, 0, w, h)
    facts = analyse(frames, w, h)
    tol_x, tol_y = max(2, int(GAP_FRACTION * w)), max(2, int(GAP_FRACTION * h))
    box = facts["border"] or full
    if black:
        box = intersect(box, black)
    border_cut = area(box) < 0.98 * area(full)
    motion = facts["motion"]
    mid = frames[len(frames) // 2]

    def gap_sides(b):
        return [s for s, g, t in (("left", b[0], tol_x), ("top", b[1], tol_y),
                                  ("right", w - b[2], tol_x), ("bottom", h - b[3], tol_y)) if g > t]

    if border_cut:
        if motion and (motion[0] < box[0] - tol_x or motion[1] < box[1] - tol_y or
                       motion[2] > box[2] + tol_x or motion[3] > box[3] + tol_y):
            raise Ambiguous("motion extends outside the flat-border box %s (motion %s)" % (box, motion))
        if not motion or area(motion) >= 0.5 * area(box):
            return "crop", box, "flat border around the app (static surroundings)", facts
        # a flat bar or two on a busy desktop is not the app: fall through to the motion box

    # no usable border: surroundings, if any, must show up as motion inside a smaller box
    if not motion:
        return "app-only", full, "no uniform border and nothing moving; frame left alone", facts
    if len(facts["row_runs"]) > 1 or len(facts["col_runs"]) > 1:
        if area(motion) >= BIG_BOX * area(full):
            return "app-only", full, "motion spans the frame in several regions; frame left alone", facts
        raise Ambiguous("moving content splits into several regions (rows %s, cols %s): two windows "
                        "or an app plus a busy desktop" % (facts["row_runs"], facts["col_runs"]))
    sides = gap_sides(motion)
    if not sides:
        return "app-only", full, "moving content fills the frame; frame left alone", facts
    # snap the cropped sides to real edges and require edge evidence on each
    box = list(motion)
    reach_x, reach_y = max(2, int(0.05 * w)), max(2, int(0.05 * h))
    weak = []
    for side in sides:
        idx = {"left": 0, "top": 1, "right": 2, "bottom": 3}[side]
        reach = reach_x if side in ("left", "right") else reach_y
        score, pos = best_edge(mid, w, h, side, box[idx], reach, tuple(box))
        if score >= EDGE_SHARE:
            box[idx] = pos
        else:
            weak.append((side, round(score, 2)))
    box = tuple(box)
    if weak:
        if area(motion) >= BIG_BOX * area(full):
            return "app-only", full, ("moving content covers most of the frame and its edges (%s) "
                                      "are not window edges; frame left alone" % weak), facts
        raise Ambiguous("moving region %s has no window-edge evidence on %s; a small animation on a "
                        "full-screen app and a window on a desktop look alike here" % (motion, weak))
    if area(box) < MIN_BOX * area(full):
        raise Ambiguous("detected box %s is under %d%% of the frame" % (box, int(MIN_BOX * 100)))
    return "crop", box, "moving window with real edges on every cropped side", facts


def refine_full(path, t, fw, fh, box):
    """Nudge each cropped side of a full-resolution box onto the best edge within a few pixels."""
    frame = grab_gray(path, t, fw, fh)
    if frame is None:
        return box
    x, y, w, h = box
    cur = [x, y, x + w, y + h]
    for side, idx, lim in (("left", 0, fw), ("top", 1, fh), ("right", 2, fw), ("bottom", 3, fh)):
        if (idx in (0, 1) and cur[idx] == 0) or (idx == 2 and cur[2] >= fw) or (idx == 3 and cur[3] >= fh):
            continue
        score, pos = best_edge(frame, fw, fh, side, cur[idx], 3, tuple(cur))
        if score >= EDGE_SHARE:
            cur[idx] = pos
    return even_box(cur[0], cur[1], cur[2] - cur[0], cur[3] - cur[1])


def detect(path):
    """Detect the app box of a video. Returns a result dict; raises Ambiguous / BadInput."""
    need_tools()
    fw, fh, dur = probe(path)
    sw = min(DETECT_WIDTH, fw)
    sw -= sw % 2
    sh = int(round(fh * sw / float(fw)))
    sh -= sh % 2
    frames = []
    for t in sample_times(dur):
        f = grab_gray(path, t, sw, sh)
        if f is not None:
            frames.append(f)
    if len(frames) < 4:
        raise BadInput("could only decode %d sample frame(s) from %s" % (len(frames), path))
    black = cropdetect_box(path, fw, fh)
    sx, sy = fw / float(sw), fh / float(sh)
    black_s = None
    if black:
        bx, by, bw, bh = black
        black_s = (int(bx / sx), int(by / sy), int(round((bx + bw) / sx)), int(round((by + bh) / sy)))
    mode, box, reason, facts = decide(frames, sw, sh, black_s)
    result = {"mode": mode, "reason": reason, "frame": [fw, fh], "samples": len(frames),
              "cropdetect_box": list(black) if black else None}
    if mode == "app-only":
        result["crop"] = None
        return result
    x, y, w, h = (int(round(box[0] * sx)), int(round(box[1] * sy)),
                  int(round((box[2] - box[0]) * sx)), int(round((box[3] - box[1]) * sy)))
    x, y = max(0, x), max(0, y)
    w, h = min(w, fw - x), min(h, fh - y)
    ex = even_box(x, y, w, h)
    if sx > 1.0:
        ex = refine_full(path, dur / 2.0, fw, fh, ex)
    result["crop"] = list(ex)
    return result


def resolve(path, crop_arg=None, no_crop=False):
    """The crop decision for a video: dict with mode and crop = [x, y, w, h] or None.

    Used by trim.py and cut_windows.py. Raises BadInput, Ambiguous.
    """
    need_tools()
    fw, fh, _ = probe(path)
    if no_crop:
        return {"mode": "full-frame", "reason": "--no-crop", "frame": [fw, fh], "crop": None}
    if crop_arg:
        x, y, w, h = parse_box(crop_arg, fw, fh)
        full = (x, y, w, h) == (0, 0, fw - fw % 2, fh - fh % 2)
        return {"mode": "app-only" if full else "forced-crop", "reason": "--crop %s" % crop_arg,
                "frame": [fw, fh], "crop": None if full else [x, y, w, h]}
    if config_value("VIDEO_TRIM_CROP", "auto").lower() in ("off", "0", "false", "no"):
        return {"mode": "full-frame", "reason": "VIDEO_TRIM_CROP=off", "frame": [fw, fh], "crop": None}
    return detect(path)


def filter_expr(result):
    """ffmpeg crop filter for a result, or '' when the frame is kept."""
    if not result.get("crop"):
        return ""
    x, y, w, h = result["crop"]
    return "crop=%d:%d:%d:%d" % (w, h, x, y)


def describe(result):
    """One-line summary, also the text recorded in the segment map."""
    fw, fh = result["frame"]
    if not result.get("crop"):
        return "crop: none (%s; %s), frame %dx%d kept" % (result["mode"], result["reason"], fw, fh)
    x, y, w, h = result["crop"]
    return "crop: %d:%d:%d:%d W:H:X:Y (%s; %s), from %dx%d" % (w, h, x, y, result["mode"], result["reason"], fw, fh)


def write_stills(path, result, prefix, at=None):
    """Write <prefix>.crop-before.png (full frame, box outlined) and <prefix>.crop-after.png."""
    if not result.get("crop"):
        return []
    _, _, dur = probe(path)
    t = dur / 2.0 if at is None else at
    x, y, w, h = result["crop"]
    outs = []
    for name, vf in (("before", "drawbox=x=%d:y=%d:w=%d:h=%d:color=red@0.9:t=4" % (x, y, w, h)),
                     ("after", "crop=%d:%d:%d:%d" % (w, h, x, y))):
        out = "%s.crop-%s.png" % (prefix, name)
        res = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", "%.3f" % t, "-i", str(path),
                              "-frames:v", "1", "-vf", vf, out], capture_output=True, text=True)
        if res.returncode != 0 or not Path(out).is_file():
            raise FfmpegFailed("still failed: %s" % res.stderr.strip()[:300])
        outs.append(out)
    return outs


def add_flags(ap):
    """The shared crop flags for scripts that import this module."""
    ap.add_argument("--crop", metavar="W:H:X:Y", help="crop to this pixel box instead of detecting the app")
    ap.add_argument("--no-crop", action="store_true",
                    help="keep the full frame (use when the surroundings are the evidence)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="input video (never modified)")
    add_flags(ap)
    ap.add_argument("--still-prefix", metavar="PREFIX",
                    help="write PREFIX.crop-before.png / .crop-after.png (default: next to the input)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the summary line")
    a = ap.parse_args()
    try:
        if a.crop and a.no_crop:
            raise BadInput("--crop and --no-crop together")
        if not Path(a.src).is_file():
            raise BadInput("input not found: %s" % a.src)
        result = resolve(a.src, a.crop, a.no_crop)
        prefix = a.still_prefix or str(Path(a.src).with_suffix(""))
        result["stills"] = write_stills(a.src, result, prefix)
    except BadInput as e:
        log("error: %s" % e)
        return 2
    except Ambiguous as e:
        log("ambiguous: %s" % e)
        log("pass --crop W:H:X:Y to name the app box, or --no-crop to keep the full frame")
        return 3
    except FfmpegFailed as e:
        log("error: %s" % e)
        return 1
    print(json.dumps(result) if a.json else describe(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
