"""GF(2^8) field tables for the Cronto Reed-Solomon layer.

Primitive polynomial 0x11D (x^8 + x^4 + x^3 + x^2 + 1), generator element alpha=2.
Shared by the RS parity generator (`encoder`) and the RS / errata decoder (`rs`).
"""
from __future__ import annotations

POLY = 0x11D
EXP = [0] * 512      # EXP[i] = alpha^i, doubled to 512 so LOG[a]+LOG[b] never wraps
LOG = [0] * 256      # LOG[alpha^i] = i
_x = 1
for _i in range(255):
    EXP[_i] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= POLY
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def mul(a, b):
    """GF(2^8) multiply."""
    return 0 if (a == 0 or b == 0) else EXP[LOG[a] + LOG[b]]
