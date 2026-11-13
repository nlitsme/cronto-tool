# Cronto / photoTAN colour code — format specification

## 1. Overview

A Cronto colour code is a square matrix of rounded **modules**, each in one of
four colours — white (background), red, green, blue — encoding **2 bits per
module**. It is framed by a solid black rounded border and a one-module white
quiet zone. It is the image half of a OneSpan DIGIPASS "Secure Channel"
transaction-data-signing / challenge-response exchange: the code transports an
(encrypted) message that the enrolled device decrypts, displays, and signs.

This document specifies the on-wire format only. Two layers are distinguished:

1. **Image → message** — geometry, colour, error correction, transport
   de-obfuscation and framing. Implemented in `cronto/`.
2. **Message → transaction** — the DIGIPASS Secure Channel container: an
   identifier plus a body encrypted with a device key established at activation.
   The body decryptor and its key are out of scope here (§5).

Some claims are marked *unconfirmed* or *inferred* where the format value is known
but its meaning is not. CRC-24 and RS agreement on independently captured codes
provide evidence for the geometry, read order and transport interpretation.
CRC is not a cryptographic authenticator; a CRC-valid payload can be fabricated.
Synthetic encoder/decoder round trips alone do not establish interoperability.

---

## 2. Image → message pipeline (layer 1)

```
image
 ├─ locate black border, sample the N×N module lattice
 │     colour → 2-bit symbol:  0 white · 1 red · 2 green · 3 blue
 ├─ choose orientation; drop the 57 function modules;
 │     pack 4 symbols/byte, first symbol in the MSBs
 │        byte = (s0<<6)|(s1<<4)|(s2<<2)|s3            → (N²−57)/4 byte codeword
 ├─ systematic Reed–Solomon over GF(2⁸), DATA first, PARITY last
 │     N=25: one block;  N=35/45/55: ⌊len/255⌋+1 contiguous blocks (§2.5)
 │        data = concat over blocks of block[:blocklen−blockparity]
 │     within data:  message = data[:−3],  crc = data[−3:]
 │        crc = CRC-24/OpenPGP over message
 └─ transport de-obfuscation:  AES-128-CTR, key = IV = 0
          message = codeword-message XOR AES0_CTR_keystream
             → the descrambled message is handed to the layer-2 container (§3)
```

### 2.1 Geometry

> **Coordinate convention.** Unless noted otherwise, all `(row, col)` are in the
> **display (rendered-image) orientation** — the code as you see it, with `(0,0)`
> at the top-left. The top-left corner is the one whose registration square is a
> **full 3×3 with a centre coloured dot**; that geometry is unique (the other three corners are
> 5-module L-shapes) and is the orientation anchor, so rotate/flip any capture
> until that square is top-left and its `(row,col)` match this document and
> `crontodecode.py --colors`. The corner dot is blue for sizes 25/55, green for
> 35, and red for 45. The centre registration block is also a full 3×3; use the
> corner block as the anchor. Canonical decoder coordinates are the transpose
> of these display coordinates, not an additional data-reading convention.

- Grid sizes `N ∈ {25, 35, 45, 55}` modules per side. Captured *scanner /
  transaction* codes are all **25**; **55** carries a device-activation message
  (§3.2). The observed 534-byte payload requires N=55; smaller and larger payloads
  also fit that size, up to its capacity (§2.5). Only **25×25** is the hardware-authenticator format;
  **35/45/55** are the phone-app "Cronto Dynamic" format, not scannable by the
  hardware tokens (DP760 / DP780), so bank scanners ignore the larger grids.
- Data capacity `(N²−57)/4` bytes; **57 modules** are finder/registration
  ("function") modules (§2.3).
- **Sizes are necessarily odd — a finder-geometry constraint.** The 57 function
  modules form a **3×3 lattice of registration blocks** whose centres sit on the
  grid's three symmetry lines in each axis: index `0`, `(N−1)/2`, and `N−1`. Each
  block is an **odd width** (≈3 modules) with a single central module, so for the
  centre and edge-midpoint blocks to sit on the mid-axis, `(N−1)/2` must be an
  **integer** — i.e. **N odd**. Measured block-centre lattice: mid-axis 12, 17,
  22, 27 for N = 25, 35, 45, 55. For even N the mid-axis falls *between* modules
  (N=20 → 9.5), so a uniform 3×3 block cannot sit symmetrically there; keeping
  symmetry would need even-width centre/edge blocks, a different shape from the
  corners. (QR codes are always odd-sized, `4v+17`, for the analogous reason.)
  This describes the supported lattice, not a proof that every colour-code
  design must be odd-sized. A centre-less **20×20** code seen in OneSpan marketing
  material is outside this specification and is not accepted by this implementation.

**Image processing is implementation-specific.** The implementation first probes
axis-aligned sampling and tight coloured-region bounds against CRC without RS
repair. All grid orientations receive this cheap check before repair begins.
Unsuccessful probes retain the full repair search, dot-region crops and
border-line fitting followed by a homography. A minimum-area rectangle remains
a rotation-only fallback. A final path samples borderless grids at multiple
offsets. Relative colour classification is retried when
needed. These heuristics do not change the on-wire format or guarantee recovery
under arbitrary perspective, clutter, occlusion, glare or blur. Automated tests
include rotation and several trapezoidal captures; historical broader tolerance
claims have not been reproduced as a universal bound.

### 2.2 Colour → symbol

```
white = 0    red = 1    green = 2    blue = 3
```

The palette is saturated near-pure red/green/blue on a near-white field, so a
max-channel rule classifies clean renders; a relative dominant-channel rule
(`crontodecode --relative`) handles desaturated / colour-shifted captures.

### 2.3 Data layout — function modules and reading order

**Function modules.** 57 of the N² modules are reserved *function* (registration)
modules — the Cronto analogue of a QR code's finder/alignment patterns. They form
a **3×3 lattice of small registration squares** at the four corners, the four
edge-midpoints, and the centre. Each square is mostly white with **one coloured
dot** (like a QR alignment pattern); the dot colours encode the size and version
(below). Their positions are shared across issuers; some colours vary by
version or issuer. They carry **no payload**. The 57 count
is fixed for every size — larger grids just have more data around the same nine
squares.

**The grid (N = 25).** Registration squares by value: `=` white module, `#` blue
module, `·` = data. Squares are *not* uniform 3×3, so a coloured module flush
against one is payload sitting in the gap. The bottom-right square carries a
**second dot at (22,24)** — the version bit (below):

```
         cols 0-2      cols 11-13    cols 23-24
    row 0 = = = · · · · · · · · = # = · · · · · · · · · = #   ┐
          = # = · · · · · · · · = = = · · · · · · · · · = =   │ TL   T   TR
          = = = · · · · · · · · · · · · · · · · · · · · · =   ┘
          · · · · · · · · · · · · · · · · · · · · · · · · ·
          (data)                                          ·
   row 11 = = · · · · · · · · · = = = · · · · · · · · · = =   ┐
          # = · · · · · · · · · = # = · · · · · · · · · = #   │ L   C   R
          = = · · · · · · · · · = = = · · · · · · · · · = =   ┘
          · · · · · · · · · · · · · · · · · · · · · · · · ·
          (data)                                          ·
   row 22 = · · · · · · · · · · · · · · · · · · · · · · · #   ← version bit at (22,24)
   row 23 = = · · · · · · · · · = = = · · · · · · · · · = =   ┐
          # = · · · · · · · · · = # = · · · · · · · · · = #   ┘ BL   B   BR
```

**What the finder colours encode.** Cronto carries no separate format-information
string; size, orientation and version are read geometrically from the nine
registration squares before the payload is decoded:

- **size** — a weighted measurement of the corner squares. The registration-dot
  *colours* also carry it: most registration dots are blue, and a
  **pair** of "size-marker" dots switches colour and diagonal with the grid (table
  below). The full dot pattern per size is in `symbols.FINDER_TEMPLATES`.
- **orientation** — the asymmetric "starting corner" (the unique full-3×3-centre
  square), which resolves rotation.
- **version** — for size 25 a version bit, the bottom-right square's second dot at
  cell **(22,24)** (display): **blue → NPAR 50**, **white → NPAR 38** (confirmed on
  real codes: payment / daily-limit captures are white/NPAR-38; login / transaction
  codes are blue/NPAR-50). NPAR selects the Reed–Solomon strength (§2.5).

Dots in display orientation at block positions TL·T·TR / L·C·R / BL·B·BR, `B` blue ·
`G` green · `R` red — the size-marker pair green or red on larger sizes, and size
25 a tenth dot, the version bit `V`:

```
    N = 25            N = 35            N = 45            N = 55
    B B B             G B B             R B B             B B G
    B B B  V          B B B             B B B             B B B
    B B B             B B G             B B R             G B B

    V = version bit at cell (22,24):  B → NPAR 50,  W → NPAR 38   (size 25 only)
```

| size | size-marker dots | marker colour | version bit |
|------|------------------|---------------|-------------|
| 25 | — (all nine blue) | blue | (22,24): blue → NPAR 50, white → NPAR 38 |
| 35 | TL, BR (main diagonal) | green | — (NPAR fixed at 88) |
| 45 | TL, BR (main diagonal) | red | — (NPAR fixed at 136) |
| 55 | TR, BL (anti-diagonal) | green | — (198 observed; 336 also supported, selection metadata unconfirmed) |

A further variable dot sits at **(22,0)** for size 25 (white on ING/Rabobank, blue
on APS Bank — an issuer/variant flag, meaning unconfirmed) and at **(52,0)** for
size 55 in display coordinates (canonical `(0,52)`). These issuer/variant cells
are omitted from the fixed-template check (`symbols.VARIANT_CELLS`). The separate
version cell is checked against the decoded parity variant (`symbols.VERSION_CELLS`).

This is analogous in *purpose* to a QR code's format-information block, but simpler:
**no user-selectable EC level, no data-mask, no encoding-mode indicator** (the
payload is raw bytes; "modes" live in the container layer, §3), and the finder
metadata is **not itself ECC-protected**.

**Reading order.** The data cells are read **down each column top→bottom, then
columns left→right**, skipping the registration squares. This yields the ordered
data stream `d[0], d[1], … , d[N²−58]` (568 symbols for N = 25). The grid below
labels each data cell with the **byte index** it belongs to (`=`/`#` = registration
modules; hex-ish labels `0,1,…,9,a,b,…`); bytes run **down the columns**:

```
    row 0 = = = d k q w · · · · = # = · · · · · · · · · = #
          = # = e k q w · · · · = = = · · · · · · · · · = =
          = = = e k q x · · · · · · · · · · · · · · · · · =
          0 4 8 e k r x · · · · · · · · · · · · · · · · · ·
          0 4 8 e l r x · · · · · · · · · · · · · · · · · ·
          0 4 8 f l r x · · · · · · · · · · · · · · · · · ·
          0 4 9 f l r y · · · · · · · · · · · · · · · · · ·
          1 5 9 f l s y · · · · · · · · · · · · · · · · · ·
          1 5 9 f m s y · · · · · · · · · · · · · · · · · ·
          1 5 9 g m s y · · · · · · · · · · · · · · · · · ·
          1 5 a g m s z · · · · · · · · · · · · · · · · · ·
   row 11 = = a g m t z · · · · = = = · · · · · · · · · = =
          # = a g n t z · · · · = # = · · · · · · · · · = #
          = = a h n t z · · · · = = = · · · · · · · · · = =
          2 6 b h n t · · · · · · · · · · · · · · · · · · ·
          2 6 b h n u · · · · · · · · · · · · · · · · · · ·
          2 6 b h o u · · · · · · · · · · · · · · · · · · ·
          2 6 b i o u · · · · · · · · · · · · · · · · · · ·
          3 7 c i o u · · · · · · · · · · · · · · · · · · ·
          3 7 c i o v · · · · · · · · · · · · · · · · · · ·
          3 7 c i p v · · · · · · · · · · · · · · · · · · ·
          3 7 c j p v · · · · · · · · · · · · · · · · · · ·
   row 22 = 8 d j p v · · · · · · · · · · · · · · · · · · #
   row 23 = = d j p w · · · · · = = = · · · · · · · · · = =
          # = d j q w · · · · · = # = · · · · · · · · · = #
```

Byte boundaries follow the data stream, not the visual columns: **byte 8** is the
last data cell of column 1, `(22,1)`, plus the top three of column 2,
`(3,2)(4,2)(5,2)` — jumping over the registration squares — and **byte d** wraps
from the bottom of column 2, `(22,2)(23,2)(24,2)`, into the top of column 3,
`(0,3)`. Larger sizes are the same idea with more data cells around the same nine
squares.

### 2.4 Packing

Each module is one colour = **2 bits**; four consecutive data symbols pack into
one byte, **first symbol in the most-significant bits**:

```
  colour → symbol:   white=00   red=01   green=10   blue=11

  data symbols:      d[0]   d[1]   d[2]   d[3]      d[4] …
                      │      │      │      │
  byte 0 bits:      b7 b6  b5 b4  b3 b2  b1 b0
  byte = (d0<<6) | (d1<<4) | (d2<<2) | d3

  example: green,blue,red,white = 10 11 01 00 = 0xB4
```

Packing all 568 symbols yields the **142-byte codeword** (`(25²−57)/4`), the input
to §2.5. The full chain:

```
  9 registration squares (fixed)          geometry / orientation / size / version
  N²−57 data modules, reading order ─┐
     → 2 bits each, 4 per byte        ├→ (N²−57)/4-byte codeword
                                      │      → RS split + CRC-24        (§2.5)
                                      │      → AES-128-CTR descramble   (§2.6)
                                      └      → descrambled message → container (§3)
```

### 2.5 Reed–Solomon + CRC

Systematic Reed–Solomon over GF(2⁸), **data first, parity last**.

**Parameters.** GF(2⁸) with primitive polynomial **`0x11D`**, generator **α = 2**,
generator polynomial `∏(x − αʲ)` for **j = 1..par**, where `par` is the parity
count of the individual block, not the multi-block total (first root α¹),
codeword byte order high-order-first. Confirmed three ways: every real codeword
has **zero syndromes** under these parameters, the systematic encoder reproduces
the **exact parity bytes** of every real codeword, and encoded codewords
round-trip through the decoder. Parity byte count `NPAR`:

| N | NPAR (total) | RS blocks | block layout (len, parity) | data bytes (codeword − parity) | corrects `t=⌊par/2⌋` per block |
|---|------|-----------|----------------------------|-----|-----|
| 25 | 50 | 1 | (142, 50) | 92 | **25** |
| 25 | 38 (version 0) | 1 | (142, 38) | 104 | 19 |
| 35 | 88 | 2 | (146, 44) × 2 | 204 | 22 |
| 45 | 136 | 2 | (246, 68) × 2 | 356 | 34 |
| 55 | 198 | 3 | (248, 66), (247, 66), (247, 66) | 544 | 33 |
| 55 | 336 | 3 | (248, 112), (247, 112), (247, 112) | 406 | 56 |

Sizes 25 and 55 each have two variants (separate rows above): for size 25 the
version bit in the finder pattern (§2.3) selects NPAR 50 vs 38; of the two
size-55 layouts the real activation code uses **198**.

The primary encoder variants are 50/88/136/198. The other supported variants
are selectable with `--parity`. Size 55 / parity 336 is a recorded decoder
candidate and is covered by synthetic tests; no independent real sample for
that variant is included. Its finder-based selection rule remains unconfirmed.

| N | Total parity | Message bytes after CRC removal | Length-prefix bytes | Maximum length-framed payload |
|---|---:|---:|---:|---:|
| 25 | 50 | 89 | 1 | 88 |
| 25 | 38 | 101 | 1 | 100 |
| 35 | 88 | 201 | 2 | 199 |
| 45 | 136 | 353 | 2 | 351 |
| 55 | 198 | 541 | 2 | 539 |
| 55 | 336 | 403 | 2 | 401 |

Full framing returns the entire message column above; it has no length prefix
to remove. The library requires the exact codeword byte count for the selected
grid size and never silently ignores a missing or additional byte.

**Multi-block RS for N > 25.** GF(2⁸) caps a single Reed–Solomon codeword at 255
bytes, but the byte codeword is `(N²−57)/4` = 142/292/492/742 bytes for
N = 25/35/45/55. So any size with **N² > 1080** is split into `⌊len/255⌋ + 1`
**contiguous** RS blocks — the first block taking the remainder — with the total
parity divided evenly. The message is the concatenation of each block's *data*
portion (each block is systematic, data-then-parity); decoding de-interleaves by
stripping each block's trailing parity and concatenating. Verified end-to-end: a
real 55×55 activation code (3 blocks; block-0 data = 248−66 = 182 bytes) decodes
with a valid CRC-24 only under this split — a single-block read is correct for the
first 182 bytes and then desyncs at the block boundary.

**Correction radius.** A block with `par` parity symbols has minimum distance
`par+1`, so it corrects up to `t = ⌊par/2⌋` symbol errors per block. When the
corrupt positions are **known** in advance (e.g. an overlay blanking a row/column)
they can be passed as *erasures* to `cronto.rs.correct_codeword_erasures`, which
corrects `e` erasures plus `v` unknown errors while **`2·v + e ≤ par`** — up to
`par` known-position symbols per block, twice the blind radius. Clean renders have
no symbol errors, so the CRC passes directly; on failure the decoder runs per-block
Berlekamp–Massey correction and re-checks the CRC.

**Integrity.** The last 3 bytes of the data region are a **CRC-24/OpenPGP** (RFC
4880 §6.1: poly `0x864CFB`, init `0xB704CE`, no reflection) over the preceding
message. A code with a deliberately wrong CRC (but valid RS) is rejected on a
hardware scanner, and one with more errors than the RS radius is rejected — both
the RS and the CRC layers were enforced in those device tests. The diagnostic
software decoder selects candidates by CRC and reports RS separately. When only
parity bytes are damaged, CRC can pass while RS fails; no repair is attempted on
that CRC-valid path. This behavior supports layer-isolating fault tests and does
not imply that a hardware scanner would accept the image.

On CRC failure, all supported parity variants are tried for RS repair. If no
CRC passes, the software returns the best finder match without asserting its
payload is correct. For an RS-valid, deliberately wrong-CRC code, the fallback
prefers the strongest zero-syndrome variant; otherwise it uses the primary.
Stronger codes also satisfy weaker root subsets, so this is diagnostic variant
selection, not an additional wire-level discriminator.

Search scheduling is separate from those integrity rules. Image decoding first
tries cheap CRC-only candidates so that clean screenshot codes need not wait for
repair of many incorrect crops and sizes. The normal grid decoder also probes
all eight orientations before any RS error-location work. Finder agreement
orders subsequent repair attempts without pruning orientations. Caches
distinguish an unsuccessful CRC-only probe from a completed repair attempt;
only a CRC-valid probe can satisfy a later request for repair without running
the repair search.

### 2.6 Transport de-obfuscation

The message is XORed with an **AES-128 CTR keystream** using an **all-zero key and
all-zero IV/counter** (big-endian counter from 0). Because the key is zero this is
a *fixed keystream* — a whitening/obfuscation step, not secret encryption.
`AES128(0, 0) = 66e94bd4ef8a2c3b884cfa59ca342b2e` (`cronto/aes.py` reproduces it).
The implementation caches complete immutable zero-key keystream values, so
concurrent decodes do not share a mutable counter or partially extended stream.
This is the last layer-1 step; the descrambled message is passed to the container
layer (§3), which owns any length/framing convention.

---

## 3. Message container (layer 2) — ING

**Framing.** The container layer unwraps the descrambled message (§2.6) with a
**length prefix**: for size 25 a single byte, `length = message[0]`, giving the
payload `message[1 : 1+length]`, zero-padded when `length` exceeds the message
(larger sizes use a 2-byte big-endian prefix). ING's `0x40` = 64 fits cleanly (a
64-byte payload plus trailing zeros). This is a container convention, not part of
the image format; other issuers frame the message differently (Rabobank, below).

The implementation interprets these fields as unsigned integers. A declared
length exceeding available bytes is deliberately retained for SDK-compatibility
and fault testing: `FramedMessage.synthesized_padding` records how many zero
bytes were added. Bytes remaining after an in-range payload are preserved in
`FramedMessage.trailing_bytes`, even when nonzero. Full framing performs no
length-based slicing or synthetic padding. Encoding a short full-framing input
does add zeros to fill the fixed message capacity, which decoding cannot undo.

The de-obfuscated ING payload is a 15-byte header, then a command-dependent
payload:

```
00                       byte 0     : must be 0x00 (else rejected) — a required discriminator,
                                      not the length; exact meaning unconfirmed
[cmd]                    byte 1     : command / target selector (see below)
xx xx xx xx xx           bytes 2-6  : 5-byte field — NOT validated (arbitrary accepted for the
                                      commands tested). Real ING codes carry a constant
                                      74 28 06 43 c2 here; its role is unconfirmed.
nn nn nn nn nn nn nn nn  bytes 7-14 : 8-byte field — arbitrary / per-request nonce, not validated
<payload>                bytes 15+  : command parameters (command-dependent)
```

Beyond the length prefix above, the payload container is parsed by the DIGIPASS
core, not the visual-code layer (§5). The field roles below were established by
fuzzing a physical ING scanner:

- **length framing** (`1 + length` exactly equals the non-padding byte count on
  every ING code). This also settles that byte 0 (the constant `00`) sits *inside*
  the payload and is not a length high-byte.
- **byte 1 = command selector.** The ING scanner acts on `00, 40, 41, 51, 80, C0,
  C1` and shows *"code is for another scanner"* otherwise — so byte 1 partly
  encodes which scanner/model a code targets. Identified: `C1` = login / payment /
  limit change (server-driven, **encrypted** body), `41` = confirm (**encrypted**),
  `00` = choose PIN, `C0` = device-info query (§3.1); `40 / 51 / 80` unidentified.
- **byte 0 must be `0x00`.** Any non-zero byte 0 is rejected — a required
  discriminator that, with byte 1, gates acceptance. Whether it is a version/format
  byte or a command high-byte is unconfirmed.
- **bytes 2–6 and 7–14 not validated** — arbitrary values accepted for the
  `00`/`C0` commands; field names are inferred (the visual-code layer never parses
  them).

**Encrypted class (`C1`/`41`).** The five `C1` bodies (login, three payments,
daily-limit) share no 6-byte chunk in common — every body is entirely distinct;
lengths are not multiples of 8/16 and pairwise XOR shows no zero runs. So the body
is consistent with varying per-request ciphertext, but those observations do
not establish a particular cipher, mode, nonce construction or integrity scheme.
The activation-key interpretation comes from the recorded device/SDK research,
not from ciphertext appearance alone.

**Unsigned class (`00`/`C0`).** The scanner performs these operations without
validating bytes 2-14 (verified on a physical scanner: the `C0` device-info code
runs with bytes 2-6 and 7-14 set to `11 … 11`). So `00`/`C0` codes carry no
per-message signature or MAC — static device instructions with an unauthenticated
header in the tested request templates. These observations do not establish the
authentication rules for every command or firmware variant.

**Body cipher mode (`C1`/`41`) — unresolved.** The recorded binary inventory
identifies DES/3DES/AES and ECB/CBC/CFB/CTR-related routines; GCM/CCM strings
found during that research were attributed to other bundled components. This
narrows hypotheses but does not prove absence of other constructions or a
separate MAC. Non-block-aligned ciphertext lengths do not identify CTR or CFB
uniquely. In particular, CRC-24 is not evidence that the application lacks
cryptographic integrity. Exact body encryption, authentication and key use
remain outside the implemented transport layer (§5).

**Rabobank exposes no length at this layer.** Under the *same* descramble a Rabo
payload has none of the ING header (no `00` discriminator, no `74 28 06 43 c2`).
Byte 0 is a constant format marker (`0x62` across every observed Rabo code); the
remainder shows no ING-style length prefix in the clear and reads as an opaque
blob (parsing it as an ING length-prefixed payload would mis-read `0x62` as a
length of 98). This is most likely because the Rabo body is a further **encrypted
wrapper** — the EMV card's cryptogram path (below) — so any length or structure
field would sit *inside* that ciphertext and is not recoverable without the card
key. At the visual-code layer the descrambled message is therefore taken whole;
whether it carries an internal length is unconfirmed.

**Two device architectures.** The container and key-location differences trace to
two reader designs:

- **ING** ships a *standalone* Cronto token (platform id `772` = Digipass 772)
  with its own activation key and no smartcard; it reads continuously and the `00
  [cmd] [13 unvalidated bytes] [payload]` container and the §3.1 command set belong
  to it.
- **Rabobank** ships a generic **EMV-CAP reader** with no key of its own — the
  crypto and PIN live in the inserted EMV chip card. The `0x62…` container feeds
  the card's cryptogram path.

So the "activation key" of §5 is device-resident for ING and card-resident for
Rabo. **Isabel "IsaKey"** is the same standalone Digipass Cronto token under
another brand: a cardless camera reader provisioned by an "activation image" (a
§3.2 code), confirmed same-family because an identical `00C0…` show-serial code
(§3.1) is accepted by an IsaKey unchanged.

### 3.1 Device-info query (`C0`) — ING scanner

Specific to the ING Digipass scanner; other issuers' devices may differ. A `C0`
code asks the scanner to display a device attribute; the reader's own menu labels
this family **"CDP info"**. Because `C0` is unsigned, the 5-byte field and 8-byte
nonce are arbitrary (all-`11` works):

```
00 C0 | xx xx xx xx xx | nn nn nn nn nn nn nn nn | 08 01 01 01 [SEL] 00 00
                                                               └ 1-byte query selector
```

The selector enumerates device fields (mapped by fuzzing an owned device);
read-out values are device-specific/personal and are **not reproduced here**:

| SEL | field | SEL | field |
|-----|-------|-----|-------|
| `01` | serial number | `09` | KCV (key check value) |
| `02` | production date | `0a` | platform ID |
| `04` | xtal correction \* | `0b` | firmware version |
| `05` | battery level | `0c` | firmware checksum |
| `06` | use count \* | `0d` | PIN OK count |
| `07` | PIN error count | `0e` | device ID |
| `08` | PIN status | | |

\* returns "unsupported" on the tested firmware.

This is an unauthenticated diagnostic read-out. Selector `09` returns the key
check value — it does not reveal the activation key but lets one *verify* a
candidate key against the device; the PIN counters leak lockout state.

### 3.2 Device-activation / provisioning container (size 55)

The largest grid carries a **device-activation** (Mobile Device Licensing)
message. A real 534-byte example (too large for the smaller grids) — from a public
OneSpan community-forum post and, independently, a rendered code from a Swagger
screenshot of the `visualcodes/render` API — **decodes to a valid CRC-24**
(three-block RS de-interleave, §2.5; all six application slots recovered),
confirming the pipeline against a real code at the largest size. It is a
**different container** from the ING transaction/CDP-info codes — a nested-TLV
provisioning descriptor:

```
00 00                    byte 0 = 0x00 discriminator (as §3); byte 1 = 0x00
<per-device head>        encrypted activation challenge + token serial (VDS…)  ← redacted in synthetic fixture
<global parameters>      TLVs, tags 0x04..0x47 (device capabilities / config)
<application slots>      repeated entries, each:
    15 0C <12-byte name>   application name, ASCII, space-padded to 12
    <parameter TLVs>       tags 0x16..0x2C
```

The application-slot names observed, verbatim, are the DIGIPASS application modes:
**`SC ONLINE`, `RO OFFLINE`, `CR OFFLINE`, `SG OFFLINE`, `SC OFFLINE`,
`ACTIVATION`** (abbreviation expansions such as SC = Secure Channel are inferred).
The TLV grammar (tag `0x15` = name with a one-byte length; tag/length framing of
the surrounding entries) is recorded as confirmed by parsing; the parameter
semantics (tags `0x16..0x2C`) are inferred. The synthetic payload fixture redacts
per-device fields; committed reference images can still contain their original
opaque activation data. This project does not implement a complete nested-TLV
application parser; decoded application bodies are returned as raw bytes.

---

## 4. Known constants

```
grid sizes        = {25, 35, 45, 55}
function modules  = 57
packing           = (s0<<6)|(s1<<4)|(s2<<2)|s3
NPAR by size      = 25:50/38  35:88  45:136  55:198/336
                    (25 has a known finder version bit; 55's selection is unresolved)
CRC-24/OpenPGP    = poly 0x864CFB, init 0xB704CE, no reflection
transport cipher  = AES-128-CTR, key=IV=0x00…            (obfuscation)
AES128(0,0)       = 66e94bd4ef8a2c3b884cfa59ca342b2e     (test vector)
canonical grid orientation = transpose of the documented display grid
                            (also named rot270 + horizontal flip by the orientation iterator)
```

---

## 5. Boundary — what this specification does not cover

1. **The transaction body is not decrypted here.** The `C1`/`41` body is
   encrypted with a device-bound key; only the zero-key transport layer (§2.6) is
   reversed.
2. **The transaction/OTP key is device-bound** — supplied from the token's
   activation record, established during activation, neither derived nor stored in
   the visual-code layer. The plaintext needs the specific enrolled device's key.
3. **The constant 5-byte field (`74 28 06 43 c2`) and the body's internal field
   layout** are not part of the visual-code format.

---

## 6. Encoding (generating a code)

`cronto/encoder.py` inverts the pipeline to build a valid code image from a
payload: `plaintext = [len][payload][zero-pad]` → AES-128-CTR(key=0, iv=0) →
append CRC-24 → RS-encode (per-size parameters, §2.5) → pack symbols → place the
data symbols around the 57 finder modules → orient to display → render (black
rounded border, one-module quiet zone, coloured square modules). All four sizes
are supported (`--size`), with alternate variants available through `--parity`.

Every generated codeword has zero RS syndromes in every block and round-trips
through the decoder to the original payload with a valid CRC-24, for all four
sizes (1/2/2/3 blocks), with default length framing and no injected faults.
Full framing includes fixed-capacity padding; raw-length overrides deliberately
change returned payload boundaries. Independently, the real 55×55 activation code decodes to a
valid CRC-24 with the three-block de-interleave (§2.5, §3.2).

**Caveats for a physical scanner.** Format validity (CRC + RS + finder) is
necessary but not sufficient: (a) for the *signed* types `C1`/`41` the device also
decrypts/verifies the body with its activation key, so a fabricated body is
rejected — only the *unsigned* types `00`/`C0` can be freely varied; (b) a
hardware scanner may expect one specific orientation, so a flip/rotation of the
output may be needed; (c) the finder-module values are known for all four sizes,
so generated codes pass the finder check; (d) dedicated readers use a
low-resolution (VGA) camera and in practice only handle size 25 — the ING and
Rabobank hardware scanners do not react to 35/45/55 (the "Cronto Dynamic" sizes),
which target phone-camera apps. The documented size-set distinction is stronger
evidence than attributing rejection solely to camera resolution.

**Fault injection.** `--bad-crc` changes a CRC byte before RS parity is computed.
`--rs-error N` flips exactly N parity bytes, walking backward through each
block's parity region from the final block to the first. It never crosses into
data or CRC; counts outside `0..total_parity` are rejected. `--finder-dot`
overrides a reserved registration cell in display coordinates; data-cell
overrides are rejected rather than silently overwritten by symbol placement.

**Sampling and correction provenance.** `DecodeResult.grid` and
`sampled_codeword` retain the sampled data. `corrected_grid` and `codeword`
contain the repaired result, and `corrections` lists zero-based codeword-byte
offsets and observed/corrected values. Finder cells are not RS-protected and
are preserved in both grids. Invalid inputs raise `ValueError`; expected
sampling failures use `SamplingError`. Debug diagnostics describe candidate
selection, CRC-only versus repair phase, and cache reuse. These are API
conventions, not extra image fields.

---

## 7. Sources

These references record earlier research. Automated tests in this repository do
not rerun the vendor SDKs or the physical-device experiments; optional SDK image
fixtures are tested only when supplied. Application-level observations should
not be generalized beyond the recorded samples and firmware.

- **Origin of this research** — see the project README. Exact research provenance
  (native decoder filenames, offsets, decompile file:line map, captured sample
  set, and decoder-internal sampling) is recorded privately in
  `RESEARCH-Findings.md` (excluded from public mirrors).
- [OneSpan — Secure Channel-based Transaction Data Signing](https://community.onespan.com/documentation/onespan-cloud-authentication/guides/user-guide/user/secure-channel-based-transaction-data-signing)
- [OneSpan — Digipass Cronto visual transaction signing](https://www.onespan.com/cybersecurity/products/digipass-cronto-visual-transaction-signing)
- [OneSpan community forum — "CRONTO message scan"](https://community.onespan.com/forum/cronto-message-scan): source of the real size-55 activation-message cleartext (§3.2) and of the `POST /visualcodes/render` parameters (`type=HEXSTRING`, `format=CRONTO`, `imageSize=210`).
- OneSpan **Image Generator SDK** 4.26.0 (Mobile Security Suite), Java edition — an independent ground-truth generator used to confirm the layer-1 format at all four sizes and to extract the 35/45/55 finder templates (§2.3). Docs: [Overview of the Image Generator SDK](https://docs.onespan.com/docs/image-gen-sdk-ig-overview-of-the-sdk-4-24-0), [Generate CrontoSign and Cronto Dynamic images](https://docs.onespan.com/docs/generate-crontosign-and-cronto-dynamic-images-with-the-image-generator-sdk-1).
- OneSpan **Image Scanner SDK** 5.0.0 — the vendor's decoder; run on the host to confirm the size set `{25,35,45,55}` and that it accepts `crontoencode.py` output. Docs: [Overview of the Image Scanner SDK](https://docs.onespan.com/docs/image-scan-sdk-ig-overview-of-the-sdk-4-32-0).
- Isabel **IsaKey** (same standalone Cronto token as ING, §3): [IsaKey access & signing](https://www.isabel.eu/en/secure-isabel-6-access-and-signing-with-isakey-and-itsme), [how to log in with IsaKey](https://my.isabelgroup.eu/en-US/knowledgebase/article/ka-02372), [activate IsaKey](https://support.isabel.eu/en-us/knowledgebase/article/KA-02476). Belgian-bank Cronto + Digipass 772 deployment: [OneSpan case study](https://www.onespan.com/resources/digital-bank/case-study).
- [AES — FIPS 197](https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.197.pdf)
- [CRC-24/OpenPGP — RFC 4880 §6.1](https://www.rfc-editor.org/rfc/rfc4880.html#section-6.1); parameters cross-checked against the [CRC catalogue](https://reveng.sourceforge.io/crc-catalogue/17plus.htm)
- [HMAC-SHA-256 — RFC 4868](https://www.rfc-editor.org/rfc/rfc4868)

## 8. Patents / prior art

The research notes identify the following patent publications concerning visual
transaction signing. They are historical technical context, not a determination
of current status, claim scope or applicability to this implementation. The
notes report that these publications discuss frameworks and crypto protocols
rather than specifying this byte-level transport in full.

**OneSpan / VASCO (the Cronto owner):**
- [**US 8,943,548 B2**](https://patents.google.com/patent/US8943548B2/en) —
  *System and method for dynamic multifactor authentication* (Drokov, Punskaya,
  Tahar; priority 2005-12-21; granted 2015-01-27). The foundational Cronto patent
  from the 2005 Cambridge spin-out: an encrypted message in a camera-captured
  visual code carrying transaction data to sign.
- [**US 8,966,268 B2**](https://patents.google.com/patent/US8966268B2/en) —
  *Strong authentication token with visual output of PKI signatures* (Marien;
  priority 2011-12-30; granted 2015-02-24; application
  [US 2013/0198519 A1](https://patents.google.com/patent/US20130198519A1/en)).
  The most protocol-specific: names **TLV** message structure, **DES/3DES/AES**
  in **CBC**, **MAC** (MD5/SHA-1), **RSA/ECC** signatures, QR/2D/matrix barcode,
  ISO/IEC 7816-4, and "colour coding schemes with variable redundancy mechanisms
  for error detection/correction".
- Related token/OTP framework:
  [**US 9,124,433 B2**](https://patents.google.com/patent/US9124433B2/en) (remote
  authentication and transaction signatures) and
  [**US 8,302,167 B2**](https://patents.google.com/patent/US8302167B2/en) /
  [**EP 2 252 961 B1**](https://patents.google.com/patent/EP2252961B1/en) (OTP +
  signature upon server-credential verification).

**Same technical space, *different* assignees (not OneSpan):**
- [**US 9,042,663 B2**](https://patents.google.com/patent/US9042663B2/en) —
  *Two-level error correcting codes for color space encoded image*
  (**Pictech Management Ltd**). The closest published colour-code parallel:
  N-colour cells, two-stage Reed-Solomon, CRC, black/white/"zebra" frames, colour
  calibration — but a different company.
- [**US 2012/0311320 A1**](https://patents.google.com/patent/US20120311320A1/en),
  [**US 8,868,902 B1**](https://patents.google.com/patent/US8868902B1/en) —
  "colorgram" coloured-matrix transaction tokens.

Current legal status is not established by this specification; publication and
grant dates above do not determine present enforceability.

## 9. Implementation validation and remaining limits

`python -m unittest discover -v` discovers individual tests. The compatibility
command `python -m tests.roundtrip` runs the same suite. It includes:

- Committed reference captures, including corrupted and photographed codes.
- Every supported size/parity combination, including synthetic-only variants.
- AES known answers, concurrent immutable-cache misses and counter wrapping.
- A CRC-24 known answer and randomized errors-and-erasures tests at the bound.
- Rotation and multiple trapezoidal projective captures with known payloads.
- Exact-length validation, invalid symbols, framing and CLI exit behavior.
- Correction provenance and propagation of unexpected implementation errors.
- Banque Cramer and APS Bank screenshots that must recover their fixed payloads
  without invoking RS repair search; this checks performance without a timing bound.

Vendor-generated fixtures are optional and reported as skipped when absent.
Passing synthetic tests does not prove that a physical device accepts a newly
generated request. Sampling heuristics, the size-55 variant discriminator and
application-body encryption remain subject to the limits stated above. Unknown
application fields are preserved as opaque payload bytes, not silently decoded
or discarded.
