"""
Cronto error-correction / integrity layer.

Ports the structure of VisualPin::Decoder::decodeForSize -> ErrorCorrection::decode
-> ErrorDetection::decode (non-crontolib.c, lines 266216-266966).

Layout of the packed codeword (size 25, the common Rabobank case):
    codeword = 142 bytes  = (25*25 - 57)/4
    systematic Reed-Solomon over GF(2^8) (rscode-style), DATA first, PARITY last:
        data   = codeword[:142 - NPAR]      (NPAR = 50 for the size-25 "version>=1"
                                             variant selected here; 38 otherwise)
        parity = codeword[142 - NPAR:]
    within `data`:
        message = data[:-3]                 (the RS-corrected message)
        crc     = data[-3:]                 (24-bit CRC over `message`)

Error CORRECTION (fixing wrong symbols) is only needed for camera captures; for
clean, bank-rendered images there are no symbol errors, so we verify integrity
via the CRC-24 and return the message. The CRC is the authoritative oracle that
the whole geometry/read-order/orientation chain is correct.
"""
from __future__ import annotations
import itertools
from numbers import Integral

from .gf import EXP as _GF_EXP, LOG as _GF_LOG, mul as _gf_mul   # GF(2^8), poly 0x11D, a=2
from .formats import FORMATS, CRC_LEN, N_FUNCTION_MODULES, RsLayout, grid_format

# Parity-byte counts. Size 25 chooses 50 or 38 based on a "version" bit read from
# the finder pattern (ImageDecoder::decode, line 267949); larger sizes use dword_D0048.
NPAR_BY_SIZE = {size: spec.parity_variants for size, spec in FORMATS.items()}

# CRC-24 (OpenPGP): poly 0x864CFB, init 0xB704CE, MSB-first, no reflection.
# Confirmed empirically: validates the size-25 message split on real samples.
_CRC_POLY = 0x864CFB
_CRC_INIT = 0xB704CE


def _crc24_table():
    t = []
    for i in range(256):
        c = i << 16
        for _ in range(8):
            c <<= 1
            if c & 0x1000000:
                c ^= _CRC_POLY
        t.append(c & 0xFFFFFF)
    return t


_CRC_T = _crc24_table()


def crc24(data: bytes) -> int:
    c = _CRC_INIT
    for b in data:
        c = (_CRC_T[(b ^ (c >> 16)) & 0xFF] ^ (c << 8)) & 0xFFFFFF
    return c


# Grids with N**2 above this use *multiple* interleaved Reed-Solomon blocks
# (GF(2^8) caps a single codeword at 255 bytes); at or below it, one block.
# From ErrorCorrection::decode, non-crontolib.c:266824 (0x438 = 1080).
# The threshold and parameters live in formats.RsLayout.


def block_layout(n: int, total_par: int):
    """(block_len, block_par) list for an n x n codeword with `total_par` parity.

    Mirrors ErrorCorrection::decode (non-crontolib.c:266820-266852): sizes with
    N**2 > 1080 split the byte codeword into ``len//255 + 1`` contiguous RS
    blocks, the first taking the remainder; parity is split evenly across blocks
    (rejected if it does not divide evenly). N=25 stays a single block.
    """
    layout = RsLayout.for_size(n, total_par)
    return [(block.length, block.parity) for block in layout.blocks]


def data_region(codeword: bytes, n: int, total_par: int):
    """Strip each RS block's trailing parity and concatenate the data portions.

    Returns the message+CRC region (data-first within each block). Invalid sizes,
    unsupported parity variants and non-exact codeword lengths raise ValueError.
    """
    return RsLayout.for_size(n, total_par).data_region(codeword)


def split_message(codeword: bytes, n: int, *, identify_variant: bool = True):
    """Return (message, npar, crc_ok) for a packed codeword of an n x n grid.

    Tries each candidate total NPAR for the size, de-interleaves the RS blocks,
    and returns the one whose trailing 3 data bytes are a valid CRC-24 of the
    preceding message. identify_variant=False skips syndrome-based fallback
    identification for cheap CRC-only probes; failed probes use primary NPAR.
    """
    spec = grid_format(n)
    spec.validate_codeword(codeword)
    for npar in spec.parity_variants:
        data = data_region(codeword, n, npar)
        if data is None or len(data) <= CRC_LEN:
            continue
        message = data[:-CRC_LEN]
        stored = int.from_bytes(data[-CRC_LEN:], "big")
        if crc24(message) == stored:
            return message, npar, True
    # No CRC match: a valid RS block can still identify an alternate variant
    # in a deliberately wrong-CRC code. Otherwise use the primary for inspection.
    npar = spec.primary_parity
    # Stronger RS codes also satisfy the weaker code's root subset. Prefer the
    # strongest zero-syndrome variant when CRC cannot distinguish them.
    if identify_variant:
        for candidate in sorted(spec.parity_variants, reverse=True):
            if syndromes_ok(codeword, n, candidate):
                npar = candidate
                break
    data = data_region(codeword, n, npar) or b""
    return data[:max(0, len(data) - CRC_LEN)], npar, False


def syndromes_ok(codeword: bytes, n: int, npar: int):
    """True if every interleaved RS block of the codeword has all-zero syndromes
    (no detectable symbol error), False if any block has a nonzero syndrome.
    Invalid size, parity or codeword length raises ValueError."""
    grid_format(n).validate_codeword(codeword)
    layout = block_layout(n, npar)
    off = 0
    for blen, par in layout:
        block = codeword[off:off + blen]
        off += blen
        for j in range(1, par + 1):
            acc = 0
            aj = _GF_EXP[j]
            for c in block:
                acc = _gf_mul(acc, aj) ^ c
            if acc != 0:
                return False
    return True


# --- Reed-Solomon error CORRECTION (Berlekamp-Massey + Chien + Forney) --------
# Repairs mis-read symbols in a systematic RS block (GF(2^8), poly 0x11D,
# generator alpha=2, first consecutive root alpha^1). Blocks are treated as
# shortened codewords of RS(255, .) — padded to 255 for the Chien search — so the
# per-block layouts here (<=255 bytes) decode correctly. Corrects up to
# floor(par/2) symbol errors per block; see correction_radius().

def _gf_pow(a, n):
    return 0 if a == 0 else _GF_EXP[(_GF_LOG[a] * n) % 255]


def _gf_inv(a):
    return _GF_EXP[255 - _GF_LOG[a]]


def _gf_poly_scale(p, x):
    return [_gf_mul(c, x) for c in p]


def _gf_poly_add(p, q):
    r = [0] * max(len(p), len(q))
    for i in range(len(p)):
        r[i + len(r) - len(p)] = p[i]
    for i in range(len(q)):
        r[i + len(r) - len(q)] ^= q[i]
    return r


def _gf_poly_mul(p, q):
    r = [0] * (len(p) + len(q) - 1)
    for j in range(len(q)):
        for i in range(len(p)):
            r[i + j] ^= _gf_mul(p[i], q[j])
    return r


def _gf_poly_eval(p, x):
    y = p[0]
    for c in p[1:]:
        y = _gf_mul(y, x) ^ c
    return y


def _rs_syndromes(msg, nsym, fcr=1):
    return [0] + [_gf_poly_eval(msg, _gf_pow(2, i + fcr)) for i in range(nsym)]


def _rs_error_locator(synd, nsym):
    err_loc, old_loc = [1], [1]
    shift = len(synd) - nsym
    for i in range(nsym):
        K = i + shift
        delta = synd[K]
        for j in range(1, len(err_loc)):
            delta ^= _gf_mul(err_loc[-(j + 1)], synd[K - j])
        old_loc = old_loc + [0]
        if delta != 0:
            if len(old_loc) > len(err_loc):
                new_loc = _gf_poly_scale(old_loc, delta)
                old_loc = _gf_poly_scale(err_loc, _gf_inv(delta))
                err_loc = new_loc
            err_loc = _gf_poly_add(err_loc, _gf_poly_scale(old_loc, delta))
    err_loc = list(itertools.dropwhile(lambda x: x == 0, err_loc))
    if (len(err_loc) - 1) * 2 > nsym:
        return None                       # more errors than the code can correct
    return err_loc


def _rs_find_errors(err_loc, nmess):
    errs = len(err_loc) - 1
    pos = []
    for i in range(255):                  # full field (shortened-code safe)
        if _gf_poly_eval(err_loc, _gf_pow(2, i)) == 0:
            exp = (255 - i) % 255          # root a^i = a^{-exp}; exp = error exponent
            j = (nmess - 1) - exp          # msg index (msg[0] = highest degree)
            if 0 <= j < nmess:
                pos.append(j)
    return pos if len(pos) == errs else None


def _rs_error_evaluator(synd, err_loc, nsym):
    r = _gf_poly_mul(synd, err_loc)
    return r[len(r) - (nsym + 1):]


def _rs_correct_errata(msg, synd, err_pos, fcr=1):
    coef_pos = [len(msg) - 1 - p for p in err_pos]
    e_loc = [1]
    for i in coef_pos:
        e_loc = _gf_poly_mul(e_loc, _gf_poly_add([1], [_gf_pow(2, i), 0]))
    e_eval = _rs_error_evaluator(synd[::-1], e_loc, len(e_loc) - 1)[::-1]
    X = [_gf_pow(2, c) for c in coef_pos]
    E = [0] * len(msg)
    for i, Xi in enumerate(X):
        Xi_inv = _gf_inv(Xi)
        prime = 1
        for j in range(len(X)):
            if j != i:
                prime = _gf_mul(prime, 1 ^ _gf_mul(Xi_inv, X[j]))
        if prime == 0:
            return None
        y = _gf_poly_eval(e_eval[::-1], Xi_inv)
        y = _gf_mul(_gf_pow(Xi, 1 - fcr), y)
        E[err_pos[i]] = _gf_mul(y, _gf_inv(prime))
    return _gf_poly_add(msg, E)


def correct_block(block, par):
    """Correct up to floor(par/2) symbol errors in a systematic RS block
    (block = data||parity, len <= 255). Returns (corrected_bytes, n_errors) or
    None if uncorrectable."""
    _validate_block(block, par)
    pad = 255 - len(block)
    msg = [0] * pad + list(block)                 # shortened RS -> pad to 255
    synd = _rs_syndromes(msg, par)
    if max(synd) == 0:
        return bytes(block), 0
    err_loc = _rs_error_locator(synd, par)
    if err_loc is None:
        return None
    pos = _rs_find_errors(err_loc, 255)
    if pos is None:
        return None
    fixed = _rs_correct_errata(msg, synd, pos)
    if fixed is None:
        return None
    if max(_rs_syndromes(fixed, par)) != 0:
        return None
    if any(fixed[:pad]):                          # correction leaked into padding
        return None
    return bytes(fixed[pad:]), len(pos)


def correct_codeword(codeword, n, npar):
    """Per-block RS-correct a full codeword for size n with total parity npar.
    Returns (corrected_codeword, total_errors) or None if any block is
    uncorrectable. Invalid size, parity or length raises ValueError."""
    grid_format(n).validate_codeword(codeword)
    layout = block_layout(n, npar)
    out = bytearray()
    off = 0
    total = 0
    for blen, par in layout:
        res = correct_block(codeword[off:off + blen], par)
        off += blen
        if res is None:
            return None
        cb, ne = res
        out += cb
        total += ne
    return bytes(out), total


def correction_radius(n, npar):
    """Per-block radius floor(par/2); invalid size or parity raises ValueError."""
    layout = block_layout(n, npar)
    return min(par // 2 for _, par in layout)


# --- errors-and-erasures decoding -------------------------------------------
# When the positions of some corrupt symbols are known in advance (e.g. an
# overlay that blanks a row+column of a captured code, or cells the front-end
# flags as unreadable), they can be passed as *erasures*. A systematic RS block
# with `par` parity symbols corrects `e` erasures plus `v` unknown errors as long
# as `2*v + e <= par` — so up to `par` erasures when the positions are known,
# versus only floor(par/2) errors when they are not. Standard errata decoding:
# Forney-modified syndromes feed Berlekamp-Massey for the unknown errors, then the
# known erasure locators are combined back in for Forney magnitude evaluation
# (Wikiversity, "Reed-Solomon codes for coders").

def _rs_forney_syndromes(synd, erase_msg, nmess):
    """Erasure-adjusted (Forney) syndromes: fold the known erasure locators out of
    the raw syndromes so Berlekamp-Massey then sees only the unknown errors.
    `erase_msg` are indices into the (255-padded) message; `synd` = [0,S1..Spar]."""
    fsynd = list(synd[1:])
    for p in erase_msg:
        x = _gf_pow(2, nmess - 1 - p)
        for j in range(len(fsynd) - 1):
            fsynd[j] = _gf_mul(fsynd[j], x) ^ fsynd[j + 1]
    return fsynd


def _rs_error_locator_erasures(fsynd, nsym, erase_count):
    """Berlekamp-Massey on Forney syndromes, solving only the (nsym-erase_count)
    unknown errors. Returns the (unknown-)error locator, or None if uncorrectable."""
    err_loc, old_loc = [1], [1]
    for i in range(nsym - erase_count):
        delta = fsynd[i]
        for j in range(1, len(err_loc)):
            delta ^= _gf_mul(err_loc[-(j + 1)], fsynd[i - j])
        old_loc = old_loc + [0]
        if delta != 0:
            if len(old_loc) > len(err_loc):
                new_loc = _gf_poly_scale(old_loc, delta)
                old_loc = _gf_poly_scale(err_loc, _gf_inv(delta))
                err_loc = new_loc
            err_loc = _gf_poly_add(err_loc, _gf_poly_scale(old_loc, delta))
    err_loc = list(itertools.dropwhile(lambda x: x == 0, err_loc))
    if (len(err_loc) - 1) * 2 + erase_count > nsym:
        return None
    return err_loc


def correct_block_errata(block, par, erasure_pos, fcr=1):
    """Correct a systematic RS block (block = data||parity, len <= 255) given the
    byte positions of known erasures (indices into `block`). Corrects `e` erasures
    plus `v` unknown errors while `2*v + e <= par`. Returns
    (corrected_bytes, n_symbols_corrected) or None if uncorrectable.

    The current (possibly wrong) values at the erasure positions are used as-is;
    only their positions matter — the decoder solves the true magnitudes."""
    _validate_block(block, par)
    erasure_pos = set(erasure_pos)
    _validate_positions(erasure_pos, len(block))
    erasure_pos = sorted(erasure_pos)
    if erasure_pos and (erasure_pos[0] < 0 or erasure_pos[-1] >= len(block)):
        raise ValueError("erasure position outside block")
    if len(erasure_pos) > par:
        return None
    pad = 255 - len(block)
    msg = [0] * pad + list(block)
    synd = _rs_syndromes(msg, par, fcr)
    if max(synd) == 0:
        return bytes(block), 0
    erase_msg = [pad + p for p in erasure_pos]
    fsynd = _rs_forney_syndromes(synd, erase_msg, 255)
    err_loc = _rs_error_locator_erasures(fsynd, par, len(erase_msg))
    if err_loc is None:
        return None
    err_pos = _rs_find_errors(err_loc, 255)                  # unknown-error indices
    if err_pos is None or 2 * len(err_pos) + len(erase_msg) > par:
        return None
    fixed = _rs_correct_errata(msg, synd, erase_msg + err_pos, fcr)
    if fixed is None or max(_rs_syndromes(fixed, par, fcr)) != 0 or any(fixed[:pad]):
        return None
    corrected = bytes(fixed[pad:])
    return corrected, sum(before != after for before, after in zip(block, corrected))


def correct_codeword_erasures(codeword, n, npar, erasures):
    """Per-block errors-and-erasures correction of a full codeword for size n.
    `erasures` are byte positions in the whole codeword whose values are known to
    be unreliable (they are de-interleaved to the contiguous RS blocks). Returns
    (corrected_codeword, total_symbols_corrected) or None if any block is
    uncorrectable. Invalid size, parity, length or erasure index raises ValueError."""
    grid_format(n).validate_codeword(codeword)
    layout = block_layout(n, npar)
    erasures = set(erasures)
    _validate_positions(erasures, len(codeword))
    out = bytearray()
    off = 0
    total = 0
    for blen, par in layout:
        local = [p - off for p in erasures if off <= p < off + blen]
        res = correct_block_errata(codeword[off:off + blen], par, local)
        if res is None:
            return None
        cb, ne = res
        out += cb
        total += ne
        off += blen
    return bytes(out), total


def _validate_block(block: bytes, par: int) -> None:
    if not 1 <= len(block) <= 255:
        raise ValueError("RS block length must be 1..255 bytes")
    if not isinstance(par, Integral) or isinstance(par, bool) or not 1 <= par <= min(254, len(block)):
        raise ValueError("parity must be an integer in 1..min(254, block length)")


def _validate_positions(positions: set[int], length: int) -> None:
    for position in positions:
        if not isinstance(position, Integral) or isinstance(position, bool) or not 0 <= position < length:
            raise ValueError(f"erasure position {position!r} outside codeword of length {length}")
