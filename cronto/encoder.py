"""
Cronto / photoTAN encoder — the inverse of the decoder.

Generates a valid size-25 colour code image from a payload, so it decodes back
to that payload with a valid CRC-24 and zero Reed-Solomon syndromes (i.e. a
code a genuine OneSpan/CrontoSign scanner should accept).

Inverse pipeline (see FORMAT.md):
    payload bytes
      -> plaintext = [len][payload][zero pad to 89]
      -> message   = AES-128-CTR(key=0, iv=0)         (XOR keystream; self-inverse)
      -> data      = message + CRC-24/OpenPGP(message)          (92 bytes)
      -> codeword  = data + RS parity                            (142 bytes)
      -> 2-bit symbols packed 4/byte, MSB-first
      -> placed into the 25x25 canonical grid (data cells) around the fixed
         57-module finder pattern
      -> de-canonicalised to display orientation and rendered

Reed-Solomon: systematic GF(2^8), primitive polynomial 0x11D, generator a=2,
roots a^1..a^NPAR (recovered from real codewords — every real code has zero
syndromes under these parameters, and this encoder reproduces their parity).

Sizes: 25 is a single RS block (validated on real ING/Rabo codes). 35/45/55
exceed the 255-byte GF(2^8) limit, so the codeword is split into 2/2/3 contiguous
RS blocks with parity divided evenly (see rs.block_layout / FORMAT.md §2.5); this
encoder builds the same block layout, and real 55x55 OneSpan activation codes
decode back to a valid CRC-24 under it. Finder templates are known for all four
sizes. Only 25x25 is the hardware-authenticator format. See FORMAT.md §6.
"""
from __future__ import annotations
import numpy as np
from PIL import Image, ImageDraw
from numbers import Integral

from .aes import ctr_crypt
from .rs import crc24, block_layout
from .formats import FORMATS, RsLayout, grid_format
from .framing import FramedMessage
from .binary import ByteReader, ByteWriter
from .symbols import (function_square_runs, N_FUNCTION_MODULES, FINDER_TEMPLATES,
                      unpack_symbols, to_display, data_positions, function_positions, validate_grid, VERSION_CELLS)
from .gf import EXP as _EXP, LOG as _LOG, mul as _mul   # GF(2^8), poly 0x11D, a=2

# --- GF(2^8) Reed-Solomon (poly 0x11D, a=2, roots a^1..a^NPAR) ---
# Primary parity-byte count per grid size (the "version >= 1" variant for 25).
NPAR_BY_SIZE = {size: spec.primary_parity for size, spec in FORMATS.items()}
NPAR = NPAR_BY_SIZE[25]                        # size-25 default (kept for imports)


def _gen_poly(npar):
    g = [1]
    for j in range(1, npar + 1):
        ng = [0] * (len(g) + 1)
        r = _EXP[j]
        for i, c in enumerate(g):
            ng[i] ^= c
            ng[i + 1] ^= _mul(c, r)
        g = ng
    return g


_G_CACHE = {}


def _gen(npar):
    if npar not in _G_CACHE:
        _G_CACHE[npar] = _gen_poly(npar)
    return _G_CACHE[npar]


_G = _gen(NPAR)                                # size-25 generator (kept for imports)


def rs_parity(data, npar=NPAR):
    """Systematic RS parity (`npar` bytes) for the data block."""
    if not isinstance(npar, Integral) or isinstance(npar, bool) or not 1 <= npar <= 254:
        raise ValueError("RS parity must be an integer in 1..254")
    if len(data) + npar > 255:
        raise ValueError("RS data plus parity must fit in 255 bytes")
    G = _gen(npar)
    m = list(data) + [0] * npar
    for i in range(len(data)):
        c = m[i]
        if c:
            for j in range(1, len(G)):
                m[i + j] ^= _mul(G[j], c)
    return bytes(m[len(data):])


N = 25                                    # default size (size-25 constants below)
CODEWORD_LEN = grid_format(N).codeword_length  # 142
DATA_LEN = CODEWORD_LEN - NPAR            # 92
MESSAGE_LEN = DATA_LEN - 3                # 89  (3 = CRC-24)
MAX_PAYLOAD = MESSAGE_LEN - 1            # 88  (1 = length prefix)


def _prefix_len(size):
    """Length-prefix width: 1 byte for size 25, else 2-byte big-endian."""
    return grid_format(size).prefix_width


def codeword_len(size):
    return grid_format(size).codeword_length


def message_len(size):
    return grid_format(size).message_length()


def max_payload(size):
    return grid_format(size).max_payload()


def _data_positions(size):
    # Compatibility for callers indexing this former private helper.
    return list(data_positions(size))


def payload_to_grid(payload: bytes, size: int = 25, raw_length: int | None = None,
                    framing: str = "length", finder: dict[int, int] | None = None,
                    crc_error: bool = False, rs_errors: int = 0, npar: int | None = None) -> np.ndarray:
    """Build the display-orientation symbol grid for a payload.

    size selects the grid (25/35/45/55). framing mirrors the decoder:
      "length" — prepend a length prefix (1 byte for size 25, else 2-byte
                 big-endian) and zero-pad to the full message (ING / mobile-SDK
                 container).
      "full"   — the payload IS the whole message (length-less container,
                 e.g. Rabobank); no length prefix.

    raw_length (length framing only) overrides the length prefix with an
    arbitrary value for robustness testing — the code still passes CRC-24 and RS
    but declares a length that may not match the payload.

    Fault-injection options (for testing how a scanner reacts):
      finder    — {flat canonical index: symbol} overriding individual finder
                  (registration) modules on top of the size's template.
      crc_error — flip a bit in the CRC-24 before RS-encoding, so the code has
                  valid Reed-Solomon (zero syndromes) but a *wrong CRC*.
      rs_errors — flip this many parity bytes, walking blocks from last to first, so the
                  code has *nonzero RS syndromes* (message + CRC left intact).
      npar      — select a supported parity variant; defaults to the primary.
    """
    layout = RsLayout.for_size(size, npar)
    spec = layout.format
    npar = layout.total_parity
    plaintext = FramedMessage.encode(payload, spec, framing, raw_length, npar).plaintext
    message = ctr_crypt(plaintext)                             # AES-CTR key=iv=0
    crc = crc24(message)
    crc_b = [(crc >> 16) & 0xFF, (crc >> 8) & 0xFF, crc & 0xFF]
    if crc_error:
        crc_b[2] ^= 0x01                                       # wrong CRC, RS still valid
    data = message + bytes(crc_b)
    # Reed-Solomon: single block for size 25, else contiguous multi-block with
    # parity split evenly (inverse of rs.data_region / ErrorCorrection::decode).
    codeword = _encode_blocks(data, layout)
    codeword = _inject_parity_errors(codeword, layout, rs_errors)
    symbols = unpack_symbols(codeword)

    canon = np.zeros(size * size, dtype=np.int8)
    finder_dots = dict(FINDER_TEMPLATES.get(size, {}))         # coloured finder dots (white elsewhere)
    for position, values in VERSION_CELLS.get(size, {}).items():
        finder_dots[position] = values[npar]
    if finder:
        _validate_finder(finder, size)
        finder_dots.update(finder)                            # per-cell overrides (canonical indices)
    for p, v in finder_dots.items():
        canon[p] = v
    for sym, p in zip(symbols, data_positions(size)):
        canon[p] = sym
    return to_display(canon.reshape(size, size))              # inverse of the decoder's canonicalisation


def _encode_blocks(data: bytes, layout: RsLayout) -> bytes:
    reader = ByteReader(data)
    writer = ByteWriter()
    for block in layout.blocks:
        chunk = reader.read(block.data_length)
        writer.write(chunk)
        writer.write(rs_parity(chunk, block.parity))
    if reader.remaining:
        raise ValueError(f"{reader.remaining} data bytes were not encoded")
    return writer.finish()


def _inject_parity_errors(codeword: bytes, layout: RsLayout, count: int) -> bytes:
    if not isinstance(count, Integral) or isinstance(count, bool) or not 0 <= count <= layout.total_parity:
        raise ValueError(f"rs_errors must be an integer in 0..{layout.total_parity}")
    damaged = bytearray(codeword)
    positions = tuple(layout.parity_positions())
    for position in reversed(positions[len(positions) - count:]):
        damaged[position] ^= 0xFF
    return bytes(damaged)


def _validate_finder(finder: dict[int, int], size: int) -> None:
    allowed = set(function_positions(size))
    for position, symbol in finder.items():
        if not isinstance(position, Integral) or isinstance(position, bool) or position not in allowed:
            raise ValueError(f"finder override {position!r} is not a registration cell")
        if not isinstance(symbol, Integral) or isinstance(symbol, bool) or not 0 <= symbol <= 3:
            raise ValueError(f"finder symbol {symbol!r} must be 0..3")


# Pure red/green/blue (middle channel 0) so the code is unambiguous both for the
# HSV colour detection and for the decoder's pure-colour localiser mask.
_COLORS = {0: (255, 255, 255), 1: (220, 0, 0), 2: (0, 176, 0), 3: (0, 0, 220)}


def render(grid: np.ndarray, pitch=16, quiet=1, border=16, radius=28) -> Image.Image:
    """Render a display-orientation grid as a bordered colour-code image."""
    validate_grid(grid)
    if pitch < 3 or quiet < 0 or border < 0 or radius < 0:
        raise ValueError("pitch must be >=3; quiet, border and radius must be nonnegative")
    n = grid.shape[0]
    field = n * pitch
    inner = field + 2 * quiet * pitch            # white field incl. quiet zone
    total = inner + 2 * border
    img = Image.new("RGB", (total, total), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, total - 1, total - 1], radius=radius, fill=(0, 0, 0))
    d.rounded_rectangle([border, border, total - 1 - border, total - 1 - border],
                        radius=max(4, radius - border // 2), fill=(255, 255, 255))
    ox = oy = border + quiet * pitch
    gap = max(1, pitch // 8)
    for r in range(n):
        for c in range(n):
            v = int(grid[r, c])
            if v == 0:
                continue
            x0 = ox + c * pitch + gap
            y0 = oy + r * pitch + gap
            d.rectangle([x0, y0, x0 + pitch - 2 * gap, y0 + pitch - 2 * gap],
                        fill=_COLORS[v])
    return img


def encode(payload_hex: str, size: int = 25, raw_length: int | None = None,
           framing: str = "length", finder: dict[int, int] | None = None,
           crc_error: bool = False, rs_errors: int = 0, npar: int | None = None, **render_kw) -> Image.Image:
    payload = bytes.fromhex(payload_hex.replace(" ", ""))
    return render(payload_to_grid(payload, size=size, raw_length=raw_length,
                                  framing=framing, finder=finder,
                                  crc_error=crc_error, rs_errors=rs_errors, npar=npar), **render_kw)
