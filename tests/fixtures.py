"""Original reference payload and syndrome helpers, shared by discovered tests."""
from __future__ import annotations

import numpy as np
from cronto.encoder import payload_to_grid, NPAR, _EXP, _LOG
from cronto.symbols import grid_to_bytes
from cronto.rs import block_layout


def rs_syndromes_zero(codeword, npar=NPAR):
    def mul(a, b):
        return 0 if (a == 0 or b == 0) else _EXP[_LOG[a] + _LOG[b]]
    for j in range(1, npar + 1):
        acc = 0
        for c in codeword:
            acc = mul(acc, _EXP[j]) ^ c
        if acc != 0:
            return False
    return True


def _canon_codeword(payload, size):
    """Codeword packed from the canonical-orientation grid, for syndrome checks."""
    return grid_to_bytes(np.ascontiguousarray(
        np.fliplr(np.rot90(payload_to_grid(payload, size=size), 3))))


def blocks_syndromes_zero(codeword, size, npar):
    """Zero RS syndromes for every interleaved block (sizes >25 are multi-block)."""
    off = 0
    for blen, par in block_layout(size, npar):
        if not rs_syndromes_zero(codeword[off:off + blen], par):
            return False
        off += blen
    return True


# Real OneSpan CRONTO *activation* code, size 55 (534-byte payload — the largest
# size, which this payload requires). Structure taken from a public OneSpan
# community-forum post (an MDL activation `activationPassword`): a nested-TLV
# device-provisioning descriptor listing DIGIPASS application slots. The
# per-device parts (encrypted activation challenge + token serial, bytes 2..96)
# are redacted here to 0x11 — the round-trip is independent of their content, and
# the generic application-slot TLVs ("SC ONLINE", "RO/CR/SG/SC OFFLINE",
# "ACTIVATION") are preserved so the test also exercises real container bytes.
ACTIVATION_55 = bytes.fromhex(
    "0000" + "11" * 94 +                                          # 00 00 + redacted 94-byte per-device head
    "0801030901010A01010B01010C01010E01010F01011001013801033C010146010A470101"
    "1147120100130101140101150C5343204F4E4C494E452020201601011704018314D01801041901101A01101B01101C01102101102201102301102401102901102A01002B01002C0102"
    "112F120100130101140102150C524F204F46464C494E45202016010117044080F0021801002901062A01002B01002C0102"
    "1135120100130101140103150C4352204F46464C494E45202016010117044183F4D21801011901062101062901062A01002B01002C0102"
    "115F120100130101140104150C5347204F46464C494E452020160101170401DDF4D21801081901001A01001B01001C01001D01001E01001F0100"
    "2001002101102201102301102401102501102601102701102801102901062A01002B01002C0102"
    "1147120100130101140105150C5343204F46464C494E4520201601011704018314D01801041901101A01101B01101C01102101102201102301102401102901062A01002B01002C0102"
    "3D35120100130101140101150C41435449564154494F4E20201601011704018314D01801011901102101102901102A01002B01002C0102")


