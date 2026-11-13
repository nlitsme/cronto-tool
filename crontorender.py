#!/usr/bin/env python3
"""
crontorender — render a w/r/g/b colour grid back to a PNG.

Turns the textual grid you get from `crontodecode --colors` (or `--grid`, or a
hand-typed / decodepixels.py grid) into an image, so you can eyeball a decode
against the source photo — e.g. open both and flip between them to spot the
cells that differ.

Usage:
    python crontorender.py GRID.txt OUT.png              # 16 px/module, filled squares
    python crontorender.py - OUT.png                     # read the grid from stdin
    python crontorender.py --dots GRID.txt OUT.png       # centred dots (Cronto look)
    python crontorender.py --border GRID.txt OUT.png     # add black frame + quiet zone
    python crontorender.py --like olivia.jpg GRID.txt OUT.png   # match that image's exact
                                                                #   WxH so the two overlay

Input parsing is lenient: lines are stripped of whitespace, non-grid lines
(headers, indentation from --colors) are skipped, and w/r/g/b are case-insensitive
(so the UPPERCASE function modules of --colors are fine); '.' also means white.

Requires: pillow, numpy.
"""
import argparse
import re
import sys
from collections import Counter

from PIL import Image, ImageDraw

from cronto.symbols import rgbw_to_grid

# Vivid, well-separated palette (independent of the encoder's slightly-darker
# _COLORS) so symbol differences are obvious when flipping against a photo.
PALETTE = {0: (255, 255, 255), 1: (220, 20, 20), 2: (20, 175, 20), 3: (30, 30, 225)}


def extract_grid_text(text):
    """Keep only the colour-grid rows from arbitrary text (tolerates --colors
    headers / indentation): rows of w/r/g/b/. of the most common length."""
    rows = [re.sub(r"\s+", "", ln) for ln in text.splitlines()]
    rows = [ln for ln in rows if ln and re.fullmatch(r"[wrgbWRGB.]+", ln)]
    if not rows:
        raise SystemExit("no colour-grid rows (w/r/g/b/.) found in input")
    width = Counter(len(ln) for ln in rows).most_common(1)[0][0]
    return "\n".join(ln for ln in rows if len(ln) == width)


def render(grid, square=16, dots=False, border=False, size=None):
    n = grid.shape[0]
    if size is not None:                       # match a reference image exactly
        W, H = size
        img = Image.new("RGB", (W, H), (255, 255, 255))
        d = ImageDraw.Draw(img)
        for r in range(n):
            for c in range(n):
                v = int(grid[r, c])
                if v == 0:
                    continue
                x0, y0 = c * W / n, r * H / n
                x1, y1 = (c + 1) * W / n - 1, (r + 1) * H / n - 1
                if dots:
                    mx, my = (x1 - x0) / 6, (y1 - y0) / 6
                    d.ellipse([x0 + mx, y0 + my, x1 - mx, y1 - my], fill=PALETTE[v])
                else:
                    d.rectangle([x0, y0, x1, y1], fill=PALETTE[v])
        return img
    s = square
    quiet = 1 if border else 0
    frame = max(4, s) if border else 0
    total = n * s + 2 * quiet * s + 2 * frame
    img = Image.new("RGB", (total, total), (255, 255, 255))
    d = ImageDraw.Draw(img)
    if border:
        d.rounded_rectangle([0, 0, total - 1, total - 1], radius=frame, fill=(0, 0, 0))
        d.rounded_rectangle([frame, frame, total - 1 - frame, total - 1 - frame],
                            radius=max(2, frame // 2), fill=(255, 255, 255))
    off = frame + quiet * s
    for r in range(n):
        for c in range(n):
            v = int(grid[r, c])
            if v == 0:
                continue
            x0, y0 = off + c * s, off + r * s
            if dots:
                m = max(1, s // 6)
                d.ellipse([x0 + m, y0 + m, x0 + s - 1 - m, y0 + s - 1 - m], fill=PALETTE[v])
            else:
                d.rectangle([x0, y0, x0 + s - 1, y0 + s - 1], fill=PALETTE[v])
    return img


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render a w/r/g/b colour grid to a PNG.")
    ap.add_argument("grid", help="text file of w/r/g/b lines ('-' = stdin)")
    ap.add_argument("out", help="output PNG path")
    ap.add_argument("--square", "-s", type=int, default=16, help="pixels per module (default 16)")
    ap.add_argument("--dots", action="store_true", help="draw centred dots instead of filled squares")
    ap.add_argument("--border", action="store_true", help="add a black rounded frame + 1-module quiet zone")
    ap.add_argument("--like", metavar="IMAGE", default=None,
                    help="render at this image's exact WxH (borderless) so the two overlay/flip")
    args = ap.parse_args(argv)

    text = sys.stdin.read() if args.grid == "-" else open(args.grid, encoding="utf-8").read()
    grid = rgbw_to_grid(extract_grid_text(text))
    size = None
    if args.like:
        with Image.open(args.like) as ref:
            size = ref.size
    img = render(grid, square=args.square, dots=args.dots, border=args.border, size=size)
    img.save(args.out)
    print(f"wrote {args.out}  {img.width}x{img.height}px  ({grid.shape[0]}x{grid.shape[0]} grid"
          f"{', dots' if args.dots else ''}{', bordered' if args.border else ''}"
          f"{', matched to '+args.like if args.like else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
