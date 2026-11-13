"""Container framing, including explicit preserved and synthesized padding."""
from __future__ import annotations

from dataclasses import dataclass

from .binary import ByteReader, ByteWriter
from .formats import GridFormat


def validate_framing(framing: str) -> None:
    if framing not in ("length", "full"):
        raise ValueError(f"unknown framing {framing!r} (use 'length' or 'full')")


@dataclass(frozen=True)
class FramedMessage:
    plaintext: bytes
    payload: bytes
    framing: str
    length_prefix: int
    trailing_bytes: bytes = b""  # Bytes after the declared payload; preserved even when nonzero.
    synthesized_padding: int = 0  # Bytes absent from the image, added to emulate copyOfRange.

    @property
    def clean(self) -> bool:
        return self.synthesized_padding == 0

    @classmethod
    def decode(cls, plaintext: bytes, spec: GridFormat, framing: str) -> FramedMessage:
        validate_framing(framing)
        reader = ByteReader(plaintext)
        length = reader.read_uint(spec.prefix_width)
        if framing == "full":
            return cls(plaintext, plaintext, framing, length)
        available = min(length, reader.remaining)
        payload = reader.read(available)
        missing = length - available
        trailing = reader.read(reader.remaining)
        return cls(plaintext, payload + bytes(missing), framing, length, trailing, missing)

    @classmethod
    def encode(cls, payload: bytes, spec: GridFormat, framing: str,
               raw_length: int | None = None, parity: int | None = None) -> FramedMessage:
        validate_framing(framing)
        writer = ByteWriter()
        capacity = spec.message_length(parity)
        if framing == "length":
            if len(payload) > spec.max_payload(parity):
                raise ValueError(f"payload too long: {len(payload)} > {spec.max_payload(parity)} bytes for size {spec.size}")
            length = len(payload) if raw_length is None else raw_length
            writer.write_uint(length, spec.prefix_width)
        elif raw_length is not None:
            raise ValueError("raw_length applies only to 'length' framing")
        writer.write(payload)
        writer.pad_to(capacity)
        return cls.decode(writer.finish(), spec, framing)
