"""
Top-level Cronto / photoTAN colour-code decoder.

Pipeline (reversed from CrontoSign_Swiss 7.2.14 native libQRCronto.so):
    image -> N x N symbols -> orientation -> drop 57 function modules
    -> packed codeword -> Reed-Solomon repair + CRC-24
    -> AES-128-CTR descramble (key=iv=0) -> explicit container framing.

CRC-24 selects a decode; RS and finder status are reported separately. A CRC
pass is evidence of a transport decode, not authentication of the payload.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from math import isfinite
from typing import NamedTuple

import numpy as np
from PIL import Image

from .aes import ctr_crypt
from .errors import SamplingError
from .formats import CODE_POINTS, grid_format
from .framing import FramedMessage, validate_framing
from .grid import extract_grid
from .rs import split_message, syndromes_ok, correct_codeword
from .symbols import codeword_to_grid, grid_to_bytes, orientations, expected_finder, validate_grid


class FinderMismatch(NamedTuple):
    position: int
    observed: int
    expected: int


@dataclass(frozen=True)
class ByteCorrection:
    position: int
    observed: int
    corrected: int


@dataclass(frozen=True)
class DecodeAttempt:
    source: str
    size: int | None
    classifier: str
    crc_ok: bool = False
    finder_matches: int = 0
    reused: bool = False
    error: str | None = None
    phase: str = "repair"

    def describe(self) -> str:
        status = self.error or f"CRC={'ok' if self.crc_ok else 'failed'} finder={self.finder_matches}"
        cached = " cached" if self.reused else ""
        return f"{self.source} size={self.size} {self.classifier} {self.phase}{cached}: {status}"


@dataclass
class DecodeResult:
    # Every field is declared; misspelled constructor keywords are rejected.
    size: int = 0
    orientation: str = ""
    codeword: bytes = b""  # Corrected codeword, or the sampled one when no repair succeeded.
    sampled_codeword: bytes = b""
    message: bytes = b""  # RS message, still AES-scrambled.
    npar: int = 0
    crc_ok: bool = False
    plaintext: bytes = b""
    length_prefix: int = 0
    clean: bool = True
    payload: bytes = b""
    framing: str = "length"
    container: FramedMessage | None = None
    rectified: bool = False  # Decoded after crop/rectify/borderless, not the direct path.
    field_bbox: tuple[int, int, int, int] | None = None  # Inclusive source-image coordinates.
    image_size: tuple[int, int] | None = None
    finder_ok: bool | None = None
    finder_mismatches: tuple[FinderMismatch, ...] = ()
    rs_ok: bool | None = None
    errors_corrected: int = 0
    grid: np.ndarray | None = field(default=None, repr=False)  # Sampled canonical grid, never repaired in place.
    corrected_grid: np.ndarray | None = field(default=None, repr=False)
    corrections: tuple[ByteCorrection, ...] = ()
    diagnostics: tuple[DecodeAttempt, ...] = ()

    def __repr__(self) -> str:
        return (f"DecodeResult(size={self.size}x{self.size}, orientation={self.orientation!r}, "
                f"npar={self.npar}, crc_ok={self.crc_ok}, payload_len={len(self.payload)})")


@dataclass(frozen=True)
class CodewordDecode:
    sampled: bytes
    codeword: bytes
    message: bytes
    npar: int
    crc_ok: bool

    @classmethod
    def decode(cls, sampled: bytes, size: int) -> CodewordDecode:
        message, npar, crc_ok = split_message(sampled, size)
        if crc_ok or syndromes_ok(sampled, size, npar):
            return cls(sampled, sampled, message, npar, crc_ok)
        # Repair mis-read symbols per block, then re-check CRC. A wrong-CRC code
        # still returns its sampled bytes for inspection when no repair succeeds.
        for parity in grid_format(size).parity_variants:
            repaired = correct_codeword(sampled, size, parity)
            if repaired is None:
                continue
            codeword, _ = repaired
            message2, npar2, ok2 = split_message(codeword, size)
            if ok2:
                return cls(sampled, codeword, message2, npar2, True)
        return cls(sampled, sampled, message, npar, False)

    @classmethod
    def probe(cls, sampled: bytes, size: int) -> CodewordDecode:
        """Check CRC without solving RS errors or inferring a failed variant."""
        message, npar, crc_ok = split_message(sampled, size, identify_variant=False)
        return cls(sampled, sampled, message, npar, crc_ok)


@dataclass(frozen=True)
class OrientedGrid:
    name: str
    grid: np.ndarray
    decoded: CodewordDecode

    @property
    def finder_matches(self) -> int:
        expected = expected_finder(self.grid.shape[0], self.decoded.npar)
        if expected is None:
            return -1
        return len(expected) - len(_finder_mismatches(self.grid, self.decoded.npar))

    @classmethod
    def probes(cls, grid: np.ndarray) -> Iterator[OrientedGrid]:
        cache: dict[bytes, CodewordDecode] = {}
        for name, oriented in orientations(grid):
            canonical = np.ascontiguousarray(oriented)
            sampled = grid_to_bytes(canonical)
            decoded = cache.get(sampled)
            if decoded is None:
                decoded = CodewordDecode.probe(sampled, canonical.shape[0])
                cache[sampled] = decoded
            yield cls(name, canonical, decoded)


def _finder_mismatches(grid: np.ndarray, parity: int) -> tuple[FinderMismatch, ...]:
    expected = expected_finder(grid.shape[0], parity)
    if expected is None:
        return ()
    flat = grid.reshape(-1)
    return tuple(FinderMismatch(position, int(flat[position]), symbol)
                 for position, symbol in expected.items() if int(flat[position]) != symbol)


def _make_result(name: str, grid: np.ndarray, decoded: CodewordDecode, framing: str) -> DecodeResult:
    size = grid.shape[0]
    plaintext = ctr_crypt(decoded.message)  # AES-128-CTR, key=0, iv=0.
    container = FramedMessage.decode(plaintext, grid_format(size), framing)
    corrections = tuple(ByteCorrection(position, observed, corrected)
                        for position, (observed, corrected) in enumerate(zip(decoded.sampled, decoded.codeword))
                        if observed != corrected)
    mismatches = _finder_mismatches(grid, decoded.npar)
    return DecodeResult(
        size=size, orientation=name, codeword=decoded.codeword, sampled_codeword=decoded.sampled,
        message=decoded.message, npar=decoded.npar, crc_ok=decoded.crc_ok,
        plaintext=plaintext, length_prefix=container.length_prefix, clean=container.clean,
        payload=container.payload, framing=framing, container=container,
        finder_ok=not mismatches, finder_mismatches=mismatches,
        rs_ok=syndromes_ok(decoded.codeword, size, decoded.npar), errors_corrected=len(corrections),
        grid=grid.copy(), corrected_grid=codeword_to_grid(decoded.codeword, grid), corrections=corrections,
    )


def decode_grid(grid: np.ndarray, framing: str = "length", *, repair: bool = True) -> DecodeResult:
    """Decode a supported square symbol grid; preserve sampled and repaired data.

    Finder checks are advisory. The version cell is checked against the decoded
    parity variant; issuer-variant dots are omitted from the fixed template.
    Length framing emulates Arrays.copyOfRange zero-padding, explicitly reported
    in result.container. Full framing returns the entire descrambled message.
    Every orientation gets a cheap CRC probe before any RS repair. repair=False
    performs only that probe stage, for candidate screening by decode_image.
    """
    validate_grid(grid)
    validate_framing(framing)
    candidates: list[OrientedGrid] = []
    for candidate in OrientedGrid.probes(grid):
        if candidate.decoded.crc_ok:
            return _make_result(candidate.name, candidate.grid, candidate.decoded, framing)
        candidates.append(candidate)
    if repair:
        return _repair_candidates(candidates, framing)
    best = max(candidates, key=lambda candidate: candidate.finder_matches)
    return _make_result(best.name, best.grid, best.decoded, framing)


def _repair_candidates(candidates: list[OrientedGrid], framing: str) -> DecodeResult:
    # Finder agreement only orders work; every orientation remains eligible.
    # A damaged finder therefore cannot remove an otherwise correct decode.
    best: OrientedGrid | None = None
    cache: dict[bytes, CodewordDecode] = {}
    for candidate in sorted(candidates, key=lambda item: item.finder_matches, reverse=True):
        sampled = candidate.decoded.sampled
        decoded = cache.get(sampled)
        if decoded is None:
            decoded = CodewordDecode.decode(sampled, candidate.grid.shape[0])
            cache[sampled] = decoded
        repaired = replace(candidate, decoded=decoded)
        if decoded.crc_ok:
            return _make_result(repaired.name, repaired.grid, decoded, framing)
        if best is None or repaired.finder_matches > best.finder_matches:
            best = repaired
    if best is None:
        raise SamplingError("no grid orientation could be decoded")
    return _make_result(best.name, best.grid, best.decoded, framing)


def _image_size(img) -> tuple[int, int] | None:
    # PIL .size is (width,height); NumPy .size is the element count. Warped
    # arrays must use .shape instead, so handle the two types explicitly.
    if isinstance(img, np.ndarray):
        return img.shape[1], img.shape[0]
    return getattr(img, "size", None)


def _finder_score(result: DecodeResult | None) -> tuple[int, int]:
    """CRC first, then the number of matching fixed registration cells."""
    if result is None:
        return -1, -2
    expected = expected_finder(result.size, result.npar)
    matches = len(expected) - len(result.finder_mismatches) if expected is not None else -1
    return int(result.crc_ok), matches


@dataclass(frozen=True)
class Classifier:
    relative: bool
    factor: float

    @property
    def name(self) -> str:
        return f"relative/{self.factor:g}" if self.relative else "absolute"


def _classifiers(relative: bool, factor: float) -> Iterator[Classifier]:
    if not isfinite(factor) or factor <= 1:
        raise ValueError("color_factor must be finite and greater than 1")
    attempts = (Classifier(True, factor), Classifier(False, factor)) if relative else (
        Classifier(False, factor), Classifier(True, factor), Classifier(True, 1.8))
    seen: set[tuple[bool, float]] = set()
    for attempt in attempts:
        key = attempt.relative, attempt.factor if attempt.relative else 0.0
        if key not in seen:
            seen.add(key)
            yield attempt


class DecodeSession:
    def __init__(self, module_count: int | None, framing: str, debug: bool = False):
        if module_count is not None:
            grid_format(module_count)
        validate_framing(framing)
        self.module_count = module_count
        self.framing = framing
        self.debug = debug
        self.cache: dict[tuple[int, bytes, bool], DecodeResult] = {}
        self.diagnostics: list[DecodeAttempt] = []
        self.best: DecodeResult | None = None

    def record(self, attempt: DecodeAttempt) -> None:
        if self.debug:
            self.diagnostics.append(attempt)

    def decode(self, grid: np.ndarray, source: str, classifier: Classifier, *, repair: bool = True) -> DecodeResult:
        key = grid.shape[0], grid.tobytes(), repair
        cached = self.cache.get(key)
        if cached is None:
            alternate = self.cache.get((key[0], key[1], not repair))
            if alternate is not None and (not repair or alternate.crc_ok):
                cached = alternate
        reused = cached is not None
        if cached is None:
            cached = decode_grid(grid, self.framing, repair=repair)
            self.cache[key] = cached
        result = replace(cached)
        self.record(DecodeAttempt(source, result.size, classifier.name, result.crc_ok,
                                  _finder_score(result)[1], reused, phase="repair" if repair else "crc"))
        return result

    def keep(self, result: DecodeResult | None) -> None:
        # Keep the strongest finder match, not the first sampled size. Otherwise
        # a failed 25x25 guess hides a correctly sampled larger grid.
        if result is not None and (self.best is None or _finder_score(result) > _finder_score(self.best)):
            self.best = result

    def finish(self, result: DecodeResult | None = None) -> DecodeResult:
        chosen = result if result is not None else self.best
        if chosen is None:
            raise SamplingError("no Cronto code found in image")
        return replace(chosen, diagnostics=tuple(self.diagnostics))

    def sample_image(self, img, classifier: Classifier, source: str, *, repair: bool = True) -> DecodeResult | None:
        sizes = (self.module_count,) if self.module_count is not None else CODE_POINTS
        best: DecodeResult | None = None
        for size in sizes:
            try:
                grid, bbox = extract_grid(img, module_count=size, relative=classifier.relative,
                                          color_factor=classifier.factor, return_bbox=True)
            except SamplingError as error:
                self.record(DecodeAttempt(source, size, classifier.name, error=str(error),
                                          phase="repair" if repair else "crc"))
                continue
            result = self.decode(grid, source, classifier, repair=repair)
            result.field_bbox = bbox
            result.image_size = _image_size(img)
            if result.crc_ok:
                return result
            if best is None or _finder_score(result) > _finder_score(best):
                best = result
        return best

    def quick(self, img: Image.Image, classifiers: tuple[Classifier, ...]) -> DecodeResult | None:
        """Try unmodified lattices and tight colour bounds before expensive repair.

        No finder threshold or size estimate removes candidates. Only CRC-valid
        probes finish here; every failure still reaches the normal fallback.
        """
        from .locate import tight_grid_candidates
        for classifier in classifiers:
            result = self.sample_image(img, classifier, "direct", repair=False)
            if result is not None and result.crc_ok:
                return result
            for grid, bbox in tight_grid_candidates(img, self.module_count, classifier.relative, classifier.factor):
                result = self.decode(grid, "tight-colour", classifier, repair=False)
                result.rectified = True
                result.field_bbox = bbox
                result.image_size = img.size
                if result.crc_ok:
                    return result
        return None

    def located(self, img: Image.Image, classifiers: tuple[Classifier, ...]) -> Iterator[DecodeResult]:
        from .locate import localize_candidates
        for index, (warped, bbox) in enumerate(localize_candidates(img)):
            for classifier in classifiers:
                result = self.sample_image(warped, classifier, f"localized/{index}")
                if result is not None:
                    result.rectified = True
                    result.field_bbox = bbox  # Source region in the original image.
                    result.image_size = img.size
                    yield result

    def borderless(self, img: Image.Image, classifiers: tuple[Classifier, ...]) -> Iterator[DecodeResult]:
        from .locate import borderless_grid_candidates
        for classifier in classifiers:
            candidates = borderless_grid_candidates(img, relative=classifier.relative,
                                                    color_factor=classifier.factor, module_count=self.module_count)
            for index, (grid, bbox) in enumerate(candidates):
                result = self.decode(grid, f"borderless/{index}", classifier)
                result.rectified = True
                result.image_size = img.size
                result.field_bbox = bbox
                yield result


def _decode_one(img, module_count, framing="length", relative=False, color_factor=1.3):
    """Decode one already-located image; retained for existing diagnostic callers."""
    classifier = next(_classifiers(relative, color_factor))
    return DecodeSession(module_count, framing).sample_image(img, classifier, "direct")


def decode_image(path_or_img, module_count=None, framing="length",
                 relative=False, color_factor=1.3, debug=False) -> DecodeResult:
    """Probe direct/tight-colour grids, then try the exhaustive repair paths.

    module_count constrains every path. When no CRC passes, return the best
    finder match for inspection. Unexpected programming exceptions propagate.
    debug=True records candidate status and expected sampling failures.
    """
    session = DecodeSession(module_count, framing, debug)
    classifiers = tuple(_classifiers(relative, color_factor))
    if isinstance(path_or_img, Image.Image):
        img = path_or_img.convert("RGB")
    else:
        with Image.open(path_or_img) as source:
            img = source.convert("RGB")
    quick = session.quick(img, classifiers)
    if quick is not None:
        return session.finish(quick)
    for classifier in classifiers:
        result = session.sample_image(img, classifier, "direct")
        if result is not None and result.crc_ok:
            return session.finish(result)
        session.keep(result)
    for candidates in (session.located(img, classifiers), session.borderless(img, classifiers)):
        for result in candidates:
            if result.crc_ok:
                return session.finish(result)
            session.keep(result)
    return session.finish()


def _fmt(bs):
    return bs.hex()


if __name__ == "__main__":
    import sys
    for fn in sys.argv[1:]:
        r = decode_image(fn)
        print(f"\n=== {fn} ===")
        print(f"  grid           : {r.size}x{r.size}")
        print(f"  orientation    : {r.orientation}")
        print(f"  RS parity bytes: {r.npar}   CRC-24 valid: {r.crc_ok}")
        print(f"  codeword  ({len(r.codeword):3d}B): {_fmt(r.codeword)}")
        print(f"  RS message({len(r.message):3d}B): {_fmt(r.message)}")
        print(f"  descrambled({len(r.plaintext):3d}B): {_fmt(r.plaintext)}")
        print(f"  length prefix  : {r.length_prefix}")
        print(f"  payload  ({len(r.payload):3d}B): {_fmt(r.payload)}")
        print(f"  payload (HEX for SDK output): {r.payload.hex().upper()}")
