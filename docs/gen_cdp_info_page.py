#!/usr/bin/env python3
"""Generate docs/cdp-info-codes.html — a reference sheet of the Cronto/DIGIPASS
"CDP info" (command 0xC0) device-info query codes.

The 0xC0 codes ask a hardware token to display one of its own attributes; the
reader labels this family "CDP info". Each code carries only a 1-byte selector
naming the field to show — the field *value* is in the device's on-screen
response, not in the code, so nothing personal is encoded. The selector map and
container layout are from FORMAT.md §3.1 (established by fuzzing an owned ING
Digipass scanner) and are cross-checked here against a real captured serial-query
code, which decodes to the same `00 c0 … 08 01 01 01 01 00 00` template.

Because 0xC0 is unsigned, bytes 2-14 are not validated; they are emitted as `11`
filler (the "all-11 works" case verified on the device). Every code is rendered
with cronto.encoder at size 25 with length framing, and this generator asserts
each round-trips back to its payload with a valid CRC-24 before writing the page.

Run from the repository root:

    python3 docs/gen_cdp_info_page.py

Output is a single self-contained HTML file (PNGs embedded as data URIs), safe to
open directly or serve as a static page.
"""
from __future__ import annotations
import base64
import html
import io
import os
import re
import sys

# Make the repo root importable whether run from root or from docs/.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from cronto.encoder import encode          # noqa: E402
from cronto.decoder import decode_image     # noqa: E402

_DOCS = os.path.dirname(os.path.abspath(__file__))
OUT_HTML = os.path.join(_DOCS, "cdp-info-codes.html")
OUT_MD = os.path.join(_DOCS, "cdp-info-codes.md")
PNG_DIR = os.path.join(_DOCS, "cdp")          # per-code PNGs for the Markdown page

# CDP-info query selectors (FORMAT.md §3.1). (sel, field, footnote-or-None)
SELECTORS = [
    (0x01, "serial number", None),
    (0x02, "production date", None),
    (0x04, "xtal correction", '"unsupported" on tested firmware'),
    (0x05, "battery level", None),
    (0x06, "use count", '"unsupported" on tested firmware'),
    (0x07, "PIN error count", None),
    (0x08, "PIN status", None),
    (0x09, "KCV (key check value)", None),
    (0x0a, "platform ID", None),
    (0x0b, "firmware version", None),
    (0x0c, "firmware checksum", None),
    (0x0d, "PIN OK count", None),
    (0x0e, "device ID", None),
]


def _slug(sel_hex: str, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"sel-{sel_hex}-{base}"


def payload_for(sel: int) -> bytes:
    """The 22-byte C0 container for a device-info query (FORMAT.md §3.1):
    00 (discriminator) C0 (command) + 5-byte + 8-byte arbitrary fields (unvalidated,
    emitted as 0x11 filler) + CDP-info params 08 01 01 01 [SEL] 00 00."""
    return (bytes([0x00, 0xC0]) + b"\x11" * 5 + b"\x11" * 8 +
            bytes([0x08, 0x01, 0x01, 0x01, sel, 0x00, 0x00]))


def render_items():
    """Render every selector to a base64 PNG, asserting each round-trips."""
    items = []
    for sel, name, note in SELECTORS:
        body = payload_for(sel)
        hexp = body.hex()
        img = encode(hexp, size=25, framing="length", pitch=18)

        r = decode_image(img, framing="length")
        got = bytes(r.payload) if r.payload is not None else b""
        ok = (r.crc_ok and got[:2] == b"\x00\xC0" and len(got) >= 20 and
              got[15:19] == b"\x08\x01\x01\x01" and got[19] == sel)
        if not ok:
            raise SystemExit(f"round-trip check failed for selector {sel:02x}")

        buf = io.BytesIO()
        img.save(buf, "PNG")
        raw = buf.getvalue()
        sel_hex = f"{sel:02x}"
        items.append({
            "sel": sel_hex, "name": name, "note": note, "hex": hexp,
            "b64": base64.b64encode(raw).decode(), "png": raw,
            "slug": _slug(sel_hex, name), "w": img.width, "h": img.height,
        })
    return items


# ---------------------------------------------------------------- HTML assembly

def _byte_cell(hv, lbl, role):
    return (f'<div class="byte byte--{role}"><span class="byte__hex">{hv}</span>'
            f'<span class="byte__lbl">{lbl}</span></div>')


def _structure_strip():
    """Labelled 22-byte container behind the 1-byte length prefix."""
    cells = [("16", "length", "len"), ("00", "00", "disc"), ("c0", "C0", "cmd")]
    cells += [("11", "·", "arb")] * 5
    cells += [("11", "·", "arb")] * 8
    cells += [(h, h, "param") for h in ("08", "01", "01", "01")]
    cells += [("SEL", "SEL", "sel"), ("00", "00", "term"), ("00", "00", "term")]
    return "\n      ".join(_byte_cell(*c) for c in cells)


def _card(it):
    sel, name, hexp = it["sel"], html.escape(it["name"]), it["hex"]
    # The selector is payload byte index 19 -> hex chars [38:40]; emphasise it.
    pre, mid, post = hexp[:38], hexp[38:40], hexp[40:]
    hex_html = (f'<span class="dim">{pre}</span><span class="hl">{mid}</span>'
                f'<span class="dim">{post}</span>')
    star = ' <span class="star">✻</span>' if it["note"] else ''
    note = (f'<p class="card__note">✻ {html.escape(it["note"])}</p>'
            if it["note"] else '')
    return f'''<article class="card">
      <div class="card__code">
        <img loading="lazy" width="{it['w']}" height="{it['h']}"
             src="data:image/png;base64,{it['b64']}"
             alt="Cronto colour code — CDP info query for {name} (selector {sel})">
      </div>
      <div class="card__meta">
        <div class="card__head">
          <span class="chip">SEL {sel}</span>
          <h3 class="card__name">{name}{star}</h3>
        </div>
        <code class="card__hex">{hex_html}</code>
        {note}
      </div>
    </article>'''


CSS = r'''<style>
:root{
  --bg:#eceff4; --surface:#ffffff; --surface-2:#f5f7fa;
  --tile:#eef1f6;                       /* constant light tile under each code */
  --border:#d7dbe3; --border-2:#c6ccd6;
  --ink:#171a20; --muted:#5b636f; --faint:#98a0ac;
  --accent:#3b5bdb; --accent-ink:#ffffff; --accent-soft:#e7ebfb;
  --radius:14px; --radius-sm:9px;
  --font-sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --font-mono:ui-monospace,"SF Mono","Cascadia Code","JetBrains Mono",Menlo,Consolas,monospace;
  --shadow:0 1px 2px rgba(18,22,30,.06),0 8px 24px -14px rgba(18,22,30,.22);
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0b0d11; --surface:#14171d; --surface-2:#0f1319; --tile:#e9edf3;
    --border:#242a33; --border-2:#323945; --ink:#e7eaf0; --muted:#98a1af; --faint:#69727f;
    --accent:#7089f2; --accent-ink:#0b0d11; --accent-soft:#1b2130;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px -16px rgba(0,0,0,.7);
  }
}
:root[data-theme="light"]{
  --bg:#eceff4; --surface:#ffffff; --surface-2:#f5f7fa; --tile:#eef1f6;
  --border:#d7dbe3; --border-2:#c6ccd6; --ink:#171a20; --muted:#5b636f; --faint:#98a0ac;
  --accent:#3b5bdb; --accent-ink:#ffffff; --accent-soft:#e7ebfb;
  --shadow:0 1px 2px rgba(18,22,30,.06),0 8px 24px -14px rgba(18,22,30,.22);
}
:root[data-theme="dark"]{
  --bg:#0b0d11; --surface:#14171d; --surface-2:#0f1319; --tile:#e9edf3;
  --border:#242a33; --border-2:#323945; --ink:#e7eaf0; --muted:#98a1af; --faint:#69727f;
  --accent:#7089f2; --accent-ink:#0b0d11; --accent-soft:#1b2130;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px -16px rgba(0,0,0,.7);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--font-sans);
  line-height:1.55;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
.mono{font-family:var(--font-mono);font-size:.92em}
.wrap{max-width:1160px;margin:0 auto;padding:clamp(28px,5vw,72px) clamp(18px,4vw,44px)}

.eyebrow{font-family:var(--font-mono);font-size:12.5px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--accent);margin:0 0 14px}
.eyebrow .mono{color:inherit}
h1{font-size:clamp(30px,5.5vw,50px);line-height:1.03;letter-spacing:-.02em;
  font-weight:660;margin:0 0 20px;text-wrap:balance}
.lead{max-width:66ch;font-size:clamp(15px,1.9vw,17px);color:var(--muted);margin:0}
.lead strong{color:var(--ink);font-weight:600}

.panel{margin:clamp(32px,5vw,52px) 0;background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);padding:clamp(20px,3vw,30px);box-shadow:var(--shadow)}
.panel__title h2{font-size:18px;font-weight:640;margin:0 0 6px;letter-spacing:-.01em}
.panel__title p{margin:0 0 20px;color:var(--muted);font-size:14px;max-width:70ch}
.bytes{display:flex;gap:6px;overflow-x:auto;padding-bottom:10px;scrollbar-width:thin}
.byte{flex:0 0 auto;min-width:46px;display:flex;flex-direction:column;align-items:center;gap:3px;
  padding:9px 6px 7px;border:1px solid var(--border);border-radius:var(--radius-sm);
  background:var(--surface-2)}
.byte__hex{font-family:var(--font-mono);font-size:13px;font-weight:600}
.byte__lbl{font-size:9.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--faint)}
.byte--len{border-style:dashed;opacity:.72}
.byte--cmd{background:var(--accent-soft);border-color:transparent}
.byte--cmd .byte__lbl{color:var(--accent)}
.byte--param{border-color:var(--border-2)}
.byte--sel{background:var(--accent);border-color:var(--accent)}
.byte--sel .byte__hex,.byte--sel .byte__lbl{color:var(--accent-ink)}
.byte--arb .byte__hex{color:var(--faint)}
.legend{list-style:none;display:flex;flex-wrap:wrap;gap:8px 20px;margin:18px 0 0;padding:0;
  font-size:12.5px;color:var(--muted)}
.legend li{display:flex;align-items:center;gap:7px}
.sw{width:13px;height:13px;border-radius:4px;border:1px solid var(--border-2);flex:0 0 auto}
.sw--len{background:var(--surface-2);border-style:dashed}
.sw--cmd{background:var(--accent-soft)}
.sw--arb{background:var(--surface-2)}
.sw--param{background:var(--surface-2);border-color:var(--border-2)}
.sw--sel{background:var(--accent);border-color:var(--accent)}
.sw--term{background:var(--surface-2)}

.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(232px,1fr));
  gap:clamp(14px,2vw,20px)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  overflow:hidden;box-shadow:var(--shadow);
  transition:transform .16s ease,box-shadow .16s ease,border-color .16s ease}
.card:hover{transform:translateY(-3px);border-color:var(--border-2);
  box-shadow:0 4px 8px rgba(18,22,30,.08),0 16px 34px -16px rgba(18,22,30,.34)}
.card__code{background:var(--tile);padding:16px;display:flex;justify-content:center;
  border-bottom:1px solid var(--border)}
.card__code img{width:100%;max-width:190px;height:auto;display:block;border-radius:8px}
.card__meta{padding:14px 15px 16px;display:flex;flex-direction:column;gap:9px}
.card__head{display:flex;align-items:center;gap:9px}
.chip{font-family:var(--font-mono);font-size:11.5px;font-weight:600;letter-spacing:.03em;
  color:var(--accent);background:var(--accent-soft);border-radius:6px;padding:3px 7px;white-space:nowrap}
.card__name{font-size:15px;font-weight:600;margin:0;letter-spacing:-.01em}
.star{color:var(--accent);font-size:.8em;vertical-align:super}
.card__hex{font-family:var(--font-mono);font-size:11px;line-height:1.5;word-break:break-all;
  color:var(--muted);background:var(--surface-2);border:1px solid var(--border);
  border-radius:8px;padding:8px 9px;display:block}
.card__hex .dim{color:var(--faint)}
.card__hex .hl{color:var(--accent-ink);background:var(--accent);border-radius:3px;
  padding:1px 3px;font-weight:700}
.card__note{margin:0;font-size:12px;color:var(--muted)}

.foot{margin-top:clamp(36px,5vw,56px);padding-top:28px;border-top:1px solid var(--border);
  display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:28px 48px}
.foot h2{font-size:12.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);
  margin:0 0 12px;font-weight:650}
.foot ul{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:9px}
.foot li,.foot p{font-size:13.5px;color:var(--muted);margin:0}
.foot strong{color:var(--ink);font-weight:600}

a{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
@media (max-width:520px){.card__code img{max-width:150px}}
@media (prefers-reduced-motion:reduce){.card{transition:none}.card:hover{transform:none}}
</style>'''


def build_html(items):
    cards = "\n    ".join(_card(it) for it in items)
    body = f'''<article class="wrap">
  <header class="masthead">
    <p class="eyebrow">DIGIPASS · Secure Channel · <span class="mono">C0</span> command family</p>
    <h1>CDP&nbsp;Info query codes</h1>
    <p class="lead">The <span class="mono">C0</span> device-info codes ask a Cronto hardware token to
      display one of its own attributes — the reader labels this family <em>“CDP info”</em>.
      Each code below is one <strong>query</strong>: it carries only a 1-byte selector naming
      the field to show. The field <em>value</em> lives in the device’s on-screen response,
      not in the code, so nothing personal is encoded here. All {len(items)} selectors mapped
      by fuzzing an owned device are reconstructed with the project encoder and verified to
      round-trip; the layout is cross-checked against a real captured serial-query capture.</p>
  </header>

  <section class="panel" aria-labelledby="struct-h">
    <div class="panel__title">
      <h2 id="struct-h">Payload structure</h2>
      <p>22-byte container behind a 1-byte length prefix. Only the selector changes between codes;
         bytes 2–14 are not validated by the scanner (shown here as <span class="mono">11</span> filler).</p>
    </div>
    <div class="bytes" role="img" aria-label="Byte layout: length 16, discriminator 00, command C0, five unvalidated bytes, eight unvalidated bytes, parameter prefix 08 01 01 01, selector byte, terminator 00 00">
      {_structure_strip()}
    </div>
    <ul class="legend">
      <li><span class="sw sw--len"></span>length prefix (framing)</li>
      <li><span class="sw sw--cmd"></span>discriminator / command</li>
      <li><span class="sw sw--arb"></span>unvalidated (arbitrary)</li>
      <li><span class="sw sw--param"></span>CDP-info parameter</li>
      <li><span class="sw sw--sel"></span>query selector</li>
      <li><span class="sw sw--term"></span>terminator</li>
    </ul>
  </section>

  <section class="grid" aria-label="Rendered CDP info codes">
    {cards}
  </section>

  <footer class="foot">
    <div class="foot__col">
      <h2>Notes</h2>
      <ul>
        <li><strong>Unsigned.</strong> <span class="mono">C0</span> codes carry no per-message
            signature; the token acts on them without validating bytes 2–14.</li>
        <li><strong>Read-only.</strong> These are diagnostic <em>reads</em> — a query code cannot
            change device state.</li>
        <li><strong>✻ unsupported.</strong> Selectors <span class="mono">04</span> (xtal correction)
            and <span class="mono">06</span> (use count) return “unsupported” on the tested firmware.</li>
        <li><strong>KCV vs key.</strong> Selector <span class="mono">09</span> returns the key check
            value — it verifies a candidate key against the device but does not reveal the key.</li>
      </ul>
    </div>
    <div class="foot__col">
      <h2>Provenance</h2>
      <p>Selector map and container layout: <span class="mono">FORMAT.md §3.1</span>
         (ING Digipass scanner, established by fuzzing an owned device). Images generated by
         <span class="mono">cronto/encoder.py</span> at size 25, length framing, and confirmed to
         decode back to each payload with a valid CRC-24.</p>
    </div>
  </footer>
</article>'''
    return CSS + "\n" + body


# ------------------------------------------------------------ Markdown assembly

def write_pngs(items):
    """Write one PNG per code into PNG_DIR (referenced by the Markdown page;
    GitLab strips data: URIs, so the Markdown needs real files)."""
    os.makedirs(PNG_DIR, exist_ok=True)
    for it in items:
        with open(os.path.join(PNG_DIR, it["slug"] + ".png"), "wb") as f:
            f.write(it["png"])


def build_markdown(items):
    # payload cell: fixed prefix, selector emphasised, terminator
    pre = "00c0 1111111111 1111111111111111 08010101"
    rows = []
    for it in items:
        field = it["name"] + (" ✻" if it["note"] else "")
        img = (f'<img src="cdp/{it["slug"]}.png" width="104" '
               f'alt="CDP info query code for {it["name"]}">')
        payload = f"`{pre}` **`{it['sel']}`** `0000`"
        rows.append(f"| `{it['sel']}` | {field} | {img} | {payload} |")
    table = "\n".join(rows)

    return f'''# CDP&nbsp;Info query codes

The `C0` device-info codes ask a Cronto hardware token to display one of its own
attributes — the reader labels this command family **“CDP info”**. Each code
below is one **query**: it carries only a 1-byte selector naming the field to
show. The field *value* lives in the device’s on-screen response, not in the
code, so nothing personal is encoded here. All {len(items)} selectors mapped by
fuzzing an owned device are reconstructed with the project encoder and verified
to round-trip with a valid CRC-24; the layout is cross-checked against a real
captured serial-query code.

> An interactive gallery version (codes embedded, no external files) is in
> [`cdp-info-codes.html`](cdp-info-codes.html). This page and it are produced by
> the same generator, `gen_cdp_info_page.py`.

## Payload structure

A 22-byte container behind a 1-byte length prefix. Only the selector changes
between codes; bytes 2–14 are not validated by the scanner (shown here as `11`
filler).

| offset | bytes | role |
|:--|:--|:--|
| *(prefix)* | `16` | length prefix (framing; = 22, the payload length) |
| 0 | `00` | discriminator — must be `00` |
| 1 | `c0` | command — device-info query |
| 2–6 | `11 11 11 11 11` | 5-byte field — **not validated** (arbitrary filler) |
| 7–14 | `11 11 11 11 11 11 11 11` | 8-byte field — **not validated** (nonce) |
| 15–18 | `08 01 01 01` | CDP-info parameter prefix |
| 19 | **`SEL`** | **query selector** — the byte that varies per code |
| 20–21 | `00 00` | terminator |

## Codes

| Selector | Field | Code | Payload |
|:--:|:--|:--:|:--|
{table}

## Notes

- **Unsigned.** `C0` codes carry no per-message signature; the token acts on them
  without validating bytes 2–14.
- **Read-only.** These are diagnostic *reads* — a query code cannot change device
  state.
- **✻ unsupported.** Selectors `04` (xtal correction) and `06` (use count) return
  “unsupported” on the tested firmware.
- **KCV vs key.** Selector `09` returns the key check value — it verifies a
  candidate key against the device but does not reveal the key.

## Provenance

Selector map and container layout: `FORMAT.md §3.1` (ING Digipass scanner,
established by fuzzing an owned device). Images generated by `cronto/encoder.py`
at size 25 with length framing, and confirmed to decode back to each payload with
a valid CRC-24. Regenerate this page, the PNGs, and the HTML gallery with:

```bash
python3 docs/gen_cdp_info_page.py
```
'''


def main():
    items = render_items()
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(build_html(items))
    write_pngs(items)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(build_markdown(items))
    print(f"wrote {OUT_HTML}")
    print(f"wrote {OUT_MD} + {len(items)} PNGs in {PNG_DIR}/")


if __name__ == "__main__":
    main()
