#!/usr/bin/env python3
"""Decode Cronto images or text grids; use --debug for candidate diagnostics."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys

from cronto.decoder import DecodeResult, decode_image, decode_grid
from cronto.grid import grid_to_str
from cronto.symbols import grid_to_rgbw, grid_to_bytes, orientations, rgbw_to_grid, function_positions
from cronto.formats import CODE_POINTS


@dataclass(frozen=True)
class DecodeOptions:
    size: int | None
    framing: str
    relative: bool
    factor: float
    from_text: bool
    debug: bool

    def decode(self, filename: str) -> DecodeResult:
        if self.from_text:
            text = sys.stdin.read() if filename == "-" else Path(filename).read_text(encoding="utf-8")
            grid = rgbw_to_grid(text)
            if self.size is not None and grid.shape[0] != self.size:
                raise ValueError(f"text grid size {grid.shape[0]} differs from --size {self.size}")
            return decode_grid(grid, framing=self.framing)
        return decode_image(filename, module_count=self.size, framing=self.framing,
                            relative=self.relative, color_factor=self.factor, debug=self.debug)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Decode a Cronto/photoTAN colour code.")
    parser.add_argument("images", nargs="+")
    parser.add_argument("--grid", action="store_true", help="print the sampled canonical grid in .RGB form")
    parser.add_argument("--colors", "--colours", action="store_true", dest="colors",
                        help="print the sampled display-orientation w/r/g/b grid; FUNCTION modules UPPERCASE")
    parser.add_argument("--corrected-colors", action="store_true", help="print the repaired display-orientation grid")
    parser.add_argument("--corrections", action="store_true", help="show repaired codeword-byte offsets and before/after values")
    parser.add_argument("--codewords", action="store_true", help="show raw codewords for all eight sampled-grid orientations")
    parser.add_argument("--from-text", "--text-grid", action="store_true", dest="from_text",
                        help="read N rows of w/r/g/b/. from files, or stdin ('-')")
    parser.add_argument("--relative", action="store_true", help="try the relative colour classifier first")
    parser.add_argument("--color-factor", "--colour-factor", type=float, default=1.3, dest="color_factor",
                        help="relative classifier dominance ratio, finite and >1 (default 1.3)")
    parser.add_argument("--size", type=int, choices=CODE_POINTS, help="force module count on every decode path")
    parser.add_argument("--framing", choices=("length", "full"), default="length",
                        help="length-prefixed ING/SDK (default), or full descrambled message (e.g. Rabobank)")
    parser.add_argument("--debug", action="store_true", help="write candidate diagnostics to stderr; retain unexpected tracebacks")
    return parser


def _print_location(result: DecodeResult, from_text: bool) -> None:
    # 1. Where in the original image the code was found; spot misidentified grids.
    width, height = result.image_size or (0, 0)
    if from_text:
        location = "manually-entered grid (no image)"
    elif result.field_bbox:
        x0, y0, x1, y1 = result.field_bbox
        coverage = 100 * (x1 - x0 + 1) * (y1 - y0 + 1) / (width * height) if width and height else 0
        method = "rectify/borderless" if result.rectified else "axis-aligned"
        location = f"field x{x0}–{x1} y{y0}–{y1} of {width}x{height} image ({coverage:.0f}% of area) [{method}]"
    else:
        location = f"{width}x{height} image" if width else "unknown"
    print(f"  location        : {location}")


def _print_symbol_mix(result: DecodeResult) -> None:
    # 2. Symbol mix over non-function modules; this is descriptive, not an
    # authenticity test. Encoded short payloads need not be uniformly distributed.
    function = set(function_positions(result.size))
    counts = [0, 0, 0, 0]
    for position, symbol in enumerate(result.grid.reshape(-1)):
        if position not in function:
            counts[int(symbol)] += 1
    total = sum(counts)
    percentages = tuple(100 * count / total for count in counts)
    print(f"  symbol mix      : w {percentages[0]:.0f}%  r {percentages[1]:.0f}%  "
          f"g {percentages[2]:.0f}%  b {percentages[3]:.0f}% ({total} sampled data modules)")


def _print_finder(result: DecodeResult) -> None:
    # Finder checks are advisory; display coordinates transpose canonical ones.
    if result.finder_ok is None:
        description = "not checked"
    elif result.finder_ok:
        description = "matches registration template"
    else:
        description = f"MISMATCH — {len(result.finder_mismatches)} checked function modules differ"
    print(f"  finder pattern  : {description}")
    names = ("white", "red", "green", "blue")
    differences = ", ".join(f"(r{position % result.size},c{position // result.size}) {names[observed]}≠{names[expected]}"
                            for position, observed, expected in result.finder_mismatches[:12])
    if differences:
        more = " …" if len(result.finder_mismatches) > 12 else ""
        print(f"                    {differences}{more}")


def _print_payload(result: DecodeResult) -> None:
    if result.framing == "full":
        framing = f"full descrambled message ({len(result.payload)} bytes)"
    else:
        padding = f"; synthesized {result.container.synthesized_padding} zero byte(s)" if not result.clean else ""
        framing = f"length-prefixed, length={result.length_prefix}{padding}"
    print(f"  framing         : {framing}")
    if result.container.trailing_bytes:
        tail = result.container.trailing_bytes
        print(f"  trailing bytes  : {len(tail)} after declared payload ({'all zero' if not any(tail) else tail.hex().upper()})")
    print(f"  payload ({len(result.payload)} bytes, AES-CTR descrambled):")
    print(f"    hex : {result.payload.hex().upper()}")
    printable = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in result.payload)
    print(f"    text: {printable}")


def _print_result(result: DecodeResult, from_text: bool) -> None:
    # Output follows the decode pipeline: locate -> sample -> orient -> integrity -> framing.
    _print_location(result, from_text)
    _print_symbol_mix(result)
    print(f"  orientation     : {result.orientation}")
    print(f"  grid size       : {result.size}x{result.size}")
    _print_finder(result)
    if result.errors_corrected:
        status = f"repaired {result.errors_corrected} codeword byte(s)"
    else:
        status = "zero syndromes" if result.rs_ok else "SYNDROMES NONZERO (symbol errors present)"
    print(f"  Reed-Solomon    : {result.npar} parity bytes   {status}")
    print(f"  integrity       : CRC-24 {'OK' if result.crc_ok else 'FAILED'}")
    _print_payload(result)


def _print_grids(result: DecodeResult, args: argparse.Namespace) -> None:
    if args.grid:
        print(grid_to_str(result.grid))
    if args.codewords:
        print("  sampled codewords (all orientations; before RS repair):")
        for name, grid in orientations(result.grid):
            print(f"    {name:12s}: {grid_to_bytes(grid).hex().upper()}")
    for enabled, title, grid in ((args.colors, "sampled", result.grid),
                                 (args.corrected_colors, "corrected", result.corrected_grid)):
        if enabled:
            print(f"  {title} colour grid (display orientation; FUNCTION modules UPPERCASE):")
            for line in grid_to_rgbw(grid).splitlines():
                print("    " + line)
    if args.corrections:
        print("  corrected codeword bytes (zero-based offsets):")
        for correction in result.corrections:
            print(f"    {correction.position:4d}: {correction.observed:02X} -> {correction.corrected:02X}")


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    options = DecodeOptions(args.size, args.framing, args.relative, args.color_factor, args.from_text, args.debug)
    exit_code = 0
    for filename in args.images:
        print(f"=== {filename} ===")
        try:
            result = options.decode(filename)
        except (OSError, ValueError) as error:
            if args.debug:
                raise
            print(f"  ERROR: {error}", file=sys.stderr)
            exit_code = 1
            continue
        _print_result(result, args.from_text)
        _print_grids(result, args)
        for attempt in result.diagnostics:
            print(f"  DEBUG {attempt.describe()}", file=sys.stderr)
        if not result.crc_ok:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
