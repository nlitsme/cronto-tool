"""Regression tests for review findings, malformed data and correction provenance."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier
from random import Random
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from cronto import aes, decoder
from cronto.decoder import Classifier, DecodeResult, DecodeSession, _decode_one, decode_grid, decode_image
from cronto.encoder import encode, payload_to_grid
from cronto.errors import SamplingError
from cronto.formats import FORMATS, RsLayout
from cronto.framing import FramedMessage
from cronto.grid import _classify, _classify_relative, _classify_rgb_grid, sample_lattice, sample_median_rgb
from cronto.locate import _homography, borderless_grid_candidates
from cronto.rs import crc24, correct_block, correct_codeword, correct_codeword_erasures, data_region, split_message, syndromes_ok
from cronto.symbols import codeword_to_grid, data_positions, grid_to_bytes, pack_symbols, rgbw_to_grid, to_canonical
from crontorender import render as render_grid


@dataclass(frozen=True)
class PerspectiveCapture:
    corners: tuple[tuple[float, float], ...]
    dimensions: tuple[int, int] = (625, 605)

    def render(self, image: Image.Image) -> Image.Image:
        width, height = image.size
        source = ((0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1))
        matrix = _homography(self.corners, source)
        return image.transform(self.dimensions, Image.Transform.PERSPECTIVE, tuple(matrix.ravel()[:8]),
                               Image.Resampling.BICUBIC, fillcolor="white")


class AesTests(unittest.TestCase):
    def test_nonzero_key_known_answer(self) -> None:
        key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        block = bytes.fromhex("00112233445566778899aabbccddeeff")
        expected = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
        self.assertEqual(aes._encrypt_block(block, aes._expand_key(key)), expected)

    def test_concurrent_cache_misses_are_safe(self) -> None:
        original = aes._encrypt_block
        key = bytes(16)
        schedule = aes._expand_key(key)
        expected = b"".join(original(counter.to_bytes(16, "big"), schedule) for counter in range(4))
        barrier = Barrier(2)
        aes._zero_keystream.cache_clear()

        def synchronized(block: bytes, expanded: list[list[int]]) -> bytes:
            if block == bytes(16):
                barrier.wait(timeout=5)
            return original(block, expanded)

        with patch.object(aes, "_encrypt_block", side_effect=synchronized):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = tuple(pool.map(aes.ctr_keystream, (64, 64)))
        self.assertEqual(results, (expected, expected))
        self.assertEqual(aes.ctr_keystream(64), expected)

    def test_invalid_parameters(self) -> None:
        for length in (-1, 1.5, True):
            with self.subTest(length=length), self.assertRaises(ValueError):
                aes.ctr_keystream(length)
        for key, iv in ((b"", bytes(16)), (bytes(16), b"")):
            with self.subTest(key=key, iv=iv), self.assertRaises(ValueError):
                aes.ctr_keystream(16, key, iv)

    def test_counter_wrap(self) -> None:
        schedule = aes._expand_key(bytes(16))
        expected = aes._encrypt_block(bytes([255]) * 16, schedule) + aes._encrypt_block(bytes(16), schedule)
        self.assertEqual(aes.ctr_keystream(32, iv=bytes([255]) * 16), expected)


class ValidationTests(unittest.TestCase):
    def test_invalid_grid_shapes_and_symbols(self) -> None:
        grids = (np.zeros((1, 1), dtype=np.int8), np.zeros((24, 24), dtype=np.int8),
                 np.zeros((25, 26), dtype=np.int8), np.zeros(625, dtype=np.int8),
                 np.zeros((25, 25), dtype=float), np.full((25, 25), 4, dtype=np.int8),
                 np.full((25, 25), -1, dtype=np.int8))
        for grid in grids:
            with self.subTest(shape=grid.shape, dtype=grid.dtype), self.assertRaises(ValueError):
                decode_grid(grid)

    def test_incomplete_or_invalid_symbol_groups(self) -> None:
        for symbols in ((1,), (0, 1, 2, 4), (0, 1, 2, -1)):
            with self.subTest(symbols=symbols), self.assertRaises(ValueError):
                pack_symbols(symbols)

    def test_unsupported_text_grid(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported size"):
            rgbw_to_grid("\n".join("w" * 24 for _ in range(24)))

    def test_nonexact_codeword_lengths(self) -> None:
        valid = grid_to_bytes(to_canonical(payload_to_grid(b"hello")))
        for codeword in (b"", valid[:-1], valid[:92], valid + b"\x01"):
            for operation in (lambda: data_region(codeword, 25, 50), lambda: split_message(codeword, 25),
                              lambda: syndromes_ok(codeword, 25, 50), lambda: correct_codeword(codeword, 25, 50),
                              lambda: correct_codeword_erasures(codeword, 25, 50, ())):
                with self.subTest(length=len(codeword)), self.assertRaises(ValueError):
                    operation()

    def test_invalid_block_and_erasure_inputs(self) -> None:
        for block, parity in ((b"", 5), (bytes(256), 50), (bytes(10), 11), (bytes(10), 0)):
            with self.subTest(length=len(block), parity=parity), self.assertRaises(ValueError):
                correct_block(block, parity)
        valid = grid_to_bytes(to_canonical(payload_to_grid(b"hello")))
        for position in (-1, len(valid), 1.5, True):
            with self.subTest(position=position), self.assertRaises(ValueError):
                correct_codeword_erasures(valid, 25, 50, (position,))

    def test_invalid_api_options_fail_before_sampling(self) -> None:
        for options in ({"module_count": 24}, {"framing": "bad"}, {"color_factor": float("nan")}, {"color_factor": 1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                decode_image(Image.new("RGB", (10, 10)), **options)

    def test_unknown_result_fields_are_rejected(self) -> None:
        with self.assertRaises(TypeError):
            DecodeResult(crc_okk=True)


class ReedSolomonTests(unittest.TestCase):
    def test_crc24_known_answer(self) -> None:
        self.assertEqual(crc24(b"123456789"), 0x21CF02)

    def test_parity_faults_never_touch_data(self) -> None:
        for size, spec in FORMATS.items():
            for parity in spec.parity_variants:
                original = grid_to_bytes(to_canonical(payload_to_grid(b"hello", size=size, npar=parity)))
                layout = RsLayout.for_size(size, parity)
                for count in (1, layout.blocks[-1].parity + 1, parity):
                    if count > parity:
                        continue
                    with self.subTest(size=size, parity=parity, count=count):
                        damaged = grid_to_bytes(to_canonical(payload_to_grid(b"hello", size=size, npar=parity, rs_errors=count)))
                        self.assertEqual(data_region(original, size, parity), data_region(damaged, size, parity))
                        changes = {position for position, pair in enumerate(zip(original, damaged)) if pair[0] != pair[1]}
                        self.assertEqual(len(changes), count)
                        self.assertLessEqual(changes, set(layout.parity_positions()))
                        self.assertFalse(syndromes_ok(damaged, size, parity))

    def test_fault_counts_outside_parity_capacity_are_rejected(self) -> None:
        for size, spec in FORMATS.items():
            for count in (-1, spec.primary_parity + 1, 1.5):
                with self.subTest(size=size, count=count), self.assertRaises(ValueError):
                    payload_to_grid(b"hello", size=size, rs_errors=count)

    def test_all_parity_variants_roundtrip(self) -> None:
        for size, spec in FORMATS.items():
            for parity in spec.parity_variants:
                with self.subTest(size=size, parity=parity):
                    payload = bytes([165]) * spec.max_payload(parity)
                    result = decode_grid(payload_to_grid(payload, size=size, npar=parity))
                    self.assertTrue(result.crc_ok and result.rs_ok and result.finder_ok)
                    self.assertEqual(result.npar, parity)
                    self.assertEqual(result.payload, payload)

    def test_alternate_variant_wrong_crc_keeps_correct_split(self) -> None:
        for size, parity in ((25, 38), (55, 336)):
            with self.subTest(size=size):
                result = decode_grid(payload_to_grid(b"hello", size=size, npar=parity, crc_error=True))
                self.assertFalse(result.crc_ok)
                self.assertTrue(result.rs_ok)
                self.assertEqual(result.npar, parity)
                self.assertEqual(result.payload, b"hello")

    def test_randomized_errors_and_erasures_at_capacity(self) -> None:
        random = Random(20261113)
        for size, spec in FORMATS.items():
            for parity in spec.parity_variants:
                original = grid_to_bytes(to_canonical(payload_to_grid(b"randomized repair", size=size, npar=parity)))
                for case in range(10):
                    damaged = bytearray(original)
                    erasures: set[int] = set()
                    for block in RsLayout.for_size(size, parity).blocks:
                        count = random.randrange(block.parity + 1)
                        errors = (block.parity - count) // 2
                        positions = random.sample(range(block.offset, block.offset + block.length), count + errors)
                        erasures.update(positions[:count])
                        for position in positions:
                            damaged[position] ^= random.randrange(1, 256)
                    with self.subTest(size=size, parity=parity, case=case):
                        result = correct_codeword_erasures(damaged, size, parity, erasures)
                        self.assertIsNotNone(result)
                        self.assertEqual(result[0], original)

    def test_correction_count_excludes_unchanged_erasures(self) -> None:
        original = grid_to_bytes(to_canonical(payload_to_grid(b"hello")))
        damaged = bytearray(original)
        damaged[0] ^= 127
        result = correct_codeword_erasures(damaged, 25, 50, (0, 1))
        self.assertEqual(result, (original, 1))


class DecoderTests(unittest.TestCase):
    def test_sampled_and_corrected_provenance(self) -> None:
        canonical = to_canonical(payload_to_grid(b"hello"))
        original = grid_to_bytes(canonical)
        position = next(data_positions(25))
        canonical.reshape(-1)[position] ^= 1
        damaged = canonical.copy()
        result = decode_grid(canonical)
        self.assertTrue(result.crc_ok and result.rs_ok)
        self.assertEqual(result.payload, b"hello")
        self.assertEqual(result.codeword, original)
        self.assertEqual(result.sampled_codeword, grid_to_bytes(result.grid))
        self.assertEqual(result.codeword, grid_to_bytes(result.corrected_grid))
        self.assertEqual(len(result.corrections), 1)
        change = result.corrections[0]
        self.assertEqual(change.position, 0)
        self.assertEqual(change.observed, result.sampled_codeword[0])
        self.assertEqual(change.corrected, original[0])
        np.testing.assert_array_equal(canonical, damaged)
        np.testing.assert_array_equal(codeword_to_grid(original, result.grid), result.corrected_grid)

    def test_forced_size_applies_to_borderless_fallback(self) -> None:
        image = render_grid(payload_to_grid(b"hello"))
        result = decode_image(image, module_count=35)
        self.assertEqual(result.size, 35)
        self.assertFalse(result.crc_ok)
        sizes = {grid.shape[0] for grid, _ in borderless_grid_candidates(image, module_count=35)}
        self.assertEqual(sizes, {35})
        self.assertEqual(decode_image(image, module_count=25).payload, b"hello")

    def test_failed_size_search_keeps_best_finder(self) -> None:
        for size in (35, 45, 55):
            with self.subTest(size=size):
                result = _decode_one(encode("68656c6c6f", size=size, crc_error=True), None)
                self.assertEqual(result.size, size)
                self.assertTrue(result.finder_ok)
                self.assertEqual(result.payload, b"hello")
                self.assertFalse(result.crc_ok)

    def test_repeated_candidate_grids_are_decoded_once(self) -> None:
        session = DecodeSession(None, "length", debug=True)
        grid = payload_to_grid(b"hello")
        with patch.object(decoder, "decode_grid", wraps=decode_grid) as wrapped:
            first = session.decode(grid, "first", Classifier(False, 1.3))
            second = session.decode(grid.copy(), "second", Classifier(True, 1.3))
        self.assertEqual(wrapped.call_count, 1)
        self.assertIsNot(first, second)
        self.assertFalse(session.diagnostics[0].reused)
        self.assertTrue(session.diagnostics[1].reused)

    def test_programming_errors_propagate(self) -> None:
        with patch.object(decoder, "extract_grid", side_effect=RuntimeError("implementation bug")):
            with self.assertRaisesRegex(RuntimeError, "implementation bug"):
                decode_image(Image.new("RGB", (100, 100)))

    def test_expected_sampling_errors_are_recorded(self) -> None:
        session = DecodeSession(25, "length", debug=True)
        with patch.object(decoder, "extract_grid", side_effect=SamplingError("unreadable field")):
            result = session.sample_image(Image.new("RGB", (100, 100)), Classifier(False, 1.3), "direct")
        self.assertIsNone(result)
        self.assertEqual(session.diagnostics[0].error, "unreadable field")

    def test_real_perspective_distortions(self) -> None:
        captures = (
            PerspectiveCapture(((80, 70), (544, 70), (484, 534), (140, 534))),
            PerspectiveCapture(((95, 95), (525, 130), (550, 510), (70, 540))),
            PerspectiveCapture(((140, 40), (570, 150), (460, 560), (40, 420))),
        )
        for capture in captures:
            with self.subTest(corners=capture.corners):
                result = decode_image(capture.render(encode("68656c6c6f")), module_count=25)
                self.assertTrue(result.crc_ok)
                self.assertEqual(result.payload, b"hello")


class SamplingTests(unittest.TestCase):
    def test_vector_classifier_matches_scalar_rules(self) -> None:
        random = np.random.default_rng(20261113)
        rgb = random.integers(0, 256, (55, 55, 3), dtype=np.int32)
        for relative in (False, True):
            with self.subTest(relative=relative):
                expected = np.array([[_classify_relative(*cell, 1.3) if relative else _classify(*cell)
                                      for cell in row] for row in rgb])
                np.testing.assert_array_equal(_classify_rgb_grid(rgb, relative, 1.3), expected)

    def test_vector_medians_match_scalar_sampling(self) -> None:
        random = np.random.default_rng(100)
        image = random.integers(0, 256, (300, 300, 3), dtype=np.int32)
        actual = sample_lattice(image, 20, 20, 10, 10, 25)
        expected = np.array([[_classify(*sample_median_rgb(image, 25 + 10 * column, 25 + 10 * row, 2))
                              for column in range(25)] for row in range(25)])
        np.testing.assert_array_equal(actual, expected)

    def test_off_image_windows_do_not_wrap(self) -> None:
        image = np.full((10, 10, 3), 255, dtype=np.int32)
        for x, y in ((-10, 0), (0, -10), (20, 0), (0, 20)):
            with self.subTest(x=x, y=y):
                self.assertIsNone(sample_median_rgb(image, x, y, 1))


class FramingTests(unittest.TestCase):
    def test_trailing_nonzero_bytes_are_preserved(self) -> None:
        container = FramedMessage.decode(b"\x01A\x12\x34", FORMATS[25], "length")
        self.assertEqual(container.payload, b"A")
        self.assertEqual(container.trailing_bytes, b"\x12\x34")
        self.assertEqual(container.synthesized_padding, 0)

    def test_synthetic_padding_is_explicit(self) -> None:
        container = FramedMessage.decode(b"\x04AB", FORMATS[25], "length")
        self.assertEqual(container.payload, b"AB\x00\x00")
        self.assertEqual(container.synthesized_padding, 2)
        self.assertFalse(container.clean)

    def test_full_framing_short_messages_keep_zero_padding(self) -> None:
        result = decode_grid(payload_to_grid(b"A", framing="full"), framing="full")
        self.assertEqual(result.payload, b"A" + bytes(88))
        self.assertEqual(result.container.trailing_bytes, b"")


if __name__ == "__main__":
    unittest.main()
