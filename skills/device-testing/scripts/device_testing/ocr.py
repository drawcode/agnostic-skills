"""Read the text on a screenshot with the macOS Vision framework, driven through osascript (JXA).

No Python packages are needed. `read_text(png)` returns one dict per recognized line with a pixel box
(top-left origin): {"text", "x1", "y1", "x2", "y2", "cx", "cy"}. macOS only; elsewhere it raises."""
import json
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
JS = os.path.join(HERE, "ocr_vision.js")


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG: {path}")
    return struct.unpack(">II", head[16:24])


def boxes_from_vision(raw, width, height):
    """Vision boxes are 0-1 fractions from the bottom-left; return pixel boxes from the top-left."""
    out = []
    for item in raw:
        x1, x2 = item["x"] * width, (item["x"] + item["w"]) * width
        y1, y2 = (1 - item["y"] - item["h"]) * height, (1 - item["y"]) * height
        out.append({"text": item["t"], "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                    "cx": (x1 + x2) / 2, "cy": (y1 + y2) / 2})
    return out


def read_text(png):
    if sys.platform != "darwin":
        raise RuntimeError("ocr needs macOS (Vision framework)")
    r = subprocess.run(["osascript", "-l", "JavaScript", JS, os.path.abspath(png)],
                       capture_output=True, text=True, timeout=120)
    text = r.stdout.strip()
    if r.returncode != 0 or text.startswith("ERR") or not text:
        raise RuntimeError(f"vision failed: {text or r.stderr.strip()}")
    width, height = png_size(png)
    return boxes_from_vision(json.loads(text), width, height)
