#!/usr/bin/env python3
"""Before/after comparison of a sub-second effect (a flash, a pop-in), slowed down, side by side.

Leave both takes untrimmed. For each take the event frame is found in a constant-frame-rate decode
(fps=N): a small grayscale crop of --region is reduced to its mean brightness per frame, and the
event is the frame with the largest absolute change from the previous one. Each take is then cut by
frame number (not by -ss, which does not line two recordings up), slowed, padded to the same size and
stacked left/right. No text is drawn into the picture (many ffmpeg builds lack freetype), so the
labels go in a sidecar text file and in the --json result.

Examples (SKILL = the skill folder):
  python3 SKILL/scripts/side_by_side.py off.mp4 on.mp4 out.mp4
  python3 SKILL/scripts/side_by_side.py off.mp4 on.mp4 out.mp4 --region 0.2,0.1,0.6,0.3 --slow 4
  python3 SKILL/scripts/side_by_side.py a.mp4 b.mp4 out.mp4 --event-a 212 --event-b 187 --json

Left is A, right is B. Writes OUT and a sidecar OUT.txt (or --labels-file PATH) naming left and right,
the event frames and the slow factor. Never touches the inputs; refuses to replace an existing output
unless --force. Exit codes: 0 ok, 1 ffmpeg or processing failed, 2 bad input.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

PROBE_SIZE = 32


class BadInput(Exception):
    pass


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def need_tools():
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        raise BadInput("required tool(s) not on PATH: " + ", ".join(missing))


def probe_size(path):
    res = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "json", str(path)], capture_output=True, text=True)
    try:
        s = json.loads(res.stdout)["streams"][0]
        return int(s["width"]), int(s["height"])
    except (ValueError, KeyError, IndexError):
        raise BadInput("ffprobe could not read a video stream from %s" % path)


def parse_region(text):
    try:
        x, y, w, h = [float(p) for p in text.split(",")]
    except ValueError:
        raise BadInput("bad --region %r (want X,Y,W,H, each 0-1)" % text)
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.0001 and y + h <= 1.0001):
        raise BadInput("--region must stay inside the frame, values 0-1")
    return x, y, w, h


def region_means(path, region, fps):
    x, y, w, h = region
    vf = "fps=%s,crop=iw*%.5f:ih*%.5f:iw*%.5f:ih*%.5f,scale=%d:%d,format=gray" % (
        fps, w, h, x, y, PROBE_SIZE, PROBE_SIZE)
    res = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-an", "-vf", vf, "-f", "rawvideo", "-"],
                         capture_output=True)
    if res.returncode != 0:
        raise RuntimeError("ffmpeg failed decoding %s: %s" % (path, res.stderr.decode(errors="replace").strip()))
    n = PROBE_SIZE * PROBE_SIZE
    data = res.stdout
    return [sum(data[i:i + n]) / n for i in range(0, len(data) - n + 1, n)]


def candidates(means, fps, count=3):
    """Top frames by absolute brightness change, neighbors within 0.1 s suppressed."""
    changes = sorted(((abs(means[i] - means[i - 1]), i) for i in range(1, len(means))), reverse=True)
    gap = max(1, int(fps // 10))
    picked = []
    for delta, i in changes:
        if all(abs(i - j) > gap for _, j, _ in picked):
            picked.append((delta, i, i / fps))
        if len(picked) == count:
            break
    return [{"frame": i, "time": round(t, 3), "change": round(d, 3)} for d, i, t in picked]


def find_event(path, region, fps, label):
    means = region_means(path, region, fps)
    if len(means) < 2:
        raise RuntimeError("%s: fewer than 2 frames decoded" % path)
    cands = candidates(means, fps)
    log("%s event candidates (%s):" % (label, Path(path).name))
    for c in cands:
        log("  frame %d  t=%.3fs  change=%.3f" % (c["frame"], c["time"], c["change"]))
    return cands


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a", help="left video")
    ap.add_argument("b", help="right video")
    ap.add_argument("dst", help="output mp4")
    ap.add_argument("--region", default="0,0,1,1", metavar="X,Y,W,H",
                    help="normalized 0-1 area watched for the event (default the whole frame)")
    ap.add_argument("--event-a", type=int, metavar="FRAME", help="event frame of A (default: detect)")
    ap.add_argument("--event-b", type=int, metavar="FRAME", help="event frame of B (default: detect)")
    ap.add_argument("--pre", type=int, default=6, help="frames kept before the event (default 6)")
    ap.add_argument("--post", type=int, default=36, help="frames kept after the event (default 36)")
    ap.add_argument("--slow", type=float, default=4, help="slow-down factor (default 4)")
    ap.add_argument("--fps", type=float, default=30, help="constant frame rate for detection and output (default 30)")
    ap.add_argument("--scale-width", type=int, default=540, help="width of each side (default 540)")
    ap.add_argument("--labels-file", metavar="PATH", help="sidecar text path (default <out>.txt)")
    ap.add_argument("--force", action="store_true", help="replace an existing output")
    ap.add_argument("--json", action="store_true", help="print a JSON result instead of the result line")
    args = ap.parse_args()
    try:
        return run(args)
    except BadInput as e:
        log("error: %s" % e)
        return 2
    except RuntimeError as e:
        log("error: %s" % e)
        return 1


def run(args):
    need_tools()
    a, b, dst = Path(args.a), Path(args.b), Path(args.dst)
    for p in (a, b):
        if not p.is_file():
            raise BadInput("input not found: %s" % p)
    if dst.resolve() in (a.resolve(), b.resolve()):
        raise BadInput("output would overwrite an input")
    labels = Path(args.labels_file) if args.labels_file else dst.with_name(dst.name + ".txt")
    if not args.force:
        for p in (dst, labels):
            if p.exists():
                raise BadInput("%s exists (use --force to replace)" % p)
    if args.pre < 0 or args.post < 1 or args.slow <= 0 or args.fps <= 0 or args.scale_width < 2:
        raise BadInput("--pre >= 0, --post >= 1, --slow > 0, --fps > 0, --scale-width >= 2 required")
    region = parse_region(args.region)

    found = {}
    events = {}
    for key, path, given in (("a", a, args.event_a), ("b", b, args.event_b)):
        if given is not None:
            if given < 0:
                raise BadInput("event frame must be >= 0")
            events[key] = given
        else:
            found[key] = find_event(path, region, args.fps, key.upper())
            events[key] = found[key][0]["frame"]

    sizes = {k: probe_size(p) for k, p in (("a", a), ("b", b))}
    width = args.scale_width // 2 * 2
    heights = {k: int(round(h * width / w / 2)) * 2 for k, (w, h) in sizes.items()}
    height = max(heights.values())

    def side(idx, key, label):
        start = max(0, events[key] - args.pre)
        end = events[key] + args.post
        return ("[%d:v]fps=%s,trim=start_frame=%d:end_frame=%d,setpts=(PTS-STARTPTS)*%s,"
                "scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:0:black,setsar=1[%s]"
                % (idx, args.fps, start, end, args.slow, width, height, width, height, label))

    graph = (side(0, "a", "l") + ";" + side(1, "b", "r") +
             ";[l][r]hstack=inputs=2,fps=%s,format=yuv420p[out]" % args.fps)
    tmp = dst.with_name(dst.stem + ".partial.mp4")
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-stats", "-y", "-i", str(a), "-i", str(b),
           "-filter_complex", graph, "-map", "[out]", "-an", "-c:v", "libx264", "-crf", "24",
           "-preset", "medium", "-movflags", "+faststart", str(tmp)]
    res = subprocess.run(cmd)
    if res.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        log("error: ffmpeg failed (exit %d)" % res.returncode)
        return 1
    tmp.replace(dst)

    result = {"ok": True, "output": str(dst), "labels_file": str(labels), "left": a.name, "right": b.name,
              "event_frames": {"a": events["a"], "b": events["b"]},
              "event_detected": {"a": "a" in found, "b": "b" in found},
              "candidates": {k.upper(): v for k, v in found.items()},
              "slow": args.slow, "fps": args.fps, "pre": args.pre, "post": args.post,
              "region": list(region), "size_bytes": dst.stat().st_size}
    labels.write_text("left: %s\nright: %s\nevent frames: left %d, right %d (fps %s)\nslow factor: %sx\n"
                      % (a.name, b.name, events["a"], events["b"], args.fps, args.slow))
    if args.json:
        print(json.dumps(result))
    else:
        print("%s: left %s (event frame %d), right %s (event frame %d), slow %sx, %.1f MB"
              % (dst, a.name, events["a"], b.name, events["b"], args.slow, result["size_bytes"] / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
