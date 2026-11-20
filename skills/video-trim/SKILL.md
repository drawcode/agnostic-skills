---
name: video-trim
description: Use to cut the dead time out of a screen recording before handing it over -- a phone screenrecord, an engine or app recorder clip, a video encoded from frame dumps, or any mp4 with long stretches where nothing on screen changes. Detects freezes with ffmpeg, keeps a short beat at each edge so every screen still visibly settles, and writes a trimmed copy with a source-time subtitle track and a segment map so a moment in the short video can still be matched to its log line. Also keeps explicit or log-driven windows when a held screen is the evidence, builds slowed before/after side-by-sides, makes contact sheets to check a cut, and cuts notification banners or any other non-app overlay (personal info such as a payment or doorbell alert) out of a recording before it is analyzed or shared. Works anywhere ffmpeg runs.
---

# Trim the static stretches out of a recording

A test pass spends most of its wall clock waiting: a load, a network round trip, an agent reading
the last screenshot. The recording keeps all of it. A 365 s capture was **293 s frozen**; the
trimmed copy was 84 s and 7 MB, with every screen change still on screen.

Scripts are in `<skill>/scripts/` (`<skill>` = the folder holding this file). Python 3 stdlib
plus `ffmpeg` and `ffprobe` on PATH. All of them write a new file (never the input, except
`scrub_overlays.py --in-place`), refuse to overwrite an output without `--force`, print a result
line on stdout, take `--json`, and exit `0` ok / `1` ffmpeg or processing failed / `2` bad input (`stitch.py` differs, see its section).

| script | use it for |
|---|---|
| `trim.py` | cut every frozen stretch, keep a beat at each edge |
| `cut_windows.py` | keep explicit windows instead -- when a held screen is the evidence |
| `scrub_overlays.py` | cut notification banners out before anything else |
| `stitch.py` | join per-feature recordings into one review video with a chapter list |
| `side_by_side.py` | slowed before/after of a sub-second effect |
| `frame_strip.py` | contact sheet to check a cut before handing it over |

Order: **scrub -> trim or cut -> frame strip -> stitch (if one file is wanted) -> hand over.**

## `trim.py`: cut the freezes

```bash
python3 <skill>/scripts/trim.py video/run.mp4               # -> video/run-trimmed.mp4 + .trimmap.txt
python3 <skill>/scripts/trim.py video/run.mp4 out.mp4 --min 1.5 --keep-in 0.6 --keep-out 0.4
```

1. **Detect.** `freezedetect` at `-50dB` on a 270 px wide copy -- detection only needs coarse
   change, and scaling first makes it several times faster. Roughly 13x realtime at 1080x2424.
2. **Cut.** Every freeze longer than `--min` (1.5 s) keeps its first `--keep-in` (0.6 s) and last
   `--keep-out` (0.4 s). The reviewer still sees each screen arrive and settle.
3. **Encode.** One `trim`/`concat` graph, H.264 CRF 30, `yuv420p` so it plays in browsers and
   QuickTime.
4. **Keep the clock.** A `mov_text` subtitle track shows the source time of each kept segment, and
   `<out>.trimmap.txt` lists the kept `src_start src_end` pairs. If `<in>.start` exists (one line,
   the wall-clock time recording began) it is included, so `start + src offset` = the log time.

## `cut_windows.py`: keep the windows that matter

Freeze trimming cuts a **held** screen -- the one you opened to show a number -- to 0.6 s. When
the held screen is the evidence, choose the windows instead.

```bash
# explicit windows
python3 <skill>/scripts/cut_windows.py run.mp4 --window 3.0-9.5 --window 41-48
# the trimmed segments, plus a held window around every log line that proves a step
python3 <skill>/scripts/cut_windows.py run.mp4 out.mp4 --from-trimmap run-trimmed.trimmap.txt \
  --log run.log --match '<regex of the evidence line>' --before 0.5 --after 6 --crop-bottom 0.055
```

- Windows come from `--window S-E` (repeatable), `--from-trimmap FILE`, and `--log FILE --match
  REGEX` (each match becomes `[t - before, t + after]`). They are combined, clipped to the video
  (or `--limit SECONDS`), sorted and merged within `--merge-gap` (0.05 s).
- **Log time -> video time** uses the `.start` file (`--start-file`, else `<in>.start`, else the
  trimmap header). The line's timestamp is pulled with `--log-time-regex` (default: an Android
  logcat stamp `MM-DD HH:MM:SS.fff`) and parsed with `--log-time-format` (default
  `%m-%d %H:%M:%S.%f`); a stamp without a year borrows the start file's. Set both flags for any
  other log format.
- `fps` (30) is applied **first**, so a held screen has frames to keep, then `trim`/`concat`, then
  optional `--crop-top` / `--crop-bottom` (fractions of the height, e.g. to drop a debug bar) and
  `--scale-width` (540; `0` keeps the size). `--crf` 28.
- Writes `<out>.segments.txt`: a header with the source and its start time, then the kept
  `src_start src_end` pairs. Default output `<in>-cut.mp4`.

## `scrub_overlays.py`: banners out first

**Every phone recording goes through this before it is analyzed, trimmed or shared.** A banner
from another app (a payment, a doorbell, a message) leaks personal info and covers the UI under
test.

```bash
python3 <skill>/scripts/scrub_overlays.py --dry-run takes/*.mp4             # list spans, change nothing
python3 <skill>/scripts/scrub_overlays.py --in-place take1.mp4 take2.mp4   # replace each source
python3 <skill>/scripts/scrub_overlays.py --ocr tesseract --extra-pattern 'just now' take.mp4
```

Banners drop in at the top and carry a relative time (`now`, `2m ago`), so the top 22% (`--top`)
of each frame is read with OCR (2 fps to find, 10 fps to bound), and each span plus a 0.3 s
slide-in and 0.7 s slide-out margin is cut. The result is re-scanned and only written when it
comes back clean. `--in-place` is the right default here: the source is the leak, so keeping it
"as evidence" defeats the point.

- **OCR backends** (`--ocr auto|vision|tesseract`): `vision` is the macOS text recognizer, built
  once with `swiftc` into `VIDEO_TRIM_CACHE` (default: the user cache dir); `tesseract` works on
  any OS with `tesseract` on PATH. `auto` picks vision on macOS, else tesseract, else exits 2
  with install hints. `ocr_frames.swift` is the macOS-only helper behind `vision`; on Linux or
  Windows (or macOS without `swiftc`) the wrapper falls back to `tesseract` cleanly.
- Banner time words are `STAMP_PATTERNS` in the script; add your own with `--extra-pattern`
  (repeatable), e.g. for another language.
- Prevention beats scrubbing: silence the phone before recording (Do Not Disturb or Focus;
  `device quiet on` if you have the `device-testing` skill). Cutting frames shifts the timeline, so on a timing capture retake
  instead when a banner lands mid-measurement.
- Tesseract accuracy on real banners is less proven than vision; dry-run first and check a frame
  strip.

## `stitch.py`: one review video from several recordings

App store review wants ONE video file. `stitch.py` joins per-feature
segments and writes a chapter list to paste into the review notes.

```bash
python3 <skill>/scripts/stitch.py review.mp4 "First launch=launch.mp4" "Protected set=pin.mp4" --trim
# -> review.mp4 + review.chapters.txt   ("0:00 First launch", "1:38 Protected set")
```

- Each segment is letterboxed to the FIRST segment's size, 30 fps, H.264, audio dropped (`--crf`,
  default 26), so a phone take and an iPad re-take can sit in one file. Chapter times come from
  the probed durations.
- `--trim` runs `trim.py` on a temp copy of each segment first; sources are never modified.
- Refuses an existing output without `--force`, a missing or empty segment, or an output that is
  a segment. Exit 0 ok, 1 no video stream, 2 bad usage, 3 ffmpeg failed (this script's codes
  differ from the others). `--json` for a machine summary.

## `side_by_side.py`: before/after of a sub-second effect

A flash, a pop-in or a flicker is gone in a few frames; freeze trimming and `-ss` seeking both
miss it (two recordings' timestamps start differently, so seeking does not line them up).

```bash
python3 <skill>/scripts/side_by_side.py before.mp4 after.mp4 compare.mp4 --region 0.1,0.3,0.8,0.2
python3 <skill>/scripts/side_by_side.py before.mp4 after.mp4 compare.mp4 --event-a 59 --event-b 61 --slow 4
```

- Without `--event-a/-b`, each take is decoded at `--fps` (30) to a 32x32 gray crop of `--region`
  (normalized `X,Y,W,H`, default the whole frame), and the event is the frame whose brightness
  changes most. The top 3 candidates per take go to stderr and the JSON. **Pass a `--region`
  around the effect**: on the whole frame, a screen transition usually wins.
- Each take is cut by frame number (`--pre` 6, `--post` 36), slowed `--slow` times (4), scaled
  to `--scale-width` (540), padded to the same height and stacked: A left, B right.
- No text is burned in (many ffmpeg builds lack `drawtext`); `<out>.txt` records which side is
  which, the event frames and the slow factor. Put "before on left" in the video's title.

## `frame_strip.py`: check the cut

```bash
python3 <skill>/scripts/frame_strip.py run-trimmed.mp4                     # 12 evenly spaced frames
python3 <skill>/scripts/frame_strip.py run-cut.mp4 sheet.png --every 5 --cols 8
python3 <skill>/scripts/frame_strip.py run-cut.mp4 --times 3.2,10,41.5
```

One grab per time (so variable-frame-rate sources show the frame actually on screen), tiled
`--cols` (6) wide at `--tile-width` (240). Prints the PNG path and the times. Look at it before
handing a video over: a banner that slipped through, a debug overlay, an evidence screen cut too
short.

## Rules

- **Work on a copy, never the original** (`scrub_overlays.py --in-place` is the exception). The
  original is the evidence; the trimmed file is for reading. Keep both until the work is done.
- **Write `<name>.start` when recording starts** -- the device's clock for a phone
  (`adb shell date +%H:%M:%S.%3N`, or `device record start` in the `device-testing` skill),
  `date` for a desktop capture.
  Without it a cut video cannot be lined up with a log.
- **Say it is trimmed** when handing a video over, and send the `.trimmap.txt` /
  `.segments.txt` if anyone will match it to logs. A reader who assumes real time will misjudge
  how long a load took.
- **Do not trim a timing capture.** A video whose point is how long something took must stay
  untrimmed, or trim with a `--min` longer than the thing being shown.
- **The thresholds are robust.** On the reference capture `-50dB`, `-40dB`, `0.003` and `0.01`
  all found 292-295 s of freeze, so do not tune noise per video; tune `--min` only.

## Traps

- ⚠️ **A static result screen gets cut to 0.6 s.** A phone screen recorder emits no frames while
  nothing moves, so a page you opened to show a number is one long freeze. Use `cut_windows.py`
  (constant frame rate first, so the held screen still has frames).
- ⚠️ **A screen whose only motion is a small idle animation sits below `-50dB`** and is cut to
  about 1 s too. Rebuild with `cut_windows.py --from-trimmap ... --log ... --match ...` so each
  evidence moment gets a held window.
- ⚠️ **Static "before" vs moving "after".** A blank scene for the whole clip is one long freeze,
  while a constantly animating fixed scene is never cut. For before/after pairs, cut explicit
  windows on both.
- ⚠️ **An animated background defeats detection.** Drifting clouds or particles are not frozen at
  `-50dB`. If a capture barely shrinks, check a mid-freeze frame for motion before lowering the
  threshold, and prefer driving the capture from a static screen.
- ⚠️ **`freezedetect` reports no `freeze_end` when the file ends frozen.** `trim.py` closes that
  freeze at the file's duration; a hand-rolled version that zips starts with ends drops it.
- ⚠️ **No `drawtext` in many ffmpeg builds** (built without freetype), so the source clock rides
  as a subtitle track and labels go in sidecar files.
- ⚠️ **Variable frame rate sources** (Android `screenrecord` only emits frames on change) are fine:
  `trim` works on timestamps and `setpts=PTS-STARTPTS` rebases each segment.
- ⚠️ `hstack` needs identical heights; `side_by_side.py` scales into a shared box and pads.
- In zsh, `${T}[b]` and not `$T[b]` (a subscript) when building filter strings by hand.

## Related

- Recording on a phone (quiet first, the `.start` file, recorder traps): the `device-testing`
  skill, `references/capture.md`, where installed.
- Setup and dependencies: [`INSTALL.md`](./INSTALL.md). Workspace tuning goes in an
  `addons/<name>.addon.md` beside this file.
