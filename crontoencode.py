#!/usr/bin/env python3
"""Generate Cronto images, including explicit CRC, parity and finder faults.

Finder templates are known for every size. Only 25x25 is the hardware-token
format; larger sizes are Cronto Dynamic. See FORMAT.md for transport details.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import sys

from cronto.encoder import encode
from cronto.formats import CODE_POINTS, grid_format


@dataclass(frozen=True)
class FinderOverride:
    row: int
    column: int
    symbol: int

    @classmethod
    def parse(cls, text: str, size: int) -> FinderOverride:
        palette = {"w": 0, "r": 1, "g": 2, "b": 3}
        try:
            coordinates, color = text.split("=")
            row, column = (int(value) for value in coordinates.split(","))
            symbol = palette[color.strip().lower()]
        except (ValueError, KeyError) as error:
            raise ValueError(f"bad --finder-dot {text!r}; expected ROW,COL=w/r/g/b") from error
        if not 0 <= row < size or not 0 <= column < size:
            raise ValueError(f"finder coordinates outside {size}x{size} grid")
        return cls(row, column, symbol)

    def canonical_position(self, size: int) -> int:
        # Display(row,column) -> canonical(column,row).
        return self.column * size + self.row


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a Cronto/photoTAN colour code.")
    parser.add_argument("hexpayload", help="payload as a hex string")
    parser.add_argument("output", help="output PNG path")
    parser.add_argument("--size", type=int, choices=CODE_POINTS, default=25, help="modules per side (default 25)")
    parser.add_argument("--parity", type=int, help="supported parity variant for the chosen size (default: primary)")
    parser.add_argument("--framing", choices=("length", "full"), default="length",
                        help="length-prefixed ING/SDK (default), or whole message (e.g. Rabobank)")
    parser.add_argument("--raw-length", type=int, metavar="N", help="override the 1/2-byte length prefix for robustness testing")
    parser.add_argument("--finder-dot", action="append", default=[], metavar="R,C=COLOR",
                        help="override a registration cell in display coordinates; repeatable, COLOR=w/r/g/b")
    parser.add_argument("--bad-crc", action="store_true", help="inject a CRC error before computing valid RS parity")
    parser.add_argument("--rs-error", type=int, default=0, metavar="N",
                        help="corrupt N parity bytes, last block first; data and CRC stay intact")
    return parser


def _generate(args: argparse.Namespace) -> tuple[int, int]:
    spec = grid_format(args.size)
    parity = spec.checked_parity(args.parity)
    payload = bytes.fromhex(args.hexpayload)
    finder: dict[int, int] = {}
    for text in args.finder_dot:
        override = FinderOverride.parse(text, args.size)
        finder[override.canonical_position(args.size)] = override.symbol
    image = encode(args.hexpayload, size=args.size, npar=parity, framing=args.framing,
                   raw_length=args.raw_length, finder=finder or None, crc_error=args.bad_crc, rs_errors=args.rs_error)
    image.save(args.output)
    return image.width, len(payload)


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        width, length = _generate(args)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    notes: list[str] = []
    if args.raw_length is not None:
        notes.append(f"length-prefix forced to {args.raw_length}")
    if args.finder_dot:
        notes.append(f"{len(args.finder_dot)} finder override(s)")
    if args.bad_crc:
        notes.append("CRC error injected")
    if args.rs_error:
        notes.append(f"{args.rs_error} parity byte(s) corrupted")
    suffix = ", " + ", ".join(notes) if notes else ""
    print(f"wrote {args.output} ({width}x{width}), size {args.size}, {args.framing} framing, payload {length} bytes{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
