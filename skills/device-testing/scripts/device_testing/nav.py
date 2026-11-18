"""Nav profiles: a TSV of named steps, so screen coordinates are data, never code.

  id <TAB> tap 540 2285 <TAB> wait regex <TAB> what it does

Headers: `#!size WxH` (calibrated screen; refused on any other), `#!coords normalized` (fractions
carry across sizes, guarded by `#!density N` instead, since density changes the layout itself).

Coordinate tokens in a tap/swipe action:
  540      raw pixels
  0.4991   a fraction of that axis
  137dp    density-independent pixels from the left/top edge
  -53dp    from the right/bottom edge
  c137dp   from the centre of the axis (negative = left/up)
iOS reports its point scale as density (3x = 480), so `dp` means a point there.
"""
import re


class NavError(Exception):
    pass


def rows(path):
    with open(path) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) >= 2:
                yield cols + [""] * (4 - len(cols))


def header(path, name):
    with open(path) as f:
        for line in f:
            m = re.match(rf"#!{name}\s+(\S+)", line)
            if m:
                return m.group(1)
    return None


def check_screen(path, size, density):
    if header(path, "coords") == "normalized":
        want = header(path, "density")
        if want and int(want) != int(density):
            raise NavError(f"nav profile is normalized but calibrated at {want}dpi and this device is "
                           f"{density}dpi -- {path}. Density changes the layout, so the fractions do not carry over.")
        return
    want = header(path, "size")
    have = f"{size[0]}x{size[1]}"
    if want and want != have:
        raise NavError(f"nav profile is calibrated for {want} but this device is {have} -- {path}. "
                       f"Give the device its own DEVICE_<ALIAS>_NAV_PROFILE.")


def resolve_token(token, axis, density):
    if re.fullmatch(r"c-?[\d.]+dp", token):
        return int(axis / 2 + float(token[1:-2]) * density / 160 + 0.5)
    if re.fullmatch(r"-[\d.]+dp", token):
        return int(axis + float(token[:-2]) * density / 160 + 0.5)
    if re.fullmatch(r"[\d.]+dp", token):
        return int(float(token[:-2]) * density / 160 + 0.5)
    if re.fullmatch(r"0?\.\d+", token):
        return int(float(token) * axis + 0.5)
    return int(token)


def resolve_action(action, size, density):
    """('tap'|'swipe'|'key', [ints or keycode])."""
    verb, *args = action.split()
    if verb in ("tap", "swipe"):
        coords = args[:4] if verb == "swipe" else args[:2]
        resolved = [resolve_token(t, size[i % 2], density) for i, t in enumerate(coords)]
        if verb == "swipe":
            resolved.append(int(args[4]) if len(args) > 4 else 400)
        return verb, resolved
    if verb == "keyevent":
        return "key", args[:1]
    raise NavError(f"unknown nav action: {action}")
