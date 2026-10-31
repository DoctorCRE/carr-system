"""Build synthetic scanned and mixed PDF fixtures without reading client documents."""

from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import pymupdf


def build(folder):
    folder.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1600, 1000), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 36)
    for index, line in enumerate(
        [
            "Rent schedule",
            "Year 1 | $24 | USD/SF/year | NNN | none",
            "Year 2 | $24.72 | USD/SF/year | NNN | 3%",
            "Term is 60 months.",
        ]
    ):
        draw.text((60, 70 + index * 90), line, font=font, fill="black")
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    raster = buffer.getvalue()
    for mode in ("scan-only", "mixed", "ocr-table"):
        content = b"q 800 0 0 500 0 80 cm /Scan Do Q\n"
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 800 700] /Resources << /XObject << /Scan 4 0 R >> /Font << /F1 6 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /XObject /Subtype /Image /Width 1600 /Height 1000 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length "
            + str(len(raster)).encode()
            + b" >>\nstream\n"
            + raster
            + b"\nendstream",
            b"<< /Length "
            + str(len(content)).encode()
            + b" >>\nstream\n"
            + content
            + b"endstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        pdf = b"%PDF-1.4\n"
        offsets = [0]
        for index, obj in enumerate(objects, 1):
            offsets.append(len(pdf))
            pdf += str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
        xref = len(pdf)
        pdf += b"xref\n0 7\n0000000000 65535 f \n"
        pdf += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
        pdf += (
            f"trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        )
        if mode == "mixed":
            with pymupdf.open(stream=pdf, filetype="pdf") as document:
                page = document.new_page(pno=0, width=800, height=700)
                page.insert_text((40, 50), "Base rent is $30 per SF per year NNN.")
                pdf = document.tobytes()
        (folder / f"{mode}.pdf").write_bytes(pdf)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    build(parser.parse_args().folder)
