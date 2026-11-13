"""Small explicit readers/writers for the transport framing fields."""
from __future__ import annotations
from numbers import Integral


class ByteReader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.offset

    def read(self, size: int) -> bytes:
        if not isinstance(size, Integral) or isinstance(size, bool) or size < 0 or size > self.remaining:
            raise ValueError(f"need {size} bytes at offset {self.offset}; only {self.remaining} available")
        start = self.offset
        self.offset += size
        return self.data[start:self.offset]

    def read_uint(self, width: int) -> int:
        return int.from_bytes(self.read(width), "big")


class ByteWriter:
    def __init__(self):
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    def write_uint(self, value: int, width: int) -> None:
        if not isinstance(value, Integral) or isinstance(value, bool) or not 0 <= value < 1 << (8 * width):
            raise ValueError(f"integer {value} does not fit in {width} bytes")
        self.write(int(value).to_bytes(width, "big"))

    def pad_to(self, length: int) -> None:
        if len(self.data) > length:
            raise ValueError(f"message exceeds capacity {length}")
        self.write(bytes(length - len(self.data)))

    def finish(self) -> bytes:
        return bytes(self.data)
