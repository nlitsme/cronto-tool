"""
Cronto symbol stage: N x N colour-symbol grid -> packed byte stream.

Ports ImageDecoder::removeFunctionSquares + convertTo8BitSymbols from the
native decoder (non-crontolib.c lines 268756-268786, 269126-269192).

The grid holds one 2-bit symbol per module (0=white,1=red,2=green,3=blue).
57 modules are finder/registration ("function") patterns and are dropped in a
fixed row-major run pattern; the remaining (N*N-57) symbols are packed
MSB-first, 4 per byte.
"""
from __future__ import annotations
import numpy as np
from collections.abc import Iterator
from .formats import CODE_POINTS, N_FUNCTION_MODULES, grid_format


def validate_grid(grid: np.ndarray) -> None:
    if not isinstance(grid, np.ndarray) or grid.ndim != 2 or grid.shape[0] != grid.shape[1]:
        raise ValueError("grid must be a square two-dimensional numpy array")
    grid_format(grid.shape[0])
    if not np.issubdtype(grid.dtype, np.integer) or np.any((grid < 0) | (grid > 3)):
        raise ValueError("grid symbols must be integers in 0..3")


def _l1(n): return (n + 1) // 2
def _l2(n): return n - _l1(n)


def function_square_runs(n):
    """The 16 (skip, copy_len) runs of removeFunctionSquares for grid size n.

    `skip` cells are function/finder modules dropped before each run; `copy_len`
    data cells follow. Sum of copy_len == n*n - 57 for every valid n.
    """
    grid_format(n)
    l1, l2 = _l1(n), _l2(n)
    return [
        (3, l1 - 5),
        (3, l2 - 4),
        (6, l1 - 5),
        (3, l2 - 3),
        (5, (l1 - 4) * n - 3),
        (2, l1 - 4),
        (3, l2 - 3),
        (4, l1 - 4),
        (3, l2 - 3),
        (4, l1 - 4),
        (3, l2 - 3),
        (2, (l2 - 3) * n),
        (2, l1 - 4),
        (3, l2 - 3),
        (5, l1 - 5),
        (3, l2 - 4),
    ]


def remove_function_squares(grid):
    """Return the (N*N-57) data symbols in reading order, function modules removed."""
    validate_grid(grid)
    n = grid.shape[0]
    flat = grid.reshape(-1)
    out = []
    pos = 0
    for skip, ln in function_square_runs(n):
        pos += skip
        if ln < 0:
            raise ValueError(f"bad run length {ln} for n={n}")
        out.extend(int(flat[pos + k]) for k in range(ln))
        pos += ln
    return out


def pack_symbols(symbols):
    """4 two-bit symbols -> 1 byte, first symbol in the MSBs."""
    if len(symbols) % 4 != 0:
        # The native code iterates i += 4 and would read past the end. Reject
        # incomplete groups explicitly; no undecoded symbols are discarded.
        raise ValueError("symbol count must be a multiple of four")
    if any(not isinstance(value, (int, np.integer)) or not 0 <= value <= 3 for value in symbols):
        raise ValueError("symbols must be integers in 0..3")
    out = bytearray()
    for i in range(0, len(symbols), 4):
        out.append((symbols[i] << 6) | (symbols[i + 1] << 4)
                   | (symbols[i + 2] << 2) | symbols[i + 3])
    return bytes(out)


def unpack_symbols(data):
    """Inverse of pack_symbols: each byte -> 4 two-bit symbols, first in the MSBs."""
    out = []
    for byte in data:
        out += [(byte >> 6) & 3, (byte >> 4) & 3, (byte >> 2) & 3, byte & 3]
    return out


def grid_to_bytes(grid):
    """Full symbol stage: N x N grid -> packed codeword bytes."""
    return pack_symbols(remove_function_squares(grid))


def data_byte_positions(n, cells):
    """Codeword byte indices affected by the given grid cells, for pairing with
    `rs.correct_codeword_erasures` (erasure decoding).

    `cells` are flat indices (row*n + col) in the **canonical** orientation — the
    same frame `grid_to_bytes` reads (transpose the display grid first if needed).
    Function/registration cells carry no data and are ignored. Each data cell is
    one 2-bit symbol; 4 symbols pack MSB-first into a byte, so a cell at data-order
    index i lands in byte i // 4."""
    func = set(function_positions(n))
    data_positions = {p: i for i, p in enumerate(p for p in range(n * n) if p not in func)}
    return sorted({data_positions[p] // 4 for p in cells if p in data_positions})


def function_positions(n):
    """Flat indices (row-major, canonical orientation) of the 57 function
    (registration) modules for grid size n."""
    data = set()
    pos = 0
    for skip, ln in function_square_runs(n):
        pos += skip
        for _ in range(ln):
            data.add(pos)
            pos += 1
    return sorted(set(range(n * n)) - data)


def data_positions(n: int) -> Iterator[int]:
    """Yield canonical data-cell indices in codeword order."""
    position = 0
    for skip, length in function_square_runs(n):
        position += skip
        yield from range(position, position + length)
        position += length


def codeword_to_grid(codeword: bytes, sampled_grid: np.ndarray) -> np.ndarray:
    """Replace data cells, preserving all sampled finder and variant cells."""
    validate_grid(sampled_grid)
    n = sampled_grid.shape[0]
    grid_format(n).validate_codeword(codeword)
    result = sampled_grid.copy()
    flat = result.reshape(-1)
    for position, symbol in zip(data_positions(n), unpack_symbols(codeword)):
        flat[position] = symbol
    return result


# Registration-square "dot" patterns (canonical orientation): the 57 function
# modules are white (symbol 0) except a few coloured dots, given as {flat index:
# symbol}. Symbols: 0=white 1=red 2=green 3=blue.
#
# The dot POSITIONS are derived from n (see `_finder_template`): a 3x3 lattice of
# registration squares at rows/cols {0, (n-1)/2, n-1}, with the top-left square's
# dot at its centre (1,1) as the orientation anchor. What is NOT derivable, and
# comes from real captures / the OneSpan Image Generator SDK 4.26.0, is per-size:
#   - a "second dot" some sizes add (bottom-right for 25 — which also carries the
#     version bit; top-right for 35/45);
#   - the two "size-marker" dots recoloured from blue to encode the grid size
#     (25 none/all-blue; 35 green; 45 red; 55 green).
# 35/45/55 confirmed against SDK output (two different-payload samples per size
# agree on all 57 function cells). 25 is the ING/Rabobank hardware format
# (generateCrontoSign, always 25x25); 35/45/55 are the Cronto Dynamic format
# (generateDynamicCrontoImage, not scannable by DP760/DP780).
#
# Cells handled below rather than here: 25's version bit (622) and issuer-variant
# dot (22), and 55's variant dot (52, the top-right second dot — blue in the SDK
# but white in captured real 55 codes, both decode). 35/45's homologous top-right
# second dot (32, 42) is kept as a fixed blue dot because only SDK samples exist
# for those sizes; reclassify if a real capture shows it varying.

def _finder_template(n):
    """Full {flat index: symbol} of the coloured registration dots for size n,
    generated from the grid width (see the note above)."""
    m = (n - 1) // 2
    P = lambda r, c: r * n + c
    t = {P(1, 1): 3,                               # top-left square: centre dot (anchor)
         P(0, m): 3, P(0, n - 1): 3,               # top-mid, top-right corner
         P(m, 0): 3, P(m, m): 3, P(m, n - 1): 3,   # mid row: left, centre, right
         P(n - 1, 0): 3, P(n - 1, m): 3, P(n - 1, n - 1): 3}  # bottom row
    extra = {25: P(n - 1, n - 3), 35: P(0, n - 3), 45: P(0, n - 3)}.get(n)
    if extra is not None:                          # 55's (0,n-3) is a VARIANT cell, not fixed
        t[extra] = 3
    marker = {35: (2, (P(1, 1), P(n - 1, n - 1))),         # green: TL centre + BR corner
              45: (1, (P(1, 1), P(n - 1, n - 1))),         # red:   TL centre + BR corner
              55: (2, (P(0, n - 1), P(n - 1, 0)))}.get(n)  # green: TR + BL corners
    if marker:
        colour, cells = marker
        for cell in cells:
            t[cell] = colour
    return t


FINDER_TEMPLATES = {n: _finder_template(n) for n in CODE_POINTS}

# Finder cells whose colour is the *version* indicator (it varies, so it is not a
# fixed template value). For size 25 the second bottom-right dot, cell (24,22)=622,
# is the `getVersion` version bit: blue -> version >= 1 (NPAR 50), white ->
# version 0 (NPAR 38). Keyed {size: {cell: {npar: expected symbol}}}. The size-25
# template above lists 622 as blue because the encoder emits the NPAR-50 variant.
VERSION_CELLS = {25: {622: {50: 3, 38: 0}}}

# Finder cells whose colour varies by issuer/variant (not by NPAR), so they are
# not part of the fixed template and are not flagged. For size 25, cell (0,22)=22
# — display (22,0), the left-edge mirror of the version bit — is white on ING /
# Rabobank but blue on APS Bank codes (both NPAR-50). For size 55, cell (0,52)=52
# is blue in OneSpan SDK 4.26.0 output but white in captured real-world 55 codes
# (both decode cleanly), so it varies the same way. Their exact meaning (a further
# version/customization flag) is unconfirmed.
VARIANT_CELLS = {25: {22}, 55: {52}}


_RGBW = "wrgb"   # symbol -> letter: 0=white 1=red 2=green 3=blue


def grid_to_rgbw(grid, display=True):
    """Render an N x N symbol grid as rows of r/g/b/w letters, function
    (registration) modules UPPERCASE and data modules lowercase.

    `grid` is the canonical (decoder-internal) orientation. display=True (default)
    transposes to the on-screen / rendered-image orientation (display = transpose
    of canonical); display=False keeps the internal frame."""
    validate_grid(grid)
    n = grid.shape[0]
    func = set(function_positions(n))
    chars = [[None] * n for _ in range(n)]
    for r in range(n):
        for c in range(n):
            ch = _RGBW[int(grid[r, c])]
            chars[r][c] = ch.upper() if (r * n + c) in func else ch
    if display:                                  # display[i][j] = canonical[j][i]
        chars = [[chars[c][r] for c in range(n)] for r in range(n)]
    return "\n".join("".join(row) for row in chars)


_RGBW_TO_SYM = {"w": 0, ".": 0, "r": 1, "g": 2, "b": 3}


def rgbw_to_grid(text):
    """Parse a manually-decoded grid (N lines of N letters) into an N x N symbol
    array (0=white,1=red,2=green,3=blue).

    Accepts w/r/g/b (case-insensitive, so the UPPERCASE function modules of
    `crontodecode --colors` are fine) and '.' for white (the `--grid` / grid_to_str
    form). Whitespace inside a line is ignored; blank lines are skipped. The
    orientation of the entered grid does not matter — the decoder tries all 8
    dihedral orientations with the CRC as oracle."""
    rows = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = "".join(raw.split())              # drop all whitespace
        if not line:
            continue
        row = []
        for col, ch in enumerate(line, 1):
            sym = _RGBW_TO_SYM.get(ch.lower())
            if sym is None:
                raise ValueError(f"line {lineno} col {col}: unexpected character "
                                 f"{ch!r} (expected w/r/g/b or '.')")
            row.append(sym)
        rows.append(row)
    if not rows:
        raise ValueError("empty grid")
    n = len(rows)
    if any(len(r) != n for r in rows):
        lens = sorted({len(r) for r in rows})
        raise ValueError(f"grid is not square: {n} rows but row length(s) {lens}")
    grid = np.array(rows, dtype=np.int8)
    validate_grid(grid)
    return grid


def expected_finder(n, npar=None):
    """Full {flat index: symbol} over the 57 function cells for size n (0 = white
    where there is no dot), or None if the finder pattern for n is unknown.

    Version-indicator cells (VERSION_CELLS) are resolved to the value implied by
    `npar` when given; if `npar` is unknown/None they are omitted from the map so
    the caller does not flag them (their value legitimately varies by version)."""
    if n not in FINDER_TEMPLATES:
        return None
    tpl = FINDER_TEMPLATES[n]
    exp = {p: tpl.get(p, 0) for p in function_positions(n)}
    for cell, by_npar in VERSION_CELLS.get(n, {}).items():
        if npar in by_npar:
            exp[cell] = by_npar[npar]
        else:
            exp.pop(cell, None)
    for cell in VARIANT_CELLS.get(n, ()):    # issuer-variant dots: never flagged
        exp.pop(cell, None)
    return exp


# --- grid orientation helpers (clean image may be rotated/flipped) ---

# The decoder works in the *canonical* orientation (the one grid_to_bytes reads,
# = the `rot270+flip` it selects); images/humans see the *display* orientation.
# The two are a fixed dihedral pair — canonical = displayᵀ across the main diagonal.

def to_display(grid):
    """Canonical (decoder-internal) orientation -> display (rendered-image)
    orientation. Inverse of to_canonical."""
    return np.rot90(np.fliplr(grid), 1)


def to_canonical(grid):
    """Display (rendered-image) orientation -> canonical (the frame grid_to_bytes
    reads). Inverse of to_display."""
    return np.ascontiguousarray(np.fliplr(np.rot90(grid, 3)))


def orientations(grid):
    """Yield (name, grid) for the 8 dihedral orientations."""
    g = grid
    for k in range(4):
        r = np.rot90(g, k)
        yield f"rot{90*k}", r
        yield f"rot{90*k}+flip", np.fliplr(r)


if __name__ == "__main__":
    # self-check: run-table totals
    for n in CODE_POINTS:
        runs = function_square_runs(n)
        skip = sum(s for s, _ in runs)
        copy = sum(c for _, c in runs)
        print(f"n={n:2d} L1={_l1(n)} L2={_l2(n)}  skip_total={skip} "
              f"copy_total={copy}  (n^2-57={n*n-57}) {'OK' if copy == n*n-57 else 'MISMATCH'}")
