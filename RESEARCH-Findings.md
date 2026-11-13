# Research findings — reverse-engineering of the binary

> **Not for public release.** This file collects material that comes from
> reverse-engineering the proprietary `libQRCronto.so` binary — the crypto
> inventory, extracted static keys, white-box key streams, and the device's OTP algorithm. It is
> relevant to the *reverse engineering*, **not** to the colour-code format
> itself (that's `FORMAT.md`), and it contains a key lifted from the binary.
> Keep it in the private research repo only; **exclude it when publishing a
> public mirror**. The rest of the project (`FORMAT.md`, the decoder/encoder) is
> a clean-room reimplementation with no such material.

## 1. Crypto inventory in the library **[decompile]**

| Layer | Algorithm | Key | Purpose |
|---|---|---|---|
| Transport | AES-128-CTR | all-zero (`crontolib.c:59`) | obfuscation |
| SDK config ("HSE") | AES-256-CTR | static, embedded (§2) | unwrap internal config strings |
| OTP / signature | DES / 3DES CBC-MAC · SHA-256 word-fold · HMAC-SHA-256 · hash truncation (`crontolib.c:2473`) | caller-supplied token key | response generation (§3) |

Primitives: DES block+schedule `crontolib.c:1426`/`1376`; AES block
`crontolib.c:7633`/`7820`; SHA-256 `crontolib.c:11829` (IV `0x6A09E667…`);
HMAC-SHA-256 `crontolib.c:11885` (ipad/opad `0x36`/`0x5c`).

## 2. Static AES-256 "HSE" key **[verified — read from binary]**

Referenced at `.data 0x1DA478` (key) / `0x1DA498` (IV) in `crontolib.c:1023–1032`,
used by `UtilitiesSDK_ikXfulM5lD:994` to AES-256-CTR-decrypt hex config strings.
Bytes read from `lib/armeabi-v7a/libQRCronto.so`:

```
key = dd17d6188d7eefd5defc7063bb9494d712d409202b8acb1d83b15218d4e4a7c0
iv  = 091b12427276e9ce55b69e23c455b14d
```

This decrypts SDK-internal configuration only; it does **not** decrypt the
transaction body (see `FORMAT.md §3`).

**Test result [verified]:** the key was tried against every embedded hex
constant in the app — 625 hex literals in the decompiled Java, the `.so` rodata
(no hex strings present), and the 8,613-byte `res/raw/signature` blob. **None
decrypt to meaningful plaintext.** The reason is structural: `DecodeCrontoImage`
is the *only* native method the app's Java declares, and `UtilitiesSDK_ikXfulM5lD`
is never called anywhere in the decompiles — the HSE string-decryptor is generic
Vasco Utilities-SDK code compiled into the library but **unused by this app**, so
no HSE ciphertext ships with it. The key is genuine (confirmed by the named data
symbols at `0x1DA478`/`0x1DA498`); there is simply no corresponding data here.
`res/raw/signature` is a separate high-entropy artifact (a code-signing/licensing
signature under a different key), not HSE-protected.

## 3. Signature / response generation **[decompile]**

The device computes the response (a 7-digit OTP) over the decrypted transaction
fields:

1. Build the data-to-sign from up to **8 data fields, each ≤66 bytes**
   (`gen_crypto_sm_id_msg:3611`), gated by a 32-bit flags word: `0x8000` → an
   8-byte field (time/counter), `0x2000` and `0x1000` → 4-byte fields.
2. Compute a MAC over it — DES/3DES CBC-MAC, SHA-256 word-fold, or a
   hash-truncation, selected by flag bits (`cCvyCyPPbCVjjlYGMbQU:2473`).
3. Decimalise, truncate to the configured digit count, and fold in an optional
   Luhn-style check digit (`OGaeeGmzIsoTlpFdOBSp:3675`).

Host-side verification (`ovVlnlBCyZPdiyYXRVah:4671`) brute-forces a time-drift ×
event-counter grid, regenerating a candidate each step and comparing.

The MAC/encryption key is **passed in from the caller** (the DIGIPASS token /
activation record); no key derivation or literal key exists in these decompiles.

## 4. Java Utilities-SDK edition — embedded keys and white-box **[verified — run on the shipped SDK]**

Obtained by running the vendor's own `UtilitiesSDK.jar` (classpath: Kryo 5.6.2 +
bcprov-jdk18on 1.81 — 1.81 needed because ≥1.84 dropped
`org.bouncycastle.pqc.legacy.math.linearalgebra`, which the white-box's
`GF2MatrixEx` extends). **No key-extraction was required** — the white-box is a
callable oracle.

### 4.1 Second static AES-256-CTR key (`xsVUhL4q6C`)

`UtilitiesSDK.xsVUhL4q6C(String)` assembles six private 8-byte arrays (`a..f`)
into a 32-byte key + 16-byte IV and AES-256-CTR-decrypts a hex string to UTF-8.
`@Deprecated`, called nowhere, and no ciphertext ships — the Java-edition twin of
the native HSE string-decryptor (§2), with a **different** value (mechanism 3 =
AES, mode 4 = CTR, confirmed against the JDK ciphers):

```
key = 25950767bd0202821dfbc71286a813a82b2cb404a27d171c11487b6381076476
iv  = 2d916b88c324fbdc0192b4b855573582
```

### 4.2 White-box AES-128 (Chow / ph4r05) — master and platform keys

`UtilitiesSDKWhiteBoxCryptography` wraps ph4r05's `cz.muni.fi.xklinex.whiteboxAES`
(Chow-2002 table-based white-box AES-128 with external encodings). Two
Kryo-serialized tables ship:

- `encrypt_master_key_tables.dat` — sha256 `55dcbad2…`, 1,285,747 B; **identical**
  across the CrontoSign app, photoTAN app, and the SDK ⇒ SDK-global. Used by
  `(de/en)cryptAESCTRWithMasterKey` (AES-CTR).
- `encrypt_platform_key_tables.dat` — sha256 `d832ec32…`, 1,285,849 B; in both
  apps, absent from the SDK download. Used by `encryptAESBlock` (raw single block)
  for device binding (`obfuscated.m.b`).

Each hides a 128-bit key. Fingerprints and streams, computed by running the
shipped tables:

```
MASTER   E_K(0^16)            = 3a48265c1e0463f12eb7e27879b6e531
MASTER   CTR keystream IV=0   = 3a48265c1e0463f12eb7e27879b6e531f300dd04668e6b3614f022c4a6370a5f
                                43dae82115bca33dc2c1f024aecbd390c80e6211e2ea6d2d467ba2773f32e28d
MASTER   CTR keystream IV=c.b = 3a242639f3a4e5d0bfbb5f3edf373c74f0e390aebeec6d0dc67ac934e88edde7
                                b3574d213142c5dd32b4cb9f2c3a7a889ef05c1e25ed339f172ff874af54013b
MASTER   CTR keystream IV=c.a = e4a198a064ed2a9073fcd567c42ca0adfc1e970d4c951a99b80b93152129b0ff
                                6ca53ae05dd17db8c58aad675dc436b04deec15e56af5b5459be6b41fd4903ce
PLATFORM E_K(0^16)            = fba2dd25c406f5c7cd628ad6eae559d7
PLATFORM CTR keystream IV=0   = fba2dd25c406f5c7cd628ad6eae559d724a122ba0143e3aab6497a510172bf9f
                                f2e8444490c97782d3c73ab13a78d3efba710c8f40453a582b9c3cc19dac9ba4
```

(`c.b = 10b48607851a87698f2aca52cfa2b118`, `c.a = f2e3decf39443a5739fa1dde3d5ae5d6`
are the static IVs hardcoded in `obfuscated.c`.)

### 4.3 Master-key-protected static secret

`obfuscated.c`/`l` decrypt an embedded 64-byte blob with the master key (IV = c.b)
and feed it as the salt of the activation/storage `deriveKey`:

```
ciphertext = b3ed8fb496b1ff8567b26178583eb91f01d853e5d7b8b63f75c2c50ed6fc3e80
             300d986dd3a70a66c65a48f02127111a7b5b260a88e0911561b6ff873f90d5d8   (hardcoded, SDK-global)
plaintext  = 89c9a98d65151a55d8093e468709856bf13bc34b6954db32b3b80c3a3e72e367
             835ad54ce2e5cfbbf4ee836f0d1d6b92e5ab7a14ad0da28a769907f390c4d4e3
```

### 4.4 Assessment — the white-box adds ~nothing here

Every master-key use is **AES-CTR**, so ciphertext = plaintext ⊕ keystream(IV):
only the keystream is ever needed, never the key. The white-box is a callable
oracle, so anyone holding the app can produce the keystream for any IV (done
above); extracting the key (a BGE attack — Chow is broken) buys nothing for CTR.
Because the IVs are static constants and the key is SDK-global, the few
precomputed pads in §4.2 fully replace the master white-box **across every
deployment**. The platform key's `encryptAESBlock` use is raw ECB over a variable
input, so a single pad does not collapse it — but it stays a per-input oracle, so
the key is still never required. Net: the white-box gives obfuscation (stops a
grep-the-binary attacker lifting a literal key) but not confidentiality, and zero
per-device / per-bank diversity — a shared default in the activation/storage path.

## 5. FORMAT.md provenance — decompile file:line map **[decompile]**

`FORMAT.md` (the clean-room spec) names the decompiled routines but not the
private pathnames/line numbers. The full map lives here.

Artifacts (all private, not in the repo):
- IDA/Hex-Rays decompiles of the native decoder: `crontolib.c`, `non-crontolib.c`.
- Obfuscated Java SDK decompile under `3/`.
- Binary: `lib/armeabi-v7a/libQRCronto.so`; static HSE key/iv read from `.data`
  `0x1DA478` / `0x1DA498` (§2).
- Captured validation codes: `rabo-login-code*.png`, `ing-inlog.png`, and two ING
  transaction codes cropped as `ing-txn-*.png`.

| FORMAT.md fact (routine) | citation |
|---|---|
| §2.1 grid sizes — `ImageDecoder::codePoints` | `non-crontolib.c:1482` |
| §2.1 size measurement vs codePoints | `non-crontolib.c:266476` |
| §2.1 four-quadrant sampling — `getSubmoduleSequence` | `non-crontolib.c:268662` |
| §2.1 quadrant seam (shared centre row/col) | `non-crontolib.c:267960` |
| §2.1 data capacity `(N²−57)/4` | `non-crontolib.c:266347` |
| §2.2 colour→symbol — `ImageDecoder::hsvColorDetection` | `non-crontolib.c:269051` |
| §2.3 function-module removal — `ImageDecoder::removeFunctionSquares` | `non-crontolib.c:269126` |
| §2.4 byte packing — `convertTo8BitSymbols` | `non-crontolib.c:268756` |
| §2.5 RS decode — `ErrorCorrection::decode` → `decodeCodeword` | `non-crontolib.c:266797`, `266859` |
| §2.5 multi-block split (N²>1080) | `non-crontolib.c:266820-266852` |
| §2.5 NPAR/version bit — `setNParityBytes`, `dword_D0048` | `non-crontolib.c:267959`, `:1483`, `:267949` |
| §2.5 CRC-24 — `ErrorDetection` | `non-crontolib.c:266923` |
| §2.6 cipher dispatch — `DecodeCrontoImage` | `crontolib.c:59` |
| §2.6 key/iv all-zero | `crontolib.c:51-58` |
| §2.6 CTR core — `egDOPlwIMmodlQfXVByw` | `crontolib.c:68-78`, `egDOPlwIMmodlQfXVByw:753`, `SqDysvHwtuNoJmLlRTWw:205` |

## 6. Decoder sampling internals (not part of the format) **[decompile]**

These are *decoder implementation* details of the native library, deliberately
kept out of `FORMAT.md` (which describes the on-wire format only).

**Four-quadrant projective sampling.** `getSubmoduleSequence` does not read one
uniform N×N lattice; it rectifies the captured image in four `⌈N/2⌉`-wide
quadrants, each fit to nearby registration squares of the 3×3 lattice (the direct
analogue of a QR code's alignment patterns — interior reference points that keep
sampling accurate when a cheap-camera / angled / slightly-warped capture does not
map linearly across the whole code). The four quadrants share the centre row and
column, so a side is `2·⌈N/2⌉ − 1 = N` modules (a *consequence* of the odd-size
rule, not its cause — the cause is finder symmetry, `FORMAT.md §2.1`).

The overlap is a sampling artefact, not a grid artefact: the quadrants write into
the **same cells** of a plain N×N buffer (`alloc_heap_buf(N*N)`) — the seam is
sampled twice but stored once (18 data modules at N=25, 48 at N=55; each is one
buffer cell, read once). So a clean, flat, upright image reads correctly as a
single uniform N×N lattice with no overlap to account for, which is what `cronto/`
does — a global homography in `locate.py` handles rotation/perspective instead,
then it samples the plain grid.

**Internal reading frame.** `removeFunctionSquares` walks the grid row-major in
the library's internal frame, which is the display grid transposed across the main
diagonal (`canonical = displayᵀ` = `rot270 + horizontal flip`). `cronto/` picks
this orientation by trying all 8 dihedral orientations with the CRC/finder as the
oracle, so the format description needs no fixed internal frame.

**Independent confirmation (Commerzbank photoTAN build).** A newer, un-obfuscated
Java build of the same SDK (`com.vasco.digipass.sdk.utils.qrcodescanner.QRCodeScannerSDKJNIWrapper`,
which ships the identical `libQRCronto.so`) implements `DecodeCrontoImage` in
readable Java and matches every FORMAT.md step: native `PredecodeCrontoImage`
(RS + CRC) returns the processed bytes and codePoints; `Cipher.getInstance(
"AES/CTR/NoPadding")` with a 16-byte zero key and zero IV descrambles them; then
the length-prefix slice `Arrays.copyOfRange(bArr, off, off+length)` and
uppercase-hex encoding produce the result.

**Native routine names** referenced by the FORMAT.md provenance map (§5):
`ImageDecoder::decode` / `getSubmoduleSequence` / `hsvColorDetection` /
`removeFunctionSquares` / `convertTo8BitSymbols`; `ErrorCorrection::decode` →
`decodeCodeword`; `getVersion` / `setNParityBytes` / `dword_D0048`;
`ErrorDetection` (CRC); `DecodeCrontoImage` / `HAkzVUkcmmaeTTlXtKlM` (cipher
dispatch) / `egDOPlwIMmodlQfXVByw` (CTR core) / `SqDysvHwtuNoJmLlRTWw` (framing).
