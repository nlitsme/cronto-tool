"""
Minimal, self-contained AES-128 (encryption only) + CTR keystream.

The Cronto native decoder descrambles the payload with AES-128 in CTR mode
using an all-zero key and all-zero IV/counter (crontolib.c: HAkzVUkcmmaeTTlXtKlM
called with key=0, iv=0, mode=CTR; egDOPlwIMmodlQfXVByw is the CTR core with a
big-endian counter starting at 0). The S-box and Rcon in the shipped
libQRCronto.so are the standard AES tables (verified by extraction).

Only encryption is needed: CTR keystream = AES_enc(key, counter) for
counter = 0, 1, 2, ...  With a zero key this keystream is a fixed constant.
"""
from __future__ import annotations
from functools import lru_cache
from numbers import Integral

# Standard AES S-box
_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16"
)

# Round constants
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _xtime(a):
    a <<= 1
    if a & 0x100:
        a ^= 0x11B
    return a & 0xFF


def _expand_key(key: bytes):
    if len(key) != 16:
        raise ValueError("AES-128 requires a 16-byte key")
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return w


def _encrypt_block(block: bytes, w) -> bytes:
    # state column-major: s[r][c]
    s = [[block[r + 4 * c] for c in range(4)] for r in range(4)]

    def add_round_key(rnd):
        for c in range(4):
            for r in range(4):
                s[r][c] ^= w[4 * rnd + c][r]

    add_round_key(0)
    for rnd in range(1, 10):
        # SubBytes
        for r in range(4):
            for c in range(4):
                s[r][c] = _SBOX[s[r][c]]
        # ShiftRows
        for r in range(1, 4):
            s[r] = s[r][r:] + s[r][:r]
        # MixColumns
        for c in range(4):
            a = [s[r][c] for r in range(4)]
            s[0][c] = _xtime(a[0]) ^ (_xtime(a[1]) ^ a[1]) ^ a[2] ^ a[3]
            s[1][c] = a[0] ^ _xtime(a[1]) ^ (_xtime(a[2]) ^ a[2]) ^ a[3]
            s[2][c] = a[0] ^ a[1] ^ _xtime(a[2]) ^ (_xtime(a[3]) ^ a[3])
            s[3][c] = (_xtime(a[0]) ^ a[0]) ^ a[1] ^ a[2] ^ _xtime(a[3])
        add_round_key(rnd)
    # final round (no MixColumns)
    for r in range(4):
        for c in range(4):
            s[r][c] = _SBOX[s[r][c]]
    for r in range(1, 4):
        s[r] = s[r][r:] + s[r][:r]
    add_round_key(10)

    return bytes(s[r][c] for c in range(4) for r in range(4))


_ZERO16 = b"\x00" * 16
@lru_cache(maxsize=8)
def _zero_keystream(blocks: int) -> bytes:
    # Cache complete immutable values. Concurrent cache misses may compute the
    # same value twice, but cannot interleave counters or corrupt future calls.
    w = _expand_key(_ZERO16)
    return b"".join(_encrypt_block(counter.to_bytes(16, "big"), w) for counter in range(blocks))


def ctr_keystream(n: int, key: bytes = _ZERO16, iv: bytes = _ZERO16) -> bytes:
    """CTR keystream of length n: AES_enc(key, counter) for counter=iv, iv+1, ...
    (big-endian 128-bit increment).

    The Cronto transport uses key=iv=0, for which the keystream is a fixed
    constant; complete immutable values are cached for transport-sized inputs
    (decode tries many orientations/attempts, each descrambling a codeword)."""
    if not isinstance(n, Integral) or isinstance(n, bool) or n < 0:
        raise ValueError("keystream length must be a nonnegative integer")
    if len(key) != 16 or len(iv) != 16:
        raise ValueError("AES-128-CTR requires a 16-byte key and IV")
    if key == _ZERO16 and iv == _ZERO16 and n <= 1024:
        return _zero_keystream((n + 15) // 16)[:n]
    w = _expand_key(key)
    out = bytearray()
    counter = int.from_bytes(iv, "big")
    while len(out) < n:
        out += _encrypt_block(counter.to_bytes(16, "big"), w)
        counter = (counter + 1) & ((1 << 128) - 1)
    return bytes(out[:n])


def ctr_crypt(data: bytes, key: bytes = b"\x00" * 16, iv: bytes = b"\x00" * 16) -> bytes:
    ks = ctr_keystream(len(data), key, iv)
    return bytes(a ^ b for a, b in zip(data, ks))


if __name__ == "__main__":
    # AES-128(key=0, block=0) == 66e94bd4ef8a2c3b884cfa59ca342b2e
    print(_encrypt_block(b"\x00" * 16, _expand_key(b"\x00" * 16)).hex())
