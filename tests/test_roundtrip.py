"""Independently discoverable cases migrated from the original round-trip suite."""
from __future__ import annotations

import os
from pathlib import Path
import unittest

import numpy as np
from PIL import Image

from cronto.encoder import encode, payload_to_grid, MAX_PAYLOAD, NPAR, NPAR_BY_SIZE, max_payload
from cronto.decoder import decode_image, decode_grid
from cronto.aes import _encrypt_block, _expand_key
from cronto.symbols import function_square_runs, CODE_POINTS, grid_to_bytes
from cronto.rs import block_layout
from tests.fixtures import ACTIVATION_55, _canon_codeword, blocks_syndromes_zero, rs_syndromes_zero

_REPO = Path(__file__).resolve().parents[1]


class RoundtripTests(unittest.TestCase):
    def check(self, name: str, ok: bool) -> None:
        self.assertTrue(ok, name)

    def test_primitives(self) -> None:
        check = self.check
        check("AES128(0,0) test vector",
              _encrypt_block(b"\x00" * 16, _expand_key(b"\x00" * 16)).hex()
              == "66e94bd4ef8a2c3b884cfa59ca342b2e")
        for n in CODE_POINTS:
            copy = sum(c for _, c in function_square_runs(n))
            check(f"function-square run total N={n}", copy == n * n - 57)

        # Reed-Solomon block layout reversed from ErrorCorrection::decode
        # (non-crontolib.c:266820): N=25 single block, larger sizes interleave.
        expected_layout = {
            25: [(142, 50)],
            35: [(146, 44), (146, 44)],
            45: [(246, 68), (246, 68)],
            55: [(248, 66), (247, 66), (247, 66)],
        }
        from cronto.encoder import NPAR_BY_SIZE as _NP
        for n in CODE_POINTS:
            check(f"RS block layout N={n} == {expected_layout[n]}",
                  block_layout(n, _NP[n]) == expected_layout[n])

        # Size-25 version bit: finder cell (24,22)=622 is blue for NPAR 50, white for
        # NPAR 38, and is not checked when the version (npar) is unknown.
        from cronto.symbols import expected_finder, FINDER_TEMPLATES
        # FINDER_TEMPLATES is now generated from n by symbols._finder_template; pin it
        # to the ground-truth values (25 from ING/Rabo captures, 35/45/55 from OneSpan
        # SDK 4.26.0) so a change to the generator cannot silently drift.
        check("FINDER_TEMPLATES generated values == ground truth", FINDER_TEMPLATES == {
            25: {12: 3, 24: 3, 26: 3, 300: 3, 312: 3, 324: 3, 600: 3, 612: 3, 622: 3, 624: 3},
            35: {17: 3, 32: 3, 34: 3, 36: 2, 595: 3, 612: 3, 629: 3, 1190: 3, 1207: 3, 1224: 2},
            45: {22: 3, 42: 3, 44: 3, 46: 1, 990: 3, 1012: 3, 1034: 3, 1980: 3, 2002: 3, 2024: 1},
            55: {27: 3, 54: 2, 56: 3, 1485: 3, 1512: 3, 1539: 3, 2970: 2, 2997: 3, 3024: 3},
        })
        check("finder version cell (24,22): blue@NPAR50, white@NPAR38, skipped@None",
              expected_finder(25, 50)[622] == 3 and expected_finder(25, 38)[622] == 0
              and 622 not in expected_finder(25, None))
        # issuer-variant dot (0,22)=22 / display (22,0) is never flagged (APS Bank uses
        # blue there, ING/Rabo white).
        check("finder variant cell (22,0) excluded from the check",
              22 not in expected_finder(25, 50) and 22 not in expected_finder(25, 38))


    def test_encode_decode_round_trip_payload_crc_rs_syndromes(self) -> None:
        check = self.check
        payloads = [
            "",                                                # empty
            "00C0C3E4403D7A345530678E2BA86F08010101010000",   # show serial
            "00C0C3E4403D7A111111111111111108010101010000",   # arbitrary nonce
            "00C174280643C2" + "11" * 40,                      # ING-like
            "AB" * MAX_PAYLOAD,                                # max length
        ]
        for hx in payloads:
            exp = bytes.fromhex(hx)
            r = decode_image(encode(hx))
            cw = grid_to_bytes(np.ascontiguousarray(
                np.fliplr(np.rot90(payload_to_grid(exp), 3))))
            ok = r.crc_ok and r.payload == exp and rs_syndromes_zero(cw)
            check(f"payload {len(exp):>2d}B", ok)


    def test_encode_decode_round_trip_across_all_grid_sizes_payload_crc_rs(self) -> None:
        check = self.check
        for size in CODE_POINTS:
            mp = max_payload(size)
            npar = NPAR_BY_SIZE[size]
            finder_expected = True   # templates now known for all sizes (from OneSpan SDK 4.26.0)
            ok = True
            for payload in (b"", bytes(range(1, 1 + min(mp, 32))), bytes([0xA5]) * mp):
                r = decode_image(encode(payload.hex(), size=size), module_count=size)
                ok = ok and r.crc_ok and r.payload == payload \
                    and r.finder_ok == finder_expected \
                    and blocks_syndromes_zero(_canon_codeword(payload, size), size, npar)
            nblk = len(block_layout(size, npar))
            check(f"size {size:>2d}: empty/mid/max ({mp}B) round-trip + zero syndromes "
                  f"({nblk} RS block{'s' if nblk > 1 else ''})", ok)


    def test_real_onespan_activation_code_size_55_redacted_per_device_fields(self) -> None:
        check = self.check
        img = encode(ACTIVATION_55.hex(), size=55)
        r = decode_image(img, module_count=55, framing="length")
        check("size-55 activation payload round-trips (534B, CRC-24)",
              r.crc_ok and r.length_prefix == 534 and r.payload == ACTIVATION_55)
        check("size-55 activation: application-slot TLVs preserved through decode",
              all(name in r.payload for name in
                  (b"SC ONLINE", b"RO OFFLINE", b"CR OFFLINE", b"SG OFFLINE",
                   b"SC OFFLINE", b"ACTIVATION")))


    def test_raw_length_override_robustness_test_feature(self) -> None:
        check = self.check
        ss = "00C0C3E4403D7A345530678E2BA86F08010101010000"
        for rl in (0, 88, 130, 200, 255):
            r = decode_image(encode(ss, raw_length=rl))
            check(f"raw_length={rl:>3d} -> valid code, declared length honoured",
                  r.crc_ok and r.length_prefix == rl and len(r.payload) == rl)


    def test_fault_injection_isolates_the_crc_rs_finder_layer(self) -> None:
        check = self.check
        ss = "00C0C3E4403D7A345530678E2BA86F08010101010000"
        exp = bytes.fromhex(ss)
        r = decode_image(encode(ss, crc_error=True))
        check("--bad-crc: RS ok, CRC fails, finder ok, payload still recovered",
              r.rs_ok and not r.crc_ok and r.finder_ok and r.payload == exp)
        r = decode_image(encode(ss, rs_errors=3))
        check("--rs-error: RS syndromes nonzero, CRC ok, payload recovered",
              r.rs_ok is False and r.crc_ok and r.payload == exp)
        r = decode_image(encode(ss, finder={24: 2}))   # canonical (0,24): blue -> green
        check("--finder-dot: finder mismatch reported, CRC/RS ok",
              r.crc_ok and r.rs_ok and r.finder_ok is False
              and (24, 2, 3) in r.finder_mismatches)


    def test_reed_solomon_error_correction_repairs_corrupted_data_modules(self) -> None:
        check = self.check
        from cronto.encoder import _data_positions
        from cronto.rs import correction_radius
        for size in CODE_POINTS:
            npar = NPAR_BY_SIZE[size]
            t = correction_radius(size, npar)
            pl = bytes([0x5A]) * min(max_payload(size), 24)
            disp = payload_to_grid(pl, size=size)
            dp = _data_positions(size)                     # canonical data flat indices

            def tamper(k):                                 # flip k data modules in distinct bytes
                g = disp.copy()
                for i in range(k):
                    p = dp[i * 4]                          # canonical flat -> display (col,row)
                    g[p % size, p // size] = (int(g[p % size, p // size]) + 1) & 3
                return g

            r_ok = decode_grid(tamper(t))                  # t errors in one block -> correctable
            r_bad = decode_grid(tamper(t + 1))             # t+1 -> beyond the radius
            check(f"size {size:>2d}: corrects {t} data errors, rejects {t + 1}",
                  r_ok.crc_ok and r_ok.payload == pl and r_ok.errors_corrected == t
                  and not r_bad.crc_ok)


    def test_framing_modes(self) -> None:
        check = self.check
        ss = "00C0C3E4403D7A345530678E2BA86F08010101010000"
        img = encode(ss)                       # 22-byte payload
        rl = decode_image(img, framing="length")
        rf = decode_image(img, framing="full")
        check("length framing recovers the payload", rl.payload == bytes.fromhex(ss))
        check("full framing returns the whole 89-byte message",
              len(rf.payload) == 89 and rf.payload == rl.plaintext)
        check("full framing payload contains the length-framed payload",
              rf.payload[1:1 + len(rl.payload)] == rl.payload)
        # encoder full-framing symmetry: an 89-byte message round-trips exactly
        msg89 = bytes(range(1, 90))
        rs = decode_image(encode(msg89.hex(), framing="full"), framing="full")
        check("encode/decode full-framing symmetry (89B)",
              rs.crc_ok and rs.payload == msg89)


    def test_decode_committed_example_images_full_pipeline_incl_localiser(self) -> None:
        check = self.check
        # size-25 "show serial" / "get serial" request codes — fixed payload, no
        # device data. Includes camera photos (decoded via corner-finding + homography).
        SHOW_SERIAL = "00C0C3E4403D7A345530678E2BA86F08010101010000"
        for path in ("examples/show-serial.png", "examples/getserial3.png",
                     "examples/code-getserial1.png"):
            path = str(_REPO / path)
            if not os.path.exists(path):
                check(f"{path} present", False)
                continue
            r = decode_image(path)
            check(f"{path}: CRC + size 25 + payload + finder",
                  r.crc_ok and r.size == 25
                  and r.payload.hex().upper() == SHOW_SERIAL
                  and r.finder_ok is True)


    def test_corrupted_committed_image(self) -> None:
        check = self.check
        SHOW_SERIAL = "00C0C3E4403D7A345530678E2BA86F08010101010000"
        # A get-serial code with several data modules painted over -> RS-corrected.
        if os.path.exists(str(_REPO / "examples/getserial3-corrupted.png")):
            r = decode_image(str(_REPO / "examples/getserial3-corrupted.png"))
            check("examples/getserial3-corrupted.png: RS repairs the corrupted modules",
                  r.crc_ok and r.size == 25 and r.errors_corrected > 0
                  and r.payload.hex().upper() == SHOW_SERIAL)
        else:
            check("examples/getserial3-corrupted.png present", False)


    def test_committed_dynamic_examples(self) -> None:
        check = self.check
        # Synthetic 35x35 / 45x45 demo codes (our own encoder, correct finders). These
        # exercise the 2-block RS path and the 35/45 finder templates end to end.
        for path, want in (("examples/example-35x35.png", 35),
                           ("examples/example-45x45.png", 45)):
            path = str(_REPO / path)
            if not os.path.exists(path):
                check(f"{path} present", False)
                continue
            r = decode_image(path, framing="length")
            check(f"{path}: CRC + size {want} + 2-block RS + finder + payload",
                  r.crc_ok and r.size == want and r.finder_ok is True
                  and len(block_layout(want, r.npar)) == 2
                  and r.payload.startswith(b"Cronto"))


    def test_committed_activation_codes(self) -> None:
        check = self.check
        # Real 55x55 OneSpan activation codes (3-block interleaved RS). Assert the
        # structure, not the payload (it carries a serial): CRC, size, length prefix,
        # 3 RS blocks, and the DIGIPASS application-slot names.
        APP_SLOTS = (b"SC ONLINE", b"RO OFFLINE", b"CR OFFLINE",
                     b"SG OFFLINE", b"SC OFFLINE", b"ACTIVATION")
        for path in ("examples/55x55-code.png", "examples/onespan-55x55.png"):
            path = str(_REPO / path)
            if not os.path.exists(path):
                check(f"{path} present", False)
                continue
            r = decode_image(path)
            nblk = len(block_layout(55, r.npar))
            check(f"{path}: CRC + size 55 + {nblk}-block RS + finder + app slots",
                  r.crc_ok and r.size == 55 and r.length_prefix == 534
                  and nblk == 3 and r.finder_ok is True
                  and all(s in r.payload for s in APP_SLOTS))


    def test_optional_official_sdk_images(self) -> None:
        check = self.check
        # Official OneSpan Image Generator SDK 4.26.0 output (ground truth). These
        # images are derived from the licensed SDK and are NOT committed (see
        # .gitignore); the check is skipped when tests/generated/ is absent. Filenames
        # encode the payload byte length; the payload is the pattern (i*37+5) & 0xFF.
        import glob
        sdk_imgs = sorted(glob.glob(str(_REPO / "tests/generated/onespan-sdk-*.png")))
        if not sdk_imgs:
            self.skipTest("optional OneSpan SDK fixtures not supplied")
        else:
            print("decode official OneSpan SDK 4.26.0 images (finder/RS/CRC + exact payload):")
            for path in sdk_imgs:
                n = int(path.split("-len")[1].split(".")[0])
                exp = bytes((i * 37 + 5) & 0xFF for i in range(n))
                r = decode_image(path, framing="length")
                check(f"{os.path.basename(path)}: finder + zero syndromes + CRC + payload",
                      r.crc_ok and r.rs_ok and r.finder_ok is True and r.payload == exp)


    def test_manual_text_grid_input_rgbw_to_grid_from_text(self) -> None:
        check = self.check
        from cronto.symbols import rgbw_to_grid, grid_to_rgbw
        from cronto.grid import grid_to_str
        base = decode_image(encode("00C0C3E4403D7A345530678E2BA86F08010101010000"))
        # (a) display r/g/b/w with UPPERCASE function modules (the --colors form)
        rd = decode_grid(rgbw_to_grid(grid_to_rgbw(base.grid)), framing="length")
        check("--from-text: --colors grid decodes to the same payload",
              rd.crc_ok and rd.payload == base.payload)
        # (b) canonical .RGB form (the --grid form); orientation resolved by the search
        rc2 = decode_grid(rgbw_to_grid(grid_to_str(base.grid)), framing="length")
        check("--from-text: .RGB grid decodes to the same payload",
              rc2.crc_ok and rc2.payload == base.payload)

        def _raises(thunk):
            try:
                thunk()
                return False
            except ValueError:
                return True
        check("--from-text: non-square grid rejected", _raises(lambda: rgbw_to_grid("wrgb\nwr")))
        check("--from-text: bad character rejected", _raises(lambda: rgbw_to_grid("wxrg")))


    def test_relative_colour_classifier_degraded_capture_fallback(self) -> None:
        check = self.check
        from cronto.grid import _classify, _classify_relative
        from cronto.decoder import _decode_one
        import cronto.encoder as _enc
        check("classifier: washed-out red -> white (absolute), red (relative)",
              _classify(150, 110, 110) == 0 and _classify_relative(150, 110, 110) == 1
              and _classify_relative(255, 255, 255) == 0 and _classify_relative(0, 0, 0) == 0)
        # render a code with a desaturated palette so the absolute classifier reads the
        # coloured modules as white (decode fails); the relative fallback must recover it.
        _pl = "00C0C3E4403D7A345530678E2BA86F08010101010000"
        _saved = _enc._COLORS
        _enc._COLORS = {0: (255, 255, 255), 1: (150, 110, 110), 2: (110, 150, 110), 3: (110, 110, 150)}
        try:
            _img = _enc.encode(_pl)
        finally:
            _enc._COLORS = _saved
        _abs = _decode_one(_img, None, "length", relative=False)
        _full = decode_image(_img)          # absolute first, then relative fallback
        check("desaturated code: fails absolute, decodes via relative fallback",
              (_abs is None or not _abs.crc_ok) and _full.crc_ok
              and _full.payload == bytes.fromhex(_pl))


    def test_borderless_tight_crop_path_grid_fills_the_image_no_frame_quiet(self) -> None:
        check = self.check
        from PIL import ImageDraw
        _bl = decode_image(encode("00C0C3E4403D7A345530678E2BA86F08010101010000"))

        def _render_borderless(grid, pal, sq=11):
            n = grid.shape[0]
            im = Image.new("RGB", (n * sq, n * sq), (255, 255, 255))
            d = ImageDraw.Draw(im)
            for r in range(n):
                for c in range(n):
                    d.rectangle([c * sq, r * sq, (c + 1) * sq - 1, (r + 1) * sq - 1],
                                fill=pal[int(grid[r, c])])
            return im
        _pure = {0: (255, 255, 255), 1: (220, 0, 0), 2: (0, 176, 0), 3: (0, 0, 220)}
        _des = {0: (255, 255, 255), 1: (150, 110, 110), 2: (110, 150, 110), 3: (110, 110, 150)}
        _rp = decode_image(_render_borderless(_bl.grid, _pure))
        _rd = decode_image(_render_borderless(_bl.grid, _des))
        check("borderless code (pure palette) decodes", _rp.crc_ok and _rp.payload == _bl.payload)
        check("borderless code (desaturated) decodes via relative fallback",
              _rd.crc_ok and _rd.payload == _bl.payload)


    def test_errors_and_erasures_rs_decoding_rs_correct_codeword_erasures(self) -> None:
        check = self.check
        from cronto.rs import correct_codeword_erasures, split_message
        from cronto.symbols import data_byte_positions
        ecw = decode_image(encode("00C0C3E4403D7A345530678E2BA86F08010101010000")).codeword
        # (A) NPAR erasures (50) at known positions, no unknown errors -> full recovery
        c = bytearray(ecw)
        erA = list(range(50))
        for p in erA:
            c[p] ^= 0x5A
        rA = correct_codeword_erasures(bytes(c), 25, 50, erA)
        check("errata: 50 erasures (= NPAR) fully recovered",
              rA is not None and rA[0] == ecw and split_message(rA[0], 25)[2])
        # (B) 40 erasures + 5 unknown errors (2v+e=50) recovered; a 6th error (52>50) rejected
        c = bytearray(ecw)
        erB = list(range(40))
        for p in erB:
            c[p] ^= 0x33
        for p in (60, 70, 80, 90, 100):
            c[p] ^= 0x11
        rB = correct_codeword_erasures(bytes(c), 25, 50, erB)
        c6 = bytearray(c)
        c6[110] ^= 0x22
        rB6 = correct_codeword_erasures(bytes(c6), 25, 50, erB)
        check("errata: 40 erasures + 5 errors (2v+e=50) recovered; +6th (52>NPAR) rejected",
              rB is not None and rB[0] == ecw and rB6 is None)
        # (C) map known-erased canonical cells -> byte positions, then decode
        cells = [5 * 25 + 7, 10 * 25 + 4, 20 * 25 + 21, 3 * 25 + 17]
        ebC = data_byte_positions(25, cells)
        c = bytearray(ecw)
        for b in ebC:
            c[b] ^= 0x7E
        rC = correct_codeword_erasures(bytes(c), 25, 50, ebC)
        check("errata: cell->byte erasures (data_byte_positions) recovered",
              rC is not None and rC[0] == ecw and len(ebC) >= 1)
        # (D) multi-block errata (size 35 = 2 RS blocks of 146B/44par): erasures + errors per block
        b35 = decode_image(encode("00C0C3E4403D7A345530678E2BA86F08010101010000", size=35),
                           module_count=35).codeword
        cD = bytearray(b35)
        erD = list(range(10, 40)) + list(range(160, 190))          # 30 erasures in each block
        for p in erD:
            cD[p] ^= 0x5A
        for p in (50, 55, 60, 65, 70, 200, 205, 210, 215, 220):    # 5 unknown errors in each block
            cD[p] ^= 0x11
        rD = correct_codeword_erasures(bytes(cD), 35, 88, erD)
        check("errata: multi-block (35, 2 blocks) recovers 30 erasures + 5 errors per block",
              rD is not None and rD[0] == b35)


    def test_localiser_decode_a_rotated_render(self) -> None:
        check = self.check
        img = encode("00C0C3E4403D7A345530678E2BA86F08010101010000")
        canvas = Image.new("RGB", (img.width + 160, img.height + 160), (255, 255, 255))
        canvas.paste(img, (80, 80))
        rot = canvas.rotate(23, expand=True, fillcolor=(255, 255, 255), resample=Image.BICUBIC)
        r = decode_image(rot)
        check("rotated 23deg round-trip",
              r.crc_ok and r.payload.hex().upper()
              == "00C0C3E4403D7A345530678E2BA86F08010101010000")




if __name__ == "__main__":
    unittest.main()
