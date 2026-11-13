"""
Cronto / photoTAN colour-code image front-end.

Turns a reasonably clean, axis-aligned image of a Cronto colour code into a
square matrix of colour symbols:

    0 = white (background)   1 = red   2 = green   3 = blue

The value mapping matches the native decoder's ImageDecoder::hsvColorDetection
(see tstrgb.py): white=0, red=1, green=2, blue=3.

This handles the "clean image" case (bank-rendered PNG/GIF, screenshot): the
code is upright, the black rounded border is intact, and modules sit on a
regular lattice. Camera photos with perspective/rotation are out of scope for
this module.
"""
from __future__ import annotations
import numpy as np
from PIL import Image
from .errors import SamplingError
from .formats import grid_format


def _load_rgb(path_or_img):
    if isinstance(path_or_img, np.ndarray):
        if path_or_img.ndim != 3 or path_or_img.shape[2] != 3 or not path_or_img.shape[0] or not path_or_img.shape[1]:
            raise SamplingError("expected a nonempty RGB image array")
        return path_or_img.astype(np.int32)
    if isinstance(path_or_img, Image.Image):
        im = path_or_img
    else:
        im = Image.open(path_or_img)
    return np.asarray(im.convert("RGB")).astype(np.int32)


def _classify(r, g, b):
    """RGB -> symbol int. Deliberately simple: the printed palette is
    saturated pure red/green/blue on a near-white field, so a max-channel
    rule is robust. (hsvColorDetection is only needed for camera noise.)"""
    mx = max(r, g, b)
    mn = min(r, g, b)
    if mx < 70:                     # black (border / bleed) -> treat as white
        return 0
    if mx - mn < 45 and mx > 110:   # low saturation, bright -> white
        return 0
    if r >= g and r >= b:
        return 1                    # red
    if g >= r and g >= b:
        return 2                    # green
    return 3                        # blue


def _classify_relative(r, g, b, factor=1.3):
    """Relative dominant-channel classifier: a cell is red/green/blue when that
    channel exceeds *both* others by `factor`x, else white.

    Robust to desaturated / colour-shifted captures (app screenshots, webp/JPEG
    recompression) where the absolute thresholds of `_classify` fail — what a
    printed pure-red module and a phone-rendered dim-red module share is that R
    still dominates. Ported from the user's decodepixels.py (`decodepixel2`)."""
    if r > factor * g and r > factor * b:
        return 1
    if g > factor * r and g > factor * b:
        return 2
    if b > factor * r and b > factor * g:
        return 3
    return 0


def sample_median_rgb(a, cx, cy, half):
    """Median (R, G, B) of the (2*half+1)-square patch centred at (cx, cy), or
    None if the patch is empty (sample point off-image).

    Clamps the window low with max(0, ...): a numpy slice whose start is negative
    (e.g. cx-half < 0) silently *wraps* to the far edge instead of clamping, which
    would sample the wrong side of the image. The high end needs no clamp (numpy
    truncates a past-the-end stop)."""
    if cy + half < 0 or cx + half < 0 or cy - half >= a.shape[0] or cx - half >= a.shape[1]:
        return None
    patch = a[max(0, cy - half):cy + half + 1, max(0, cx - half):cx + half + 1].reshape(-1, 3)
    if patch.size == 0:
        return None
    med = np.median(patch, axis=0)
    return int(med[0]), int(med[1]), int(med[2])


def _field_bbox(a):
    """Interior of the black rounded border (the code field, quiet zone included).

    Cronto codes are framed by a solid black rounded rectangle. We locate the
    frame as the rows/columns that are predominantly black and take the region
    just inside it. This is robust whether or not there is a white margin
    outside the frame (Rabobank vs ING renderings)."""
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    black = (r < 70) & (g < 70) & (b < 70)
    h, w = black.shape
    rb = black.sum(1)
    cb = black.sum(0)
    frame_rows = np.where(rb > 0.5 * w)[0]
    frame_cols = np.where(cb > 0.5 * h)[0]
    if len(frame_rows) and len(frame_cols):
        top = frame_rows[frame_rows < h // 2]
        bot = frame_rows[frame_rows > h // 2]
        lft = frame_cols[frame_cols < w // 2]
        rgt = frame_cols[frame_cols > w // 2]
        if len(top) and len(bot) and len(lft) and len(rgt):
            return lft.max() + 1, rgt.min() - 1, top.max() + 1, bot.min() - 1
    # fallback: bounding box of bright pixels
    white = (r > 150) & (g > 150) & (b > 150)
    ys = np.where(white.any(1))[0]
    xs = np.where(white.any(0))[0]
    if xs.size == 0 or ys.size == 0:              # no bright pixels -> use the whole frame
        return 0, w - 1, 0, h - 1
    return int(xs[0]), int(xs[-1]), int(ys[0]), int(ys[-1])


def _estimate_pitch(colored):
    """Estimate module pitch (px) from the autocorrelation of the
    coloured-pixel column/row projection."""
    def one(prof):
        prof = prof.astype(float)
        if prof.size == 0 or not prof.any():        # degenerate/empty field
            return None                             # (avoids mean-of-empty / div-by-zero)
        prof = prof - prof.mean()
        ac = np.correlate(prof, prof, "full")[len(prof) - 1:]
        if len(ac) == 0 or ac[0] <= 0:              # constant profile -> no periodicity
            return None
        best = None
        for lag in range(3, 60):
            if lag + 1 >= len(ac):
                break
            if ac[lag] > ac[lag - 1] and ac[lag] >= ac[lag + 1] and ac[lag] > 0.15 * ac[0]:
                best = lag
                break
        return best
    pc = one(colored.sum(0))
    pr = one(colored.sum(1))
    cands = [p for p in (pc, pr) if p]
    if not cands:
        raise SamplingError("could not estimate module pitch")
    return float(min(cands))


def extract_grid(path_or_img, module_count=None, quiet_modules=1, debug=False,
                 relative=False, color_factor=1.3, return_bbox=False):
    """Return an (N x N) numpy array of colour symbols (0..3).

    module_count : force the data-grid size N. If None, it is auto-detected
                   from the field size and pitch.
    quiet_modules: width of the white quiet zone (in modules) on each side,
                   which is stripped. Cronto uses 1.
    relative     : use `_classify_relative` (dominant-channel, tolerant of
                   desaturated / colour-shifted captures) instead of the absolute
                   `_classify`; `color_factor` is its dominance ratio.
    return_bbox  : if True, return (grid, (x0, y0, x1, y1)) where the tuple is the
                   located field's pixel bounding box in the image (for reporting
                   where the code was found).
    """
    if module_count is not None:
        grid_format(module_count)
    if not np.isfinite(color_factor) or color_factor <= 1:
        raise ValueError("color_factor must be finite and greater than 1")
    if not isinstance(quiet_modules, int) or quiet_modules < 0:
        raise ValueError("quiet_modules must be a nonnegative integer")
    a = _load_rgb(path_or_img)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    black = (r < 70) & (g < 70) & (b < 70)
    white = (r > 150) & (g > 150) & (b > 150)
    colored = ~black & ~white

    x0, x1, y0, y1 = _field_bbox(a)
    field_w = x1 - x0 + 1
    field_h = y1 - y0 + 1
    field_colored = colored[y0:y1 + 1, x0:x1 + 1]

    # Pitch is only needed to auto-detect the grid size (the field includes
    # `quiet_modules` white cells on each side). When the caller forces
    # module_count we skip it: pitch estimation can fail on a low-contrast field
    # even though the size is already known, and pitch_x/pitch_y below are derived
    # from module_count regardless.
    pitch = cells = None
    if module_count is None:
        pitch = _estimate_pitch(field_colored)
        cells = int(round(((field_w + field_h) / 2) / pitch))
        module_count = cells - 2 * quiet_modules
        try:
            grid_format(module_count)
        except ValueError as error:
            raise SamplingError(f"estimated unsupported grid size {module_count}") from error
    pitch_x = field_w / (module_count + 2 * quiet_modules)
    pitch_y = field_h / (module_count + 2 * quiet_modules)

    ox = x0 + pitch_x * quiet_modules
    oy = y0 + pitch_y * quiet_modules

    if debug:
        ps = "n/a" if pitch is None else f"{pitch:.2f}"
        print(f"field {field_w}x{field_h} pitch~{ps} cells~{cells} "
              f"-> N={module_count} pitchxy=({pitch_x:.2f},{pitch_y:.2f})")

    grid = sample_lattice(a, ox, oy, pitch_x, pitch_y, module_count, relative, color_factor)
    if return_bbox:
        return grid, (int(x0), int(y0), int(x1), int(y1))
    return grid


def sample_lattice(a: np.ndarray, x0: float, y0: float, px: float, py: float, n: int,
                   relative: bool = False, factor: float = 1.3, clip_centers: bool = False) -> np.ndarray:
    """Sample all interior patches together; preserve scalar clipping at edges."""
    half = max(1, int(min(px, py) // 4))
    xs = (x0 + px * (np.arange(n) + 0.5)).astype(int)
    ys = (y0 + py * (np.arange(n) + 0.5)).astype(int)
    height, width = a.shape[:2]
    if clip_centers:
        xs = np.clip(xs, 1, width - 2)
        ys = np.clip(ys, 1, height - 2)
    if xs.min() < half or ys.min() < half or xs.max() + half >= width or ys.max() + half >= height:
        return _sample_edge_lattice(a, xs, ys, half, relative, factor)
    delta = np.arange(-half, half + 1)
    rows = ys[:, None, None, None] + delta[None, None, :, None]
    cols = xs[None, :, None, None] + delta[None, None, None, :]
    medians = np.median(a[rows, cols], axis=(2, 3)).astype(np.int32)
    return _classify_rgb_grid(medians, relative, factor)


def _classify_rgb_grid(rgb: np.ndarray, relative: bool, factor: float) -> np.ndarray:
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    result = np.zeros(red.shape, dtype=np.int8)
    if relative:
        result[(red > factor * green) & (red > factor * blue)] = 1
        result[(green > factor * red) & (green > factor * blue)] = 2
        result[(blue > factor * red) & (blue > factor * green)] = 3
    else:
        maximum = rgb.max(axis=-1)
        minimum = rgb.min(axis=-1)
        colored = (maximum >= 70) & ~((maximum - minimum < 45) & (maximum > 110))
        result[colored] = np.argmax(rgb, axis=-1)[colored] + 1
    return result


def _sample_edge_lattice(a: np.ndarray, xs: np.ndarray, ys: np.ndarray, half: int,
                         relative: bool, factor: float) -> np.ndarray:
    result = np.zeros((len(ys), len(xs)), dtype=np.int8)
    for row, cy in enumerate(ys):
        for col, cx in enumerate(xs):
            rgb = sample_median_rgb(a, int(cx), int(cy), half)
            if rgb is not None:
                result[row, col] = _classify_relative(*rgb, factor) if relative else _classify(*rgb)
    return result


_SYM = ".RGB"


def grid_to_str(grid):
    return "\n".join("".join(_SYM[v] for v in row) for row in grid)


if __name__ == "__main__":
    import sys
    for fn in sys.argv[1:]:
        g = extract_grid(fn, debug=True)
        print(f"# {fn}: {g.shape[0]}x{g.shape[1]}")
        print(grid_to_str(g))
