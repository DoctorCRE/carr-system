#!/usr/bin/env python3
"""Prove a PDF opens and draws before it is handed to a partner.

Dell, 2026-08-27: "please check every document to be sure it works before you
provide it to me moving forward." A page count read out of a parser is not that
check. This one opens the file the way a reader does, rasterises every page, and
fails loudly on anything a person would see as broken.

The bug this exists to stop repeating: an earlier check called a pypdfium2
attribute that does not exist, caught the AttributeError from its OWN code, and
reported all 13 pages as failures. A checker that cannot tell its own crash from
the document's is worse than no checker, so every probe here is narrow and the
failure message says which probe failed.
"""
import os, re, sys, subprocess

def check(path):
    p, fail, warn = os.path.abspath(path), [], []
    name = os.path.basename(p)

    if not os.path.exists(p):
        return name, ["file does not exist"], []
    size = os.path.getsize(p)
    if size == 0:
        return name, ["file is zero bytes"], []

    raw = open(p, "rb").read()
    if not raw.startswith(b"%PDF-"):
        fail.append(f"no %PDF- header, starts {raw[:8]!r}")
    if b"%%EOF" not in raw[-2048:]:
        fail.append("no %%EOF near the end, file is likely truncated")
    sx = re.findall(rb"startxref\s+(\d+)", raw)
    if not sx:
        fail.append("no startxref")
    elif int(sx[-1]) >= size:
        fail.append(f"startxref {int(sx[-1])} points past the end of a {size} byte file")
    if b"/Encrypt" in raw:
        warn.append("carries /Encrypt, a reader may prompt for a password")

    # macOS refuses or warns on quarantined files depending on Gatekeeper settings.
    q = subprocess.run(["xattr", p], capture_output=True, text=True).stdout
    if "com.apple.quarantine" in q:
        warn.append("com.apple.quarantine is set")

    try:
        import pypdfium2 as pdfium
    except ImportError:
        fail.append("pypdfium2 unavailable, cannot prove it renders")
        return name, fail, warn

    try:
        doc = pdfium.PdfDocument(p)
        n = len(doc)
    except Exception as e:
        fail.append(f"will not open: {e!r}")
        return name, fail, warn
    if n == 0:
        fail.append("opens but has zero pages")
        return name, fail, warn

    blank, notext = [], []
    for i in range(n):
        page = doc[i]
        try:
            im = page.render(scale=0.4).to_pil()          # the only probe that matters
        except Exception as e:
            fail.append(f"page {i+1} will not draw: {e!r}")
            continue
        g = im.convert("L")
        if min(g.getdata()) > 250:
            blank.append(i + 1)
        try:
            if not (page.get_textpage().get_text_range() or "").strip():
                notext.append(i + 1)
        except Exception:
            pass
    if blank:
        fail.append(f"pages render completely blank: {blank}")
    if notext:
        warn.append(f"pages with no extractable text: {notext}")
    return name, fail, warn, n, size

if __name__ == "__main__":
    bad = 0
    for arg in sys.argv[1:]:
        r = check(arg)
        name, fail, warn = r[0], r[1], r[2]
        n, size = (r[3], r[4]) if len(r) > 3 else ("?", "?")
        status = "FAIL" if fail else ("ok  " if not warn else "ok* ")
        print(f"{status} {name}  ({n} pages, {size} bytes)")
        for f in fail:
            print(f"       FAIL: {f}"); bad += 1
        for w in warn:
            print(f"       note: {w}")
    sys.exit(1 if bad else 0)
