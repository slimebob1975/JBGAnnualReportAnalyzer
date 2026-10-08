"""Varför säger den ena kontrollen "verifierad" om ett namn den andra ser?

Loggen för 172140 påstår två saker som inte kan vara sanna samtidigt:

    Maskering verifierad: inga av 82 termer återfinns i utdata.
    1 namn står kvar bredvid sin roll ... trots att de finns i termlistan.

Rollsvepet läser rad för rad ur get_text(). Verifieraren söker i en
normaliserad, sammanslagen hötapp. Går de isär om samma sträng finns felet i
normaliseringen, och då kan verifieraren säga "verifierad" om ett namn som
står kvar - i det här dokumentet och i andra, utan att någon märker det.

Skriptet skriver ut strukturen hos skillnaden, inte namnet. Tecken som är
vanliga bokstäver visas som "a" respektive "A", siffror som "9". Allt annat
skrivs ut med kodpunkt, eftersom det är där felet rimligen sitter.

Kör från projektets rot:

    python scripts/diagnose_masking_mismatch.py "C:\\...\\...(172140) (0)_masked.pdf"
"""

import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf  # noqa: E402

from app.src.masking.JBGPDFMasking import (  # noqa: E402
    PDFMasker,
)


def skeleton(text: str) -> str:
    """Strängens form utan dess innehåll.

    Gemener blir "a", versaler "A", siffror "9". Allt annat skrivs ut med
    kodpunkt och unicode-namn: det är de tecknen frågan gäller.
    """
    parts = []
    for char in text:
        if char.islower() and char.isalpha() and ord(char) < 128:
            parts.append("a")
        elif char.isupper() and char.isalpha() and ord(char) < 128:
            parts.append("A")
        elif char.isdigit():
            parts.append("9")
        elif char == " ":
            parts.append("_")
        else:
            try:
                name = unicodedata.name(char)
            except ValueError:
                name = "OKÄNT"
            parts.append(f"[U+{ord(char):04X} {name}]")
    return "".join(parts)


def main(pdf_path):
    pdf = Path(pdf_path)
    if not pdf.is_file():
        sys.exit(f"Hittar inte filen: {pdf}")

    kvar = sorted(PDFMasker.find_role_names_in_pdf(pdf))
    print(f"Rollsvepet hittar {len(kvar)} namn kvar i {pdf.name}.")
    if not kvar:
        print("Ingenting att undersöka: kontrollerna är överens om den här filen.")
        return

    for namn in kvar:
        print("\n" + "=" * 70)
        print(f"Form:    {skeleton(namn)}")
        print(f"Längd:   {len(namn)} tecken")

        # Fråga verifieraren om exakt samma sträng.
        ser_verifieraren = PDFMasker.find_surviving_terms(pdf, [namn])
        print(f"Verifieraren hittar strängen: "
              f"{'JA' if ser_verifieraren else 'NEJ  <-- motsägelsen'}")

        # Hur ser de två vägarna på samma text?
        normaliserad = PDFMasker._normalise_haystack(namn)
        print(f"Normaliserad form: {skeleton(normaliserad)}")
        if normaliserad != namn:
            print("  (normaliseringen ändrar strängen)")

        # Och hur är raden satt?
        with pymupdf.open(pdf) as doc:
            for sidnr, sida in enumerate(doc, 1):
                if namn not in sida.get_text():
                    continue
                print(f"\nSidan {sidnr}, raden sådan den är satt:")
                for block in sida.get_text("dict").get("blocks", []):
                    for rad in block.get("lines", []):
                        text = "".join(sp.get("text", "") for sp in rad["spans"])
                        if namn not in text:
                            continue
                        spans = rad["spans"]
                        print(f"  {len(spans)} span på raden")
                        for sp in spans:
                            typsnitt = sp.get("font", "?")
                            print(f"    {skeleton(sp.get('text', '')):<40} {typsnitt}")
                        break
                break

        # Går strängen att placera på sidan över huvud taget?
        with pymupdf.open(pdf) as doc:
            for _sidnr, sida in enumerate(doc, 1):
                if namn not in sida.get_text():
                    continue
                via_ord = PDFMasker._locate_term(sida, namn)
                via_span = PDFMasker._locate_term_in_spans(sida, namn)
                print(f"\n  Ordlistan ger {len(via_ord)} rektangel/-ar, "
                      f"spanreserven {len(via_span)}.")
                break

    print("\n" + "=" * 70)
    print("Säger verifieraren NEJ om en sträng som rollsvepet ser, ligger felet")
    print("i normaliseringen och gäller sannolikt fler dokument än det här.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
