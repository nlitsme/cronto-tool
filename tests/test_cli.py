"""CLI behavior, validation, diagnostic output and exit status."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import crontodecode
import crontoencode
from cronto.encoder import encode, payload_to_grid
from cronto.symbols import grid_to_rgbw, to_canonical


@dataclass(frozen=True)
class CommandResult:
    status: int
    stdout: str
    stderr: str


def invoke(main, arguments: tuple[str, ...]) -> CommandResult:
    output = StringIO()
    errors = StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        try:
            status = main(arguments)
        except SystemExit as error:
            status = int(error.code)
    return CommandResult(status, output.getvalue(), errors.getvalue())


class CliTests(unittest.TestCase):
    def test_invalid_size_is_rejected_by_argument_parser(self) -> None:
        result = invoke(crontodecode.main, ("--size", "24", "missing.png"))
        self.assertEqual(result.status, 2)
        self.assertIn("invalid choice", result.stderr)

    def test_malformed_hex_has_no_traceback(self) -> None:
        result = invoke(crontoencode.main, ("xyz", "unused.png"))
        self.assertEqual(result.status, 1)
        self.assertIn("error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_finder_override_must_be_a_registration_cell(self) -> None:
        result = invoke(crontoencode.main, ("00", "unused.png", "--finder-dot", "5,5=r"))
        self.assertEqual(result.status, 1)
        self.assertIn("registration cell", result.stderr)

    def test_negative_finder_coordinates_are_rejected_without_assertions(self) -> None:
        result = invoke(crontoencode.main, ("00", "unused.png", "--finder-dot=-1,0=r"))
        self.assertEqual(result.status, 1)
        self.assertIn("coordinates", result.stderr)

    def test_out_of_range_parity_fault_is_rejected(self) -> None:
        result = invoke(crontoencode.main, ("00", "unused.png", "--rs-error", "51"))
        self.assertEqual(result.status, 1)
        self.assertIn("0..50", result.stderr)

    def test_roundtrip_and_debug_output(self) -> None:
        with TemporaryDirectory() as directory:
            path = str(Path(directory) / "code.png")
            encoded = invoke(crontoencode.main, ("68656c6c6f", path, "--parity", "38"))
            decoded = invoke(crontodecode.main, ("--debug", "--colors", "--corrected-colors", "--corrections", path))
        self.assertEqual(encoded.status, 0)
        self.assertEqual(decoded.status, 0)
        self.assertIn("68656C6C6F", decoded.stdout)
        self.assertIn("sampled colour grid", decoded.stdout)
        self.assertIn("corrected colour grid", decoded.stdout)
        self.assertIn("DEBUG direct", decoded.stderr)

    def test_failed_crc_sets_failure_status(self) -> None:
        grid = to_canonical(payload_to_grid(b"hello", crc_error=True))
        text = grid_to_rgbw(grid)
        with patch("sys.stdin", StringIO(text)):
            result = invoke(crontodecode.main, ("--from-text", "-"))
        self.assertEqual(result.status, 1)
        self.assertIn("CRC-24 FAILED", result.stdout)

    def test_text_grid_honors_forced_size(self) -> None:
        text = grid_to_rgbw(to_canonical(payload_to_grid(b"hello")))
        with patch("sys.stdin", StringIO(text)):
            result = invoke(crontodecode.main, ("--from-text", "--size", "35", "-"))
        self.assertEqual(result.status, 1)
        self.assertIn("differs from --size", result.stderr)

    def test_missing_input_has_no_traceback(self) -> None:
        result = invoke(crontodecode.main, ("nonexistent-input.png",))
        self.assertEqual(result.status, 1)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
