"""Vilken term står kvar som läsbar text i en maskerad fil?

Rollsvepet letar efter namn intill en roll. Den andra kontrollen går igenom
hela termlistan, och det är den GS faller på:

    1 av 36 känsliga termer finns kvar som läsbar text

Loggen säger hur många, inte vilken. Skriptet kör upptäckten på originalet,
frågar sedan den maskerade filen vilka av termerna som fortfarande går att
läsa, och visar formen hos dem - inte innehållet.

Kräver NER-modellen, så det tar en stund att starta.

    python scripts/diagnose_surviving_terms.py "...(0).pdf" "...(0)_ocr_masked.pdf"
"""

import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf  # noqa: E402

from app.src.masking.JBGPDFMasking import PDFMasker  # noqa: E402


def skeleton(text: str) -> str:
    """Strängens form utan dess innehåll."""
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


def main(original_path, masked_path):
    original = Path(original_path)
    masked = Path(masked_path)
    for path in (original, masked):
        if not path.is_file():
            sys.exit(f"Hittar inte filen: {path}")

    print("Startar NER-modellen ...")
    masker = PDFMasker()

    page_texts = masker.extract_text(original)
    terms = masker.detect_sensitive_terms(page_texts)
    kept, too_short, too_common = PDFMasker._plausible_terms(original, terms)
    print(f"{len(terms)} termer upptäckta, {len(kept)} behållna, "
          f"{len(too_short)} för korta, {len(too_common)} för vanliga.")

    kvar = PDFMasker.find_surviving_terms(masked, kept)
    print(f"\n{len(kvar)} av {len(kept)} termer går fortfarande att läsa i "
          f"{masked.name}.")

    if not kvar:
        print("Inget att undersöka: inget står kvar.")
        return

    for term in sorted(kvar):
        print("\n" + "=" * 70)
        print(f"Form:  {skeleton(term)}")
        print(f"Längd: {len(term)} tecken")

        with pymupdf.open(masked) as doc:
            for number, page in enumerate(doc, 1):
                if term not in page.get_text():
                    continue
                via_words = PDFMasker._locate_term(page, term)
                via_spans = PDFMasker._locate_term_in_spans(page, term)
                print(f"Sidan {number}: ordlistan ger {len(via_words)} "
                      f"rektangel/-ar, spanreserven {len(via_spans)}.")
                for block in page.get_text("dict").get("blocks", []):
                    for line in block.get("lines", []):
                        text = "".join(s.get("text", "") for s in line["spans"])
                        if term not in text:
                            continue
                        print(f"  raden har {len(line['spans'])} span:")
                        for span in line["spans"]:
                            print(f"    {skeleton(span.get('text', '')):<46}"
                                  f" {span.get('font', '?')}")
                        break
                break

    print("\n" + "=" * 70)
    print("Ger båda lokaliserarna 0 rektanglar går texten inte att placera,")
    print("och det är skanningen som är problemet, inte koden.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])