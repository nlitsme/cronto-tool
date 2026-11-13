"""Candidate scheduling regressions; test work performed rather than wall time."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from cronto import decoder
from cronto.decoder import Classifier, DecodeSession, decode_grid, decode_image
from cronto.encoder import payload_to_grid
from cronto.symbols import data_positions, function_positions, orientations, to_canonical

_REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ScreenshotSample:
    filename: str
    payload_digest: str

    def path(self) -> Path:
        return _REPO / "examples" / self.filename


class CandidateSearchTests(unittest.TestCase):
    def test_screenshots_decode_without_rs_repair_search(self) -> None:
        samples = (
            ScreenshotSample("banque-cramer-cronto.png", "4a8a238d62692d1d0bc38fb5a3103900c568eb9d95df634becddbd172881866a"),
            ScreenshotSample("apsbank.jpeg", "3590b5f75813696f4618990f9852829959ee49e83d9e1eb87f20064ca53d46bd"),
        )
        for sample in samples:
            with self.subTest(sample=sample.filename):
                with patch.object(decoder, "correct_codeword", side_effect=AssertionError("unnecessary RS repair")):
                    result = decode_image(sample.path(), debug=True)
                self.assertTrue(result.crc_ok and result.rs_ok and result.finder_ok)
                self.assertEqual(result.size, 25)
                self.assertEqual(result.errors_corrected, 0)
                self.assertEqual(sha256(result.payload).hexdigest(), sample.payload_digest)
                self.assertEqual(result.diagnostics[-1].source, "tight-colour")
                self.assertTrue(all(attempt.phase == "crc" for attempt in result.diagnostics))

    def test_clean_orientations_are_probed_before_repair(self) -> None:
        for name, grid in orientations(payload_to_grid(b"hello")):
            with self.subTest(orientation=name):
                with patch.object(decoder, "correct_codeword", side_effect=AssertionError("unnecessary RS repair")):
                    result = decode_grid(np.ascontiguousarray(grid))
                self.assertTrue(result.crc_ok)
                self.assertEqual(result.payload, b"hello")

    def test_failed_crc_probe_does_not_suppress_later_repair(self) -> None:
        canonical = to_canonical(payload_to_grid(b"hello"))
        canonical.reshape(-1)[next(data_positions(25))] ^= 1
        session = DecodeSession(25, "length", debug=True)
        classifier = Classifier(False, 1.3)
        probe = session.decode(canonical, "probe", classifier, repair=False)
        repaired = session.decode(canonical, "repair", classifier)
        self.assertFalse(probe.crc_ok)
        self.assertTrue(repaired.crc_ok)
        self.assertEqual(repaired.payload, b"hello")
        self.assertEqual(repaired.errors_corrected, 1)
        self.assertFalse(session.diagnostics[-1].reused)
        self.assertEqual(session.diagnostics[0].phase, "crc")
        self.assertEqual(session.diagnostics[1].phase, "repair")

    def test_successful_crc_probe_can_be_reused_by_repair_stage(self) -> None:
        grid = payload_to_grid(b"hello")
        session = DecodeSession(25, "length", debug=True)
        classifier = Classifier(False, 1.3)
        with patch.object(decoder, "decode_grid", wraps=decode_grid) as wrapped:
            probe = session.decode(grid, "probe", classifier, repair=False)
            repaired = session.decode(grid, "repair", classifier)
        self.assertEqual(wrapped.call_count, 1)
        self.assertTrue(session.diagnostics[-1].reused)
        self.assertEqual(probe.codeword, repaired.codeword)

    def test_damaged_finder_does_not_prune_rs_orientations(self) -> None:
        finder = {position: 0 for position in function_positions(25)}
        canonical = to_canonical(payload_to_grid(b"hello", finder=finder))
        canonical.reshape(-1)[next(data_positions(25))] ^= 1
        result = decode_grid(canonical)
        self.assertTrue(result.crc_ok and result.rs_ok)
        self.assertFalse(result.finder_ok)
        self.assertEqual(result.payload, b"hello")
        self.assertEqual(result.errors_corrected, 1)


if __name__ == "__main__":
    unittest.main()
