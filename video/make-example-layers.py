#!/usr/bin/env python3
"""
make-example-layers.py — build the demo layer set for make-animated-static.sh.

This exists to prove the input contract, not to replace Canva. The real workflow
is: author the static in Canva, export each element as a full-canvas transparent
PNG, drop them in a folder numbered in build order. That is the whole reason this
is cheap for us and expensive for a vendor — we authored the source, so the
layers already exist and nothing has to segment a flat JPG.

Copy is the conflict-of-interest hook (the standing strongest pitch) and it
passes run.sh lint clean.
"""
import os, sys
# Pillow moved the resampling constants under Image.Resampling in 9.1 and
# dropped them from the Image namespace in the type stubs. The bare
# Image.LANCZOS alias still RESOLVES at runtime on Pillow 12.3 (checked, not
# assumed), so this was never a crash — but mypy cannot see it, and the
# Resampling form is the documented one. Fixing the call beats silencing it.
from PIL import Image, ImageDraw, ImageFont

W = H = 1080
NAVY = (0, 47, 108, 255)
ORANGE = (245, 127, 41, 255)
WHITE = (255, 255, 255, 255)
DIM = (198, 210, 228, 255)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BRAND = os.path.join(REPO, "claude-tree", "skills", "write-content", "graphics")
FONTS = os.path.join(BRAND, "fonts")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
    "~/Movies/CARR Video Pipeline/Scripts/example-layers")


def font(name, size):
    family = "Oswald.ttf" if name.startswith("Oswald") else "Montserrat.ttf"
    for p in (os.path.join(FONTS, family),):
        if os.path.isfile(p):
            return ImageFont.truetype(p, size)
    raise SystemExit(f"font not found: {name}")


def plate():
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def centered(img, text, fnt, fill, y, tracking=0):
    """Draw text centered on the canvas, with optional letter tracking."""
    d = ImageDraw.Draw(img)
    if not tracking:
        w = d.textbbox((0, 0), text, font=fnt)[2]
        d.text(((W - w) / 2, y), text, font=fnt, fill=fill)
        return
    widths = [d.textbbox((0, 0), c, font=fnt)[2] for c in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x = (W - total) / 2
    for c, cw in zip(text, widths):
        d.text((x, y), c, font=fnt, fill=fill)
        x += cw + tracking


os.makedirs(OUT, exist_ok=True)
for f in os.listdir(OUT):
    if f.endswith(".png"):
        os.remove(os.path.join(OUT, f))

# 01 — background plate. Never slides or tilts; it IS the canvas.
bg = Image.new("RGBA", (W, H), NAVY)
# Soft radial vignette: darkest at the corners, clean at the center. Built from a
# real radial gradient rather than nested rectangles, which leave a visible edge.
vig = Image.radial_gradient("L").resize((W, H), Image.Resampling.LANCZOS).point(lambda v: int(v * 0.42))
bg = Image.composite(Image.new("RGBA", (W, H), (0, 18, 44, 255)), bg, vig)
bg.save(f"{OUT}/01_background_scale.png")

l = plate()
centered(l, "BEFORE YOU SIGN", font("Montserrat-SemiBold.ttf", 30), ORANGE, 214, tracking=9)
l.save(f"{OUT}/02_kicker_above.png")

# 03 / 04 — headline, one layer per line so they land one after the other
l = plate()
centered(l, "NOBODY IN THAT ROOM", font("Oswald-Bold.ttf", 92), WHITE, 306, tracking=2)
l.save(f"{OUT}/03_head1_left.png")

l = plate()
centered(l, "WORKS FOR YOU.", font("Oswald-Bold.ttf", 92), WHITE, 414, tracking=2)
l.save(f"{OUT}/04_head2_right.png")

l = plate()
ImageDraw.Draw(l).rectangle([W / 2 - 108, 556, W / 2 + 108, 562], fill=ORANGE)
l.save(f"{OUT}/05_rule_scale.png")

# 06 — body, wrapped by hand so the line breaks are deliberate
l = plate()
body = font("Montserrat-Medium.ttf", 33)
for i, line in enumerate(["The listing broker represents the landlord",
                          "and collects both sides of the fee when a",
                          "tenant signs without their own broker."]):
    centered(l, line, body, DIM, 626 + i * 50)
l.save(f"{OUT}/06_body.png")

l = plate()
for i, line in enumerate(["CARR represents healthcare",
                          "tenants and buyers only."]):
    centered(l, line, font("Montserrat-Bold.ttf", 37), WHITE, 806 + i * 50)
l.save(f"{OUT}/07_claim.png")

logo = Image.open(os.path.join(BRAND, "assets", "carr-white.png")).convert("RGBA")
scale = (W * 0.20) / logo.width
logo = logo.resize((int(logo.width * scale), int(logo.height * scale)), Image.Resampling.LANCZOS)
l = plate()
l.paste(logo, (int((W - logo.width) / 2), 946), logo)
l.save(f"{OUT}/08_logo_scale.png")

l = plate()
centered(l, "JOE BOOKOUT  ·  HEALTHCARE REAL ESTATE",
         font("Montserrat-SemiBold.ttf", 23), DIM, 1024, tracking=4)
l.save(f"{OUT}/09_byline.png")

names = sorted(f for f in os.listdir(OUT) if f.endswith(".png"))
print(f"{len(names)} layers -> {OUT}")
for n in names:
    print("  " + n)

# flattened reference: this is what the finished static looks like, and it must
# match the video's final frame.
flat = Image.new("RGBA", (W, H), (0, 0, 0, 0))
for n in names:
    flat = Image.alpha_composite(flat, Image.open(os.path.join(OUT, n)).convert("RGBA"))
flat.convert("RGB").save(f"{OUT}/../example-static-flat.png")
print("reference composite -> example-static-flat.png")
