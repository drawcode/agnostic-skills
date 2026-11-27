# Install video-trim

Cut the dead time out of screen recordings, scrub notification banners, and build review videos.
Install is a folder copy: it changes nothing else. What it does and when to use each script: [`SKILL.md`](./SKILL.md).

## Requirements

- `python3` 3.9+ (stdlib only). Windows: `py` in place of `python3`.
- `ffmpeg` and `ffprobe` on PATH.
- OCR, only for `scrub_overlays.py`: `swiftc` on macOS, or `tesseract` on any OS.

## Install

1. Put the `video-trim` folder where your harness looks for skills (details in the toggle below).

   ```sh
   cp -R video-trim ~/.claude/skills/video-trim
   ```

2. Check the tools are found:

   ```sh
   ffmpeg -version && ffprobe -version
   ```

3. Check the scripts run:

   ```sh
   python3 <skill>/scripts/trim.py --help
   ```

4. Check OCR (exits 2 with hints if no OCR backend):

   ```sh
   python3 <skill>/scripts/scrub_overlays.py --dry-run <any.mp4>
   ```

There are no hooks to wire.

## Check it works

```sh
python3 <skill>/scripts/trim.py --help
```

## Uninstall

Delete the folder (and the cache folder, see below):

```sh
rm -rf ~/.claude/skills/video-trim
```

<details>
<summary><b>Per-harness locations</b></summary>

- Claude Code: `~/.claude/skills/video-trim` (all projects), or `<repo>/.claude/skills/video-trim`.
- Codex, opencode: `~/.agents/skills/video-trim`, or the harness's own skills folder.
- Other harnesses: any fixed folder, with a pointer to its `SKILL.md` in your always-loaded rules (`AGENTS.md`, `GEMINI.md`, and so on).

</details>

<details>
<summary><b>Dependencies and install commands</b></summary>

| Dependency | For | Install |
|---|---|---|
| `python3` 3.9+ | every script (stdlib only) | Windows: `py` in place of `python3` |
| `ffmpeg` and `ffprobe` on PATH | every script | macOS `brew install ffmpeg`; Debian/Ubuntu `apt install ffmpeg`; Windows `winget install Gyan.FFmpeg` or `choco install ffmpeg` |
| OCR, only for `scrub_overlays.py` | reading banners | macOS: nothing extra when `swiftc` exists (Xcode command line tools: `xcode-select --install`); the Vision helper is built once on first use. Any OS: `tesseract` on PATH (`brew install tesseract`, `apt install tesseract-ocr`, or the UB Mannheim installer / `choco install tesseract` on Windows) |

</details>

<details>
<summary><b>Configuration</b></summary>

Optional. Set in the environment, or as a `KEY=VALUE` line in `~/.config/video-trim/config.env` (template: `config.example.env`).

| Key | Default | Meaning |
|---|---|---|
| `VIDEO_TRIM_CROP` | `auto` | `off` stops `trim.py` and `cut_windows.py` cropping to the app by default |
| `VIDEO_TRIM_CACHE` | `~/.cache/video-trim`, or `%LOCALAPPDATA%\video-trim` on Windows | Folder for the compiled OCR helper |

</details>

<details>
<summary><b>What is in it</b></summary>

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

</details>

<details>
<summary><b>What it writes</b></summary>

- No network access.
- Nothing is written outside the outputs you name and a per-user cache folder for the compiled OCR helper (`VIDEO_TRIM_CACHE`, else `~/.cache/video-trim`, or `%LOCALAPPDATA%\video-trim` on Windows).
- To remove it, delete the folder (and the cache folder).

</details>
