"""
Locate and rectify a Cronto colour code in a rotated / perspective image.

Pure NumPy: no OpenCV/scipy. The strategy is geometry-light and robust for the
realistic case (a screenshot or photo where the colour code is the dominant
saturated-colour object):

  1. The red/green/blue dots are the only strongly-saturated pixels, so they
     localise the code even under rotation/clutter.
  2. Inside a region grown from that colour cloud, the outermost non-background
     pixels are the black border; fit its four edges and intersect them to
     obtain perspective-aware corners. A rotated rectangle is a fallback.
  3. A 4-point homography rectifies that quad to an axis-aligned square, which
     the normal `extract_grid` front-end then samples.

Correct rotation is *not* required here — the decoder tries all 8 orientations
and uses the CRC-24 as the oracle, so any consistent rectification suffices.
"""
from __future__ import annotations
from collections import deque
import numpy as np
from PIL import Image
from .errors import SamplingError
from .formats import CODE_POINTS, grid_format
from .geometry import BorderFrame, Point


def _rgb(path_or_img):
    im = path_or_img if isinstance(path_or_img, Image.Image) else Image.open(path_or_img)
    return np.asarray(im.convert("RGB")).astype(np.int32)


def _masks(a):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    mid = r + g + b - mx - mn                        # middle channel
    # Cronto dots are near-pure red / green / blue: one channel dominates and the
    # second channel is low. Requiring mid < 0.3*mx excludes orange/purple UI
    # (ING orange headers/buttons have mid/max ~0.39) which would otherwise
    # dominate the density-based localisation.
    colored = (mx > 90) & (mid < 0.3 * mx) & (mx - mn > 60)
    background = (mn > 150)                           # near-white page
    return colored, background


def dense_region_bbox(a, thresh=0.22):
    """Bounding box (x0,x1,y0,y1) of the densest cluster of colour dots.

    The code is a compact grid of many dots; sparse coloured UI/text (blue
    account digits, buttons) is low-density and thus excluded. Works by tiling
    the colour mask into cells, keeping high-density cells, and taking the
    largest 4-connected component."""
    colored, _ = _masks(a)
    if colored.sum() < 50:
        return None
    H, W = colored.shape
    cell = max(6, min(H, W) // 80)
    gh, gw = H // cell, W // cell
    if gh < 2 or gw < 2:
        return None
    dens = colored[:gh * cell, :gw * cell].reshape(gh, cell, gw, cell).sum((1, 3))
    high = dens > thresh * cell * cell
    if not high.any():
        return None
    lab = -np.ones((gh, gw), int)
    best = None
    cur = 0
    for i in range(gh):
        for j in range(gw):
            if high[i, j] and lab[i, j] < 0:
                q = deque([(i, j)])
                lab[i, j] = cur
                pix: list[tuple[int, int]] = []
                while q:
                    y, x = q.popleft()
                    pix.append((y, x))
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < gh and 0 <= nx < gw and high[ny, nx] and lab[ny, nx] < 0:
                            lab[ny, nx] = cur
                            q.append((ny, nx))
                if best is None or len(pix) > len(best):
                    best = pix
                cur += 1
    ys = [p[0] for p in best]
    xs = [p[1] for p in best]
    return (min(xs) * cell, (max(xs) + 1) * cell - 1,
            min(ys) * cell, (max(ys) + 1) * cell - 1)


def crop_candidates(a, base_img):
    """Axis-aligned crops around the code's dense dot region (for cluttered but
    upright images, e.g. a screenshot with a code embedded in a page)."""
    bb = dense_region_bbox(a)
    if bb is None:
        return
    x0, x1, y0, y1 = bb
    w, h = x1 - x0 + 1, y1 - y0 + 1
    W, H = base_img.size
    for frac in (0.18, 0.26, 0.34):
        mx_, my_ = int(frac * w) + 6, int(frac * h) + 6
        box = (max(0, x0 - mx_), max(0, y0 - my_),
               min(W, x1 + mx_ + 1), min(H, y1 + my_ + 1))
        yield base_img.crop(box), (int(box[0]), int(box[1]), int(box[2]) - 1, int(box[3]) - 1)


def find_quad_corners(a):
    """Return corner-quad candidates: a list of [TL, TR, BR, BL] (x, y) sets for
    the code's black border, or None if no colour cloud is found.

    The dot cloud locates the code centre; the black border (which extends well
    beyond the dots, especially when the code is rotated) is then found among the
    dark pixels within a radius of that centre — large enough to include the
    whole border, small enough to reject distant clutter (page text etc.)."""
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    bb = dense_region_bbox(a)
    if bb is None:
        return None
    x0, x1, y0, y1 = bb
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    # dense-region half-diagonal -> radius that includes the border, excludes clutter
    half_diag = 0.5 * np.hypot(x1 - x0, y1 - y0)
    radius = 1.6 * half_diag + 12

    gray = (299 * r + 587 * g + 114 * b) // 1000
    dark = gray < 110
    Y, X = np.where(dark)
    if X.size:
        keep = (X - cx) ** 2 + (Y - cy) ** 2 <= radius ** 2
        X, Y = X[keep], Y[keep]
    if X.size < 50:                       # no clear dark border (e.g. faint photo)
        colored, _ = _masks(a)            # fall back to the colour dots themselves
        Y, X = np.where(colored)
    if X.size < 50:
        return None

    # The rectangle supplies a rotated frame. Fit the four edges independently
    # so converging perspective edges are preserved; keep the rectangle as fallback.
    P = np.column_stack([X.astype(float), Y.astype(float)])
    frame = BorderFrame.estimate(P)
    candidates: list[list[Point]] = []
    try:
        candidates.append(list(frame.quadrilateral(P)))
    except SamplingError:
        pass  # Rounded/occluded edges may not support a stable four-line fit.
    candidates.append(list(frame.rectangle()))
    return candidates


def _homography(dst, src):
    """3x3 H mapping dst -> src (so a destination pixel samples the source).
    dst, src: lists of 4 (x, y)."""
    A = []
    bvec = []
    for (u, v), (x, y) in zip(dst, src):
        A.append([u, v, 1, 0, 0, 0, -u * x, -v * x])
        bvec.append(x)
        A.append([0, 0, 0, u, v, 1, -u * y, -v * y])
        bvec.append(y)
    try:
        h = np.linalg.solve(np.asarray(A, float), np.asarray(bvec, float))
    except np.linalg.LinAlgError as error:
        raise SamplingError("degenerate corner homography") from error
    return np.array([[h[0], h[1], h[2]],
                     [h[3], h[4], h[5]],
                     [h[6], h[7], 1.0]])


def warp_to_square(a, corners, size=360, margin_frac=0.07):
    """Rectify the quad `corners` [TL,TR,BR,BL] to a `size`x`size` RGB image,
    with the quad inset by `margin_frac` so a white page margin surrounds the
    code (matching the clean-scan layout `extract_grid` expects). Pixels in the
    margin sample the page just outside the detected border."""
    if a.shape[0] < 2 or a.shape[1] < 2 or size < 2 or not 0 <= margin_frac < 0.5:
        raise SamplingError("invalid image dimensions or homography margin")
    m = int(margin_frac * size)
    lo, hi = m, size - 1 - m
    dst = [(lo, lo), (hi, lo), (hi, hi), (lo, hi)]
    Hm = _homography(dst, corners)
    u, v = np.meshgrid(np.arange(size), np.arange(size))
    u = u.ravel().astype(float)
    v = v.ravel().astype(float)
    den = Hm[2, 0] * u + Hm[2, 1] * v + Hm[2, 2]
    den = np.where(np.abs(den) < 1e-9, 1e-9, den)
    sx = np.nan_to_num((Hm[0, 0] * u + Hm[0, 1] * v + Hm[0, 2]) / den)
    sy = np.nan_to_num((Hm[1, 0] * u + Hm[1, 1] * v + Hm[1, 2]) / den)
    Hs, Ws = a.shape[:2]
    x0 = np.clip(np.floor(sx).astype(int), 0, Ws - 2)
    y0 = np.clip(np.floor(sy).astype(int), 0, Hs - 2)
    fx = np.clip(sx - x0, 0, 1)[:, None]
    fy = np.clip(sy - y0, 0, 1)[:, None]
    p00 = a[y0, x0]
    p01 = a[y0, x0 + 1]
    p10 = a[y0 + 1, x0]
    p11 = a[y0 + 1, x0 + 1]
    top = p00 * (1 - fx) + p01 * fx
    bot = p10 * (1 - fx) + p11 * fx
    out = (top * (1 - fy) + bot * fy).reshape(size, size, 3)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def _sample_grid(a, x0, y0, px, py, n, relative=False, color_factor=1.3):
    from .grid import sample_lattice
    return sample_lattice(a, x0, y0, px, py, n, relative, color_factor, clip_centers=True)


def _colour_bbox(a: np.ndarray) -> tuple[int, int, int, int] | None:
    colored, _ = _masks(a)
    ys, xs = np.where(colored)
    if xs.size < 50:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def tight_grid_candidates(path_or_img, module_count=None, relative=False, color_factor=1.3):
    """Yield zero-offset grids over colour bounds for cheap early CRC screening.

    This is the same colour extent as the exhaustive borderless fallback, but
    without first exhausting all whole-image sizes, offsets and RS repairs.
    Clutter or perspective may spoil these guesses; callers retain fallbacks.
    """
    if module_count is not None:
        grid_format(module_count)
    sizes = (module_count,) if module_count is not None else CODE_POINTS
    a = _rgb(path_or_img)
    bbox = _colour_bbox(a)
    if bbox is None:
        return
    x0, y0, x1, y1 = bbox
    for size in sizes:
        px = (x1 - x0 + 1) / size
        py = (y1 - y0 + 1) / size
        yield _sample_grid(a, x0, y0, px, py, size, relative, color_factor), bbox


def borderless_grid_candidates(path_or_img, relative=False, color_factor=1.3, module_count=None):
    """Yield N x N symbol grids for a *borderless* code (no black frame / quiet
    zone — the grid fills the image, e.g. a tightly-cropped screenshot). Two
    extents are tried: the whole image (an exact tight crop, decodepixels-style)
    and the colour bounding box (a crop with a white margin); each is sampled at
    every N with a sub-module offset scan for edge alignment. CRC-24 downstream is
    the oracle. `relative`/`color_factor` select the colour classifier."""
    if module_count is not None:
        grid_format(module_count)
    sizes = (module_count,) if module_count is not None else CODE_POINTS
    a = _rgb(path_or_img)
    H, W = a.shape[:2]
    extents = [(0, 0, W - 1, H - 1)]                 # whole image = the code (tight crop)
    bbox = _colour_bbox(a)
    if bbox is not None:                             # + colour bounding box (crop w/ margin)
        extents.append(bbox)
    for x0, y0, x1, y1 in extents:
        for n in sizes:
            px = (x1 - x0 + 1) / n
            py = (y1 - y0 + 1) / n
            for oxf in (0.0, -0.5, 0.5):
                for oyf in (0.0, -0.5, 0.5):
                    grid = _sample_grid(a, x0 + oxf * px, y0 + oyf * py, px, py, n,
                                        relative, color_factor)
                    yield grid, (int(x0), int(y0), int(x1), int(y1))


def localize_candidates(path_or_img, size=360):
    """Yield candidate images to decode from a cluttered / rotated / skewed input.

    Axis-aligned crops around the dot cloud are tried first (cheap; handles a
    code embedded in a page). Then perspective rectifications of the located
    border quad (handles rotation / perspective). CRC-24 downstream selects a
    good one; a few margins are tried because the rounded border corners inset
    the detected quad by an unknown amount."""
    a = _rgb(path_or_img)
    im = path_or_img if isinstance(path_or_img, Image.Image) else Image.open(path_or_img).convert("RGB")

    yield from crop_candidates(a, im)               # yields (warped_img, source_bbox)

    quads = find_quad_corners(a)
    if quads is None:
        return
    seen = set()
    for corners in quads:
        # shoelace area; skip degenerate quads (near-collinear/duplicate corners)
        pts = corners + [corners[0]]
        area = abs(sum(pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]
                       for i in range(4))) / 2
        key = tuple(corners)
        if area < 400 or key in seen:
            continue
        seen.add(key)
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        cbbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
        for margin in (0.07, 0.12):
            try:
                warped = warp_to_square(a, corners, size, margin_frac=margin)
            except SamplingError:
                continue  # Reject this degenerate transform, then try other candidates.
            yield warped, cbbox
