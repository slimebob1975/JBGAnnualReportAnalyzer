"""Var tar maskeringens tid vägen?

Att flytta NER-modellen till grafikkortet gav 27,0 till 22,4 sekunder per
dokument. Nyttigt, men inte den storleksordning ett kort brukar ge, och
slutsatsen är att modellen aldrig var huvudkostnaden: ungefär fem sekunder av
tjugosju. Resten är pdf-arbete på en kärna - sanering, att leta upp termerna,
att lägga på svärtningen och att svepa utdata efteråt.

Innan något optimeras bör man veta vilket av de stegen som kostar. Skriptet
kör en riktig maskering under cProfile och visar de funktioner som tar mest
tid, samt en egen klocka på de fem stegen.

    python scripts/profile_masking.py "C:\\...\\arsredovisning.pdf"

Inga API-anrop. Räkna med en halv minut plus modellens starttid.
"""

import argparse
import cProfile
import functools
import io
import pstats
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from app.src.masking.JBGPDFMasking import PDFMasker
except ImportError as ex:
    sys.exit(f"Kunde inte läsa in maskeringen: {ex}")


class Timed:
    """Klockar de enskilda stegen genom att lägga sig runt metoderna.

    cProfile visar vilka funktioner som kostar, men inte hur de fördelar sig
    på maskeringens egna steg. Det här gör det andra.
    """

    STEPS = (
        "sanitize_pdf",
        "detect_sensitive_terms",
        "mask_pdf_black_boxes",
        "find_surviving_terms",
        "find_role_names_in_pdf",
        "find_identity_numbers_in_pdf",
        "_locate_term",
        "_locate_term_in_spans",
    )

    def __init__(self, masker):
        self.masker = masker
        self.totals = dict.fromkeys(self.STEPS, 0.0)
        self.counts = dict.fromkeys(self.STEPS, 0)
        self._originals = {}

    def __enter__(self):
        """Lägg klockan runt metoderna utan att ändra hur de binds.

        Första versionen ersatte allt med vanliga funktioner, vilket gjorde
        att klassmetodernas `cls` hamnade bland de vanliga argumenten:
        "_locate_term() got multiple values for argument 'entries'". Mätningen
        sänkte alltså det den skulle mäta. Descriptorn måste behållas.
        """
        klass = type(self.masker)
        for step in self.STEPS:
            raw = klass.__dict__.get(step)
            if raw is None:
                continue
            self._originals[step] = raw
            function = raw.__func__ if isinstance(raw, (classmethod, staticmethod)) else raw

            def timed(*args, _step=step, _function=function, **kwargs):
                started = time.monotonic()
                try:
                    return _function(*args, **kwargs)
                finally:
                    self.totals[_step] += time.monotonic() - started
                    self.counts[_step] += 1

            timed = functools.wraps(function)(timed)
            if isinstance(raw, classmethod):
                timed = classmethod(timed)
            elif isinstance(raw, staticmethod):
                timed = staticmethod(timed)
            setattr(klass, step, timed)
        return self

    def __exit__(self, *exc):
        for step, original in self._originals.items():
            setattr(type(self.masker), step, original)
        return False

    def report(self, wall: float) -> None:
        print(f"\n{'steg':<32} {'sekunder':>9} {'andel':>7} {'anrop':>7}")
        for step in self.STEPS:
            if not self.counts[step]:
                continue
            share = 100 * self.totals[step] / wall if wall else 0
            print(f"  {step:<30} {self.totals[step]:>9.1f} {share:>6.0f}% "
                  f"{self.counts[step]:>7}")
        print(f"  {'(hela maskeringen)':<30} {wall:>9.1f} {100:>6.0f}%")
        print("\nStegen överlappar: mask_pdf_black_boxes innehåller både "
              "_locate_term\noch verifieringen, så andelarna summerar till mer "
              "än hundra.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf")
    parser.add_argument("--top", type=int, default=18,
                        help="antal funktioner att visa (förval 18)")
    args = parser.parse_args()

    pdf = Path(args.pdf)
    if not pdf.is_file():
        sys.exit(f"Hittar inte filen: {pdf}")

    workdir = Path(tempfile.mkdtemp(prefix="jbg-profil-"))
    try:
        target = workdir / pdf.name
        shutil.copy(pdf, target)

        print("Startar modellen ...")
        masker = PDFMasker()
        output = target.with_name(target.stem + "_masked.pdf")

        profiler = cProfile.Profile()
        with Timed(masker) as timed:
            started = time.monotonic()
            profiler.enable()
            resultat = masker.do_masking(target, output)
            profiler.disable()
            wall = time.monotonic() - started
        if resultat is None:
            sys.exit("Maskeringen misslyckades - siffrorna nedan vore "
                     "meningslösa. Se felet ovan.")

        timed.report(wall)

        stream = io.StringIO()
        stats = pstats.Stats(profiler, stream=stream).sort_stats("cumulative")
        stats.print_stats(args.top)
        print(f"\nDe {args.top} dyraste funktionerna, sammanlagd tid:\n")
        for line in stream.getvalue().split("\n"):
            if "pymupdf" in line or "torch" in line or "JBG" in line or \
               "transformers" in line or line.strip().startswith("ncalls"):
                print("  " + line.strip()[:150])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
