"""Contact sheet: a grid of screenshots in one image, so a burst can be read at a glance."""
import os


def contact_sheet(files, out, cols=5, cell_width=216):
    try:
        from PIL import Image
    except ImportError:
        raise RuntimeError("Pillow is needed for contact sheets: python3 -m pip install Pillow")
    if not files:
        raise RuntimeError("no images given")
    images = [Image.open(f).convert("RGB") for f in files]
    cell_height = int(images[0].height * cell_width / images[0].width)
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (min(cols, len(images)) * cell_width, rows * cell_height), "white")
    for i, im in enumerate(images):
        sheet.paste(im.resize((cell_width, cell_height)), ((i % cols) * cell_width, (i // cols) * cell_height))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    sheet.save(out)
    return out


def downscale(path, max_height, out=None):
    """Write a copy no taller than max_height (same file name with _s) and return its path."""
    from PIL import Image
    im = Image.open(path)
    if im.height <= max_height:
        return path
    out = out or os.path.splitext(path)[0] + "_s.png"
    im.resize((int(im.width * max_height / im.height), max_height)).save(out)
    return out
