# Cronto / photoTAN colour-code tools

Decode Cronto colour-code images to payload bytes, generate codes from payloads,
and render manually transcribed colour grids. The implementation is written in
Python with Pillow and NumPy, including its own documented AES and Reed–Solomon
primitives.

The byte-level specification, finder patterns, framing conventions, research
provenance and remaining unknowns belong in **[FORMAT.md](FORMAT.md)**. This
README describes using and testing the tools.

This is an independent interoperability and security-research project, not
affiliated with or endorsed by OneSpan/VASCO or the banks whose devices were
studied. Product names identify the formats and devices involved. Proprietary
APKs, libraries and decompiled source are not required to run these tools.
`RESEARCH-Findings.md` contains private research material and is intended to be
omitted from public exports.

All code in this repository was written by claude(opus-4.8) and later improved by chatgpt(GPT-6.1 Sol).

## Install

Requires Python 3.10 or later.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Decode an image

```bash
python crontodecode.py examples/show-serial.png
python crontodecode.py examples/getserial3-corrupted.png --corrections
python crontodecode.py examples/55x55-code.png --colors
python crontodecode.py examples/show-serial.png --debug
```

The decoder first checks direct grids and the coloured region's bounds with
cheap CRC probes. Every orientation is checked before trying expensive
Reed–Solomon repair; failed probes still reach the complete repair and
localization fallbacks. This avoids repairing many wrong grids before finding
a clean code embedded in a screenshot. It retries colour classification when
necessary and supports bordered images, tightly cropped
borderless grids, rotation, and perspective captures with sufficiently visible
border edges. Strong blur, glare, occlusion or unrelated coloured objects can
still defeat localization; there is no universal perspective-tolerance bound.

Output includes the located region, sampled colour distribution, orientation,
finder status, RS status, CRC, framing and payload. Colour distribution is a
diagnostic, not an authenticity test. CRC and RS protect the transport against
accidental errors; they do not authenticate the transaction or decrypt the
application's device-specific encrypted body.

Useful options:

| Option | Behavior |
|---|---|
| `--size 25` | Force one of 25/35/45/55 on **every** path, including borderless and text input. |
| `--framing length` | Strip the issuer-specific length prefix; this is the default. |
| `--framing full` | Return the entire descrambled message, for length-less containers such as observed Rabobank samples. |
| `--colors` | Print the **sampled** grid in display orientation, with function cells uppercase. |
| `--corrected-colors` | Print the grid after successful RS repairs; sampled finder cells are preserved. |
| `--corrections` | List changed codeword-byte offsets and before/after values. |
| `--grid` | Print the sampled canonical grid in `.RGB` form. |
| `--codewords` | Print sampled codewords for all eight orientations, before repair. |
| `--relative` | Try relative dominant-channel classification first. |
| `--color-factor 1.8` | Set its dominance ratio; must be finite and greater than 1. |
| `--debug` | Send candidate status, CRC/repair phase, cache reuse and sampling failures to stderr; preserve error tracebacks. |

The exit status is 0 when all inputs have a CRC-valid decode, 1 for an input
error or CRC failure, and 2 for argument-parser errors. Finder mismatches,
nonzero parity syndromes and an excessive declared length are reported
separately; they do not independently change CRC-based exit status. In
particular, parity-only corruption can leave the message CRC valid.

When no CRC passes, the decoder returns its best finder match for inspection.
Those bytes remain unvalidated and the CLI exits unsuccessfully.

## Work with text grids

```bash
python crontodecode.py --from-text grid.txt
python crontodecode.py --from-text -
python crontorender.py grid.txt rendered.png --dots --border
python crontorender.py grid.txt rendered.png --like reference.png
```

Text input is exactly N rows of N `w/r/g/b` characters (`.` also means white),
case-insensitive, with whitespace permitted. Supported sizes are 25/35/45/55.
Pass the grid rows themselves to the decoder, without surrounding report text.
The renderer also accepts surrounding report text and extracts the grid rows.

## Generate a code

```bash
python crontoencode.py 00C0C3E4403D7A345530678E2BA86F08010101010000 serial.png
python crontoencode.py 48656c6c6f hello.png --size 35
python crontoencode.py 48656c6c6f version0.png --size 25 --parity 38
```

The encoder supports the same grid sizes and framing options as the decoder.
`--parity` selects a supported variant for the size; otherwise the primary
variant is used. Capacity tables and variant evidence are in FORMAT.md. Finder
templates are implemented for all four sizes. The larger Cronto Dynamic sizes
are not the 25×25 hardware-authenticator format.

Length framing records the supplied payload length and pads the remaining
message space. Full framing also zero-pads a short input to the fixed message
capacity; decoding full framing returns that entire padded message, since no
length is encoded. A short full-framing input therefore does not round-trip to
its original length.

A code with valid image framing, CRC and parity is not necessarily an accepted
banking request: application commands and device-specific authentication are
additional layers. A fixed unsigned device-info query is illustrated above;
its selector family is documented in [docs/cdp-info-codes.md](docs/cdp-info-codes.md)
and [docs/cdp-info-codes.html](docs/cdp-info-codes.html).

## Fault injection

For testing a scanner you own:

```bash
python crontoencode.py 00 test.png --raw-length 200
python crontoencode.py 00 test.png --bad-crc
python crontoencode.py 00 test.png --rs-error 3
python crontoencode.py 00 test.png --finder-dot 0,24=g
```

`--raw-length` overrides the declared length without changing payload bytes.
Oversized lengths are deliberately supported. Decoding emulates the recorded
SDK zero-padding behavior and explicitly reports synthesized bytes.

`--bad-crc` changes the CRC before generating valid RS parity. `--rs-error N`
changes exactly N **parity bytes**, taking trailing parity from the last block
first and then preceding blocks; data and CRC are preserved. N must be between
zero and the selected variant's total parity count. `--finder-dot` accepts only
registration cells, using display row/column coordinates.

## Python API and provenance

```python
from cronto.decoder import decode_image

result = decode_image("examples/getserial3-corrupted.png", debug=True)
print(result.payload.hex(), result.crc_ok, result.rs_ok)
for correction in result.corrections:
    print(correction.position, correction.observed, correction.corrected)
```

`DecodeResult` is a typed dataclass. `grid` and `sampled_codeword` preserve what
was read, while `corrected_grid` and `codeword` describe the result after any
successful repair. `corrections` contains only bytes that actually changed.
`container.trailing_bytes` preserves bytes after a declared payload, including
nonzero bytes; `container.synthesized_padding` counts zeros absent from the
captured message. `diagnostics` is populated with `debug=True`.

`decode_grid(grid, repair=False)` performs only the orientation/CRC probe stage;
its unsuccessful result is a diagnostic guess, not an RS-repaired decode.
The default remains `repair=True`. Within a decode session, a failed probe is
never reused as if repair had already been attempted. Finder agreement orders
repair attempts but never removes an orientation from the search.

Shared `GridFormat`, `RsLayout` and `RsBlockLayout` objects describe capacities
and contiguous block boundaries. `FramedMessage` uses explicit byte readers and
writers. Existing encoder/decoder functions and the compatibility
`block_layout()` tuple interface remain available. Invalid sizes, shapes,
symbols, variants, erasure positions and non-exact codeword lengths raise
`ValueError`; an uncorrectable valid-length RS block returns `None`.

## Tests

```bash
python -m unittest discover -v
python -m unittest tests.test_regressions.AesTests -v
python -m tests.roundtrip
```

The last command is a compatibility entry point for the same discovered suite.
Cases cover the committed captures, all supported parity variants, CRC/RS and
finder faults, maximum payloads, framing, errors-and-erasures limits, sampled
versus repaired provenance, concurrent AES cache misses, malformed input, CLI
behavior, rotation and synthetic perspective captures. Optional proprietary SDK
fixtures under `tests/generated/` are explicitly skipped when absent.

The committed Banque Cramer and APS Bank screenshots are performance regression
fixtures. Their tests require identical payload hashes and successful decoding
without calling the RS repair search, avoiding machine-dependent timing limits.

The committed real images exercise decoding independently of this encoder.
Synthetic round trips verify internal consistency, not vendor compatibility by
themselves. Historical SDK/device observations are identified in FORMAT.md;
the automated suite does not rerun an external SDK or physical scanner.

## Layout

| Path | Purpose |
|---|---|
| `cronto/formats.py` | Shared grid parameters and RS block layouts |
| `cronto/binary.py`, `cronto/framing.py` | Byte reader/writer and message framing |
| `cronto/grid.py`, `cronto/locate.py`, `cronto/geometry.py` | Sampling, localization and border-line fitting |
| `cronto/symbols.py` | Finder patterns, symbol packing and orientations |
| `cronto/gf.py`, `cronto/rs.py`, `cronto/aes.py` | Explicit transport primitives |
| `cronto/decoder.py`, `cronto/encoder.py` | Pipeline orchestration |
| `crontodecode.py`, `crontoencode.py`, `crontorender.py` | Command-line tools |
| `docs/gen_cdp_info_page.py` | Regenerate the device-info reference pages and PNGs |
| `tests/` | Discoverable tests, fixtures and compatibility runner |
| `FORMAT.md` | Detailed specification, evidence and unresolved questions |
