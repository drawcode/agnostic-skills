# Video trim: install

Cut the dead time out of screen recordings, scrub notification banners, and build review videos.
What it does and when to use each script: [`SKILL.md`](./SKILL.md).

## Needs

| Dependency | For | Install |
|---|---|---|
| `python3` 3.9+ | every script (stdlib only) | Windows: `py` in place of `python3` |
| `ffmpeg` and `ffprobe` on PATH | every script | macOS `brew install ffmpeg`; Debian/Ubuntu `apt install ffmpeg`; Windows `winget install Gyan.FFmpeg` or `choco install ffmpeg` |
| OCR, only for `scrub_overlays.py` | reading banners | macOS: nothing extra when `swiftc` exists (Xcode command line tools: `xcode-select --install`); the Vision helper is built once on first use. Any OS: `tesseract` on PATH (`brew install tesseract`, `apt install tesseract-ocr`, or the UB Mannheim installer / `choco install tesseract` on Windows) |

No network access, nothing written outside the outputs you name and a per-user
cache folder for the compiled OCR helper (`VIDEO_TRIM_CACHE`, else `~/.cache/video-trim`, or
`%LOCALAPPDATA%\video-trim` on Windows).

Optional setting: `VIDEO_TRIM_CROP=off` (environment, or a `KEY=VALUE` line in
`~/.config/video-trim/config.env`) stops `trim.py` and `cut_windows.py` cropping to the app by
default; the default is `auto`.

## What is in it

| Path | What it is |
|---|---|
| `SKILL.md` | When to use which script, the rules and the traps |
| `scripts/app_crop.py` | Find the app's rectangle in a larger capture (used by the two below) |
| `scripts/trim.py` | Cut every frozen stretch, keep a beat at each edge |
| `scripts/cut_windows.py` | Keep explicit or log-driven windows instead |
| `scripts/scrub_overlays.py` | Cut notification banners out (OCR) |
| `scripts/ocr_frames.swift` | macOS Vision OCR helper behind `scrub_overlays.py --ocr vision` |
| `scripts/stitch.py` | Join recordings into one review video with a chapter list |
| `scripts/side_by_side.py` | Slowed before/after of a sub-second effect |
| `scripts/frame_strip.py` | Contact sheet to check a cut |

## Install

1. Put the `video-trim` folder where your harness looks for skills.
   - Claude Code: `~/.claude/skills/video-trim` (all projects), or `<repo>/.claude/skills/video-trim`.
   - Codex, opencode: `~/.agents/skills/video-trim`, or the harness's own skills folder.
   - Other harnesses: any fixed folder, with a pointer to its `SKILL.md` in your always-loaded
     rules (`AGENTS.md`, `GEMINI.md`, and so on).
2. Check the tools are found:

   ```sh
   ffmpeg -version && ffprobe -version
   python3 <skill>/scripts/trim.py --help
   python3 <skill>/scripts/scrub_overlays.py --dry-run <any.mp4>   # exits 2 with hints if no OCR backend
   ```

There are no hooks to wire. To remove it, delete the folder (and the cache folder above).
