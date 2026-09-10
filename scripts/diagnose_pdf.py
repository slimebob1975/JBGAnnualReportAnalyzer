"""Diagnose why a scanned PDF yields almost no text after OCR.

Read-only. Prints one line per page plus a summary, then suggests which
ocrmypdf flags are worth trying.

Usage:
    python diagnose_pdf.py "C:\\path\\to\\Arsredovisning 2025(172422) (0).pdf"
"""

import sys
from pathlib import Path

import pymupdf


def effective_dpi(page, img_width_px, img_height_px):
    """Pixels per inch, given how large the image is drawn on the page."""
    w_in = page.rect.width / 72.0
    h_in = page.rect.height / 72.0
    if w_in <= 0 or h_in <= 0:
        return 0, 0
    return round(img_width_px / w_in), round(img_height_px / h_in)


def main(path):
    pdf = Path(path)
    if not pdf.is_file():
        sys.exit(f"Hittar inte filen: {pdf}")

    doc = pymupdf.open(pdf)
    print(f"{pdf.name}: {doc.page_count} sidor, krypterad={doc.is_encrypted}")
    print(f"metadata producer={doc.metadata.get('producer')!r} "
          f"creator={doc.metadata.get('creator')!r}")
    print()
    print(f"{'sida':>4} {'rot':>4} {'tecken':>7} {'bilder':>6} "
          f"{'största bild (px)':>19} {'dpi':>9} {'colorspace/filter'}")
    print("-" * 88)

    total_chars = 0
    dpis = []
    for i, page in enumerate(doc, 1):
        text = page.get_text().strip()
        total_chars += len(text)
        images = page.get_images(full=True)
        biggest = ""
        dpi_str = ""
        cs = ""
        if images:
            # xref, smask, width, height, bpc, colorspace, ...
            largest = max(images, key=lambda im: im[2] * im[3])
            w, h = largest[2], largest[3]
            biggest = f"{w}x{h}"
            dx, dy = effective_dpi(page, w, h)
            dpi_str = f"{dx}x{dy}"
            dpis.append(min(dx, dy))
            try:
                info = doc.extract_image(largest[0])
                cs = f"{info.get('colorspace')}ch/{info.get('ext')} bpc={largest[4]}"
            except Exception as ex:
                cs = f"(kunde inte läsas: {ex})"
        print(f"{i:>4} {page.rotation:>4} {len(text):>7} {len(images):>6} "
              f"{biggest:>19} {dpi_str:>9} {cs}")

    print("-" * 88)
    print(f"Totalt {total_chars} tecken i befintligt textlager.")
    if dpis:
        print(f"Effektiv upplösning: min {min(dpis)} dpi, median "
              f"{sorted(dpis)[len(dpis) // 2]} dpi, max {max(dpis)} dpi.")
    rotations = {p.rotation for p in doc}
    print(f"Sidrotationer i filen: {sorted(rotations)}")

    print()
    print("Tolkning:")
    if dpis and min(dpis) < 200:
        print("  - Under ~200 dpi läser tesseract brödtext dåligt och siffror sämre.")
        print("    Pröva: ocrmypdf --force-ocr --image-dpi 300 ...")
    if rotations - {0}:
        print("  - Sidor är roterade. Pröva: ocrmypdf --rotate-pages ...")
    if total_chars > 0:
        print("  - Det finns redan text i filen. Med skip_text=True hoppar")
        print("    ocrmypdf över hela sidor som har någon text alls, även en stämpel.")
        print("    Pröva: ocrmypdf --force-ocr ...")
    if not dpis:
        print("  - Inga inbäddade bilder hittades. Sidorna är då vektorgrafik")
        print("    eller tomma, och OCR har ingenting att arbeta med.")
    doc.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
