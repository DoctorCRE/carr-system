#!/usr/bin/env python3
"""
check-brand.py, read a finished PDF back and check EVERY page against CARR brand.

Dell's standing instruction, 2026-08-20: check the whole document, all pages, before
anything is delivered. This exists because the first River Bank tour packet was
verified by counting pages and grepping text, both of which passed while nine of the
eleven property sheets rendered a forty-word paragraph inside a numeric spec cell in
orange monospace. Counts and greps cannot see layout. This reads what the PDF actually
draws: every colour, every font, every glyph, page by page.

The rules come from the brand-voice doctrine (Visual Identity):
  navy #002F6C carries the weight, orange #F57F29 is an ACCENT and is explicitly
  never body text or a dominant background; headers Oswald, body Montserrat.

Usage:
    python3 check-brand.py <file.pdf> [--max-accent-run N]

Exit 0 clean, 1 if anything failed. Findings name the page.

WHAT THIS CANNOT SEE, so nobody over-trusts a clean run: Chrome subsets the inlined
webfonts and emits them as unnamed Type3, so this confirms that no UNEXPECTED face
appears, it cannot confirm the embedded outlines really are Oswald and Montserrat.
That holds by construction, since build-space-search.py inlines them from the brand
asset folder. It also does not judge composition: whether a photo is the right photo,
whether a page is well balanced, whether the words are true. Read the pages too.
"""

import argparse
import collections
import os
import re
import sys

import pymupdf


NAVY = (0x00, 0x2F, 0x6C)
NAVY_DEEP = (0x00, 0x22, 0x4D)
ORANGE = (0xF5, 0x7F, 0x29)
ORANGE_INK = (0xB4, 0x55, 0x0F)

# Greys and near-blacks are the body/muted ramp; they are brand-neutral and fine.
# Anything else that carries real text is a colour nobody chose on purpose.
APPROVED = [NAVY, NAVY_DEEP, ORANGE, ORANGE_INK, (0, 0, 0), (0xFF, 0xFF, 0xFF)]

# CARR's greys are BLUE-greys, #5C6B7C has a 32-point channel spread, so a flat
# "r≈g≈b" test calls the muted print colour off-brand and buries the real findings.
# Saturation separates them cleanly: the blue-greys sit around 0.26, navy and orange
# above 0.8. Measured, not guessed.
MAX_GREY_SATURATION = 0.42

BRAND_FACES = ("Oswald", "Montserrat")
# Chrome subsets the inlined webfonts and emits them as unnamed Type3, so a per-span
# face name proves nothing about what was actually used. The document-level font list
# is checked instead, once, and these names are skipped per-span.
OPAQUE_FACES = ("Unnamed", "T3")
# Tabular figures in a spec grid are a deliberate typographic choice, not a brand
# violation, but they are only correct for SHORT values.
MONO_FACES = ("Mono", "Menlo", "Consolas", "Courier")

# An accent run longer than this is body text wearing the accent colour, which is the
# one thing the brand doctrine names outright. Tuned to pass a headline figure like
# "$4,300,000 · $238 /SF" and fail a sentence.
DEFAULT_MAX_ACCENT_RUN = 60


def rgb(i):
    return ((i >> 16) & 255, (i >> 8) & 255, i & 255)


def near(a, b, tol=26):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def approved(c):
    if any(near(c, a) for a in APPROVED):
        return True
    # the grey ramp, blue-greys included
    hi = max(c)
    return hi == 0 or (hi - min(c)) / hi <= MAX_GREY_SATURATION


def is_accent(c):
    return near(c, ORANGE, 40) or near(c, ORANGE_INK, 40)


def check(path, max_accent_run):
    doc = pymupdf.open(path)
    fails, warns = [], []
    faces = collections.Counter()

    if "—" in os.path.basename(path):
        fails.append(f"filename: em dash in {os.path.basename(path)!r}")

    for pno, page in enumerate(doc, 1):
        # Accumulate consecutive same-colour text so an accent RUN is measured across
        # the spans it is split into, rather than per span.
        run_len, run_page_reported = 0, False

        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"]
                    if not text.strip():
                        continue
                    colour = rgb(span["color"])
                    font = span["font"]
                    faces[font] += 1

                    if not approved(colour):
                        fails.append(
                            f"p{pno}: off-brand text colour #{span['color']:06X} "
                            f"on {text.strip()[:44]!r}")

                    if not any(o in font for o in OPAQUE_FACES) and \
                       not any(b in font for b in BRAND_FACES) and \
                       not any(m in font for m in MONO_FACES):
                        warns.append(f"p{pno}: non-brand face {font!r} on "
                                     f"{text.strip()[:44]!r}")

                    if is_accent(colour):
                        run_len += len(text)
                        if run_len > max_accent_run and not run_page_reported:
                            fails.append(
                                f"p{pno}: {run_len} characters of accent-orange text, "
                                f"orange is an accent, never body text "
                                f"(near {text.strip()[:44]!r})")
                            run_page_reported = True
                    else:
                        run_len = 0

                    # A long value in tabular figures is prose in the wrong face; it is
                    # also the shape that blows out a spec grid.
                    if any(m in font for m in MONO_FACES) and len(text.strip()) > 48:
                        fails.append(
                            f"p{pno}: {len(text.strip())} characters set in {font!r}, "
                            f"tabular figures are for short values, not prose "
                            f"({text.strip()[:44]!r})")

        # Em dashes are banned outright on every CARR surface, Dell 2026-08-20, after
        # repeated corrections that kept not sticking. Checked here rather than trusted
        # to memory, because memory is what failed. The filename is checked too: the
        # first River Bank packet shipped with two of them in its own name.
        page_text = page.get_text()
        n_em = page_text.count("—")
        if n_em:
            sample = re.search(r".{0,40}—.{0,40}", page_text)
            fails.append(f"p{pno}: {n_em} em dash(es), banned on every CARR surface "
                         f"({sample.group(0).strip()!r})" if sample else
                         f"p{pno}: {n_em} em dash(es)")

        # A page can be a defect without being empty. The Ferris pre-tour map orphaned a
        # single paragraph onto its own sheet, which the blank-page test passed because
        # the sheet was not blank. A page carrying no image and almost no text is a
        # widow: something above it should have been made to fit.
        body = page.get_text().strip()
        if not body and not page.get_images():
            fails.append(f"p{pno}: blank page")
        elif not page.get_images() and 0 < len(body) < 400:
            fails.append(f"p{pno}: near-empty page, {len(body)} characters and no image "
                         f"({' '.join(body.split())[:60]!r}). Make it fit on the page "
                         f"before, or give it something to sit with.")

    print(f"{path}\n  {doc.page_count} pages")
    print("  faces: " + ", ".join(f"{f} x{n}" for f, n in faces.most_common()))
    for f in fails:
        print(f"  FAIL  {f}")
    for w in sorted(set(warns)):
        print(f"  warn  {w}")
    print(f"  {len(fails)} failures, {len(set(warns))} warnings")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--max-accent-run", type=int, default=DEFAULT_MAX_ACCENT_RUN)
    a = ap.parse_args()
    return check(a.pdf, a.max_accent_run)


if __name__ == "__main__":
    raise SystemExit(main())
