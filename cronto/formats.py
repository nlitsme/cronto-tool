"""Shared transport parameters and explicit contiguous Reed-Solomon layouts."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from numbers import Integral

N_FUNCTION_MODULES = 57
CRC_LEN = 3
# From ErrorCorrection::decode, non-crontolib.c:266824 (0x438 = 1080).
_MULTIBLOCK_THRESHOLD = 0x438


@dataclass(frozen=True)
class GridFormat:
    size: int
    parity_variants: tuple[int, ...]
    prefix_width: int

    @property
    def codeword_length(self) -> int:
        return (self.size * self.size - N_FUNCTION_MODULES) // 4

    @property
    def primary_parity(self) -> int:
        return self.parity_variants[0]

    def message_length(self, parity: int | None = None) -> int:
        return self.codeword_length - self.checked_parity(parity) - CRC_LEN

    def max_payload(self, parity: int | None = None) -> int:
        return self.message_length(parity) - self.prefix_width

    def checked_parity(self, parity: int | None) -> int:
        value = self.primary_parity if parity is None else parity
        if not isinstance(value, Integral) or isinstance(value, bool) or value not in self.parity_variants:
            raise ValueError(f"unsupported parity {value!r} for size {self.size}")
        return int(value)

    def validate_codeword(self, codeword: bytes) -> None:
        if len(codeword) != self.codeword_length:
            raise ValueError(f"size {self.size} requires exactly {self.codeword_length} codeword bytes; got {len(codeword)}")


FORMATS: dict[int, GridFormat] = {
    25: GridFormat(25, (50, 38), 1),
    35: GridFormat(35, (88,), 2),
    45: GridFormat(45, (136,), 2),
    55: GridFormat(55, (198, 336), 2),
}
CODE_POINTS = tuple(FORMATS)


def grid_format(size: int) -> GridFormat:
    if not isinstance(size, Integral) or isinstance(size, bool) or size not in FORMATS:
        raise ValueError(f"unsupported size {size!r}; choose one of {CODE_POINTS}")
    return FORMATS[int(size)]


@dataclass(frozen=True)
class RsBlockLayout:
    offset: int
    length: int
    parity: int

    @property
    def data_length(self) -> int:
        return self.length - self.parity

    @property
    def codeword_slice(self) -> slice:
        return slice(self.offset, self.offset + self.length)

    @property
    def data_slice(self) -> slice:
        return slice(self.offset, self.offset + self.data_length)

    def parity_positions(self) -> Iterator[int]:
        yield from range(self.offset + self.data_length, self.offset + self.length)


@dataclass(frozen=True)
class RsLayout:
    format: GridFormat
    total_parity: int
    blocks: tuple[RsBlockLayout, ...]

    @classmethod
    def for_size(cls, size: int, total_parity: int | None = None) -> RsLayout:
        spec = grid_format(size)
        parity = spec.checked_parity(total_parity)
        length = spec.codeword_length
        count = length // 255 + 1 if size * size > _MULTIBLOCK_THRESHOLD else 1
        if parity % count:
            raise ValueError("total parity does not divide evenly across RS blocks")
        base, remainder = divmod(length, count)
        blocks: list[RsBlockLayout] = []
        offset = 0
        for index in range(count):
            block_length = base + (remainder if index == 0 else 0)
            blocks.append(RsBlockLayout(offset, block_length, parity // count))
            offset += block_length
        return cls(spec, parity, tuple(blocks))

    def data_region(self, codeword: bytes) -> bytes:
        self.format.validate_codeword(codeword)
        return b"".join(codeword[block.data_slice] for block in self.blocks)

    def parity_positions(self) -> Iterator[int]:
        for block in self.blocks:
            yield from block.parity_positions()
