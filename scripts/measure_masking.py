"""Vinner maskeringen på fler arbetare, och i så fall varför?

Maskeringen är golvet i en parallell körning: 15,7 av 22,7 minuter. Frågan är
om den går att korta genom att köra flera NER-modeller samtidigt, och den har
två tänkbara svar som ser likadana ut utifrån.

Är arbetet minnesbundet vinner man på fler modeller så länge minnet räcker.
Är det processorbundet vinner man ingenting: PyTorch sprider redan en modells
arbete över alla kärnor, och fyra modeller slåss då om samma kärnor och blir
var och en fyra gånger långsammare. Mer minne köper inga kärnor.

Skriptet mäter i stället för att gissa. Samma dokument maskeras med ett, två,
fyra och åtta parallella arbetare, och för varje omgång rapporteras väggtid,
tid per dokument och högsta minnesanvändning.

    python scripts/measure_masking.py "C:\\...\\arsredovisning.pdf"

Inga API-anrop, ingen kostnad. Räkna med en kvart, och att maskinen går varm.

Kräver maskeringsberoendena (torch och transformers) och psutil för minnet.
Saknas psutil mäts tiden ändå.
"""

import argparse
import shutil
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from app.src.masking.JBGPDFMasking import PDFMasker
except ImportError as ex:
    sys.exit(f"Kunde inte läsa in maskeringen: {ex}")

try:
    import psutil
except ImportError:
    psutil = None


def peak_memory_mb() -> float:
    if not psutil:
        return 0.0
    return psutil.Process().memory_info().rss / (1024 * 1024)


def available_memory_mb() -> float:
    if not psutil:
        return 0.0
    return psutil.virtual_memory().available / (1024 * 1024)


def torch_threads() -> int:
    try:
        import torch

        return torch.get_num_threads()
    except Exception:
        return 0


def set_torch_threads(count: int) -> None:
    try:
        import torch

        torch.set_num_threads(max(1, count))
    except Exception:
        pass


def run_round(pdf: Path, workers: int, repeats: int, share_model: bool,
              threads_each: int | None) -> dict:
    """Maskera samma dokument `repeats` gånger med `workers` arbetare."""
    workdir = Path(tempfile.mkdtemp(prefix="jbg-mask-"))
    try:
        copies = []
        for index in range(repeats):
            target = workdir / f"{index:02d}_{pdf.name}"
            shutil.copy(pdf, target)
            copies.append(target)

        if threads_each:
            set_torch_threads(threads_each)

        # En delad modell speglar hur tjänsten gör i dag. En per arbetare är
        # det alternativ som skulle kosta minne.
        shared = PDFMasker() if share_model else None
        local = threading.local()
        peak = [peak_memory_mb()]
        durations = []
        lock = threading.Lock()

        def mask_one(source: Path):
            if shared is not None:
                masker = shared
            else:
                if not hasattr(local, "masker"):
                    local.masker = PDFMasker()
                masker = local.masker
            started = time.monotonic()
            masker.do_masking(source, source.with_name(source.stem + "_masked.pdf"))
            elapsed = time.monotonic() - started
            with lock:
                durations.append(elapsed)
                peak[0] = max(peak[0], peak_memory_mb())

        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(mask_one, copies))
        wall = time.monotonic() - started

        return {
            "arbetare": workers,
            "dokument": len(durations),
            "vaggtid": wall,
            "per_dokument": statistics.median(durations) if durations else 0,
            "minne": peak[0],
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="en årsredovisning att maskera om och om igen")
    parser.add_argument("--workers", default="1,2,4",
                        help="antal parallella arbetare att pröva")
    parser.add_argument("--repeats", type=int, default=0,
                        help="dokument per omgång. Förval: dubbelt så många "
                             "som arbetarna.")
    parser.add_argument("--own-model", action="store_true",
                        help="en NER-modell per arbetare i stället för en "
                             "delad. Det är det alternativ som kostar minne.")
    parser.add_argument("--split-threads", action="store_true",
                        help="dela PyTorchs trådar mellan arbetarna. Utan "
                             "detta använder varje modell alla kärnor och de "
                             "slåss om samma.")
    args = parser.parse_args()

    pdf = Path(args.pdf)
    if not pdf.is_file():
        sys.exit(f"Hittar inte filen: {pdf}")

    cores = torch_threads()
    print(f"Dokument: {pdf.name}")
    print(f"PyTorch-trådar: {cores or 'okänt'}, "
          f"ledigt minne: {available_memory_mb():,.0f} MB".replace(",", " "))
    print(f"Modell: {'en per arbetare' if args.own_model else 'en delad'}, "
          f"trådar: {'delade mellan arbetarna' if args.split_threads else 'alla till var och en'}")
    if not psutil:
        print("psutil saknas - minnet mäts inte.")
    print()
    print(f"{'arbetare':>9} {'dok':>5} {'väggtid s':>10} {'per dok s':>10} "
          f"{'minne MB':>10} {'ggr':>6}")

    results = []
    for workers in [int(w) for w in args.workers.split(",")]:
        repeats = args.repeats or workers * 2
        threads = (cores // workers) if (args.split_threads and cores) else None
        result = run_round(pdf, workers, repeats, not args.own_model, threads)
        results.append(result)
        base = results[0]
        speedup = ((base["vaggtid"] / base["dokument"]) /
                   (result["vaggtid"] / result["dokument"])) if result["dokument"] else 0
        result["ggr"] = speedup
        print(f"{result['arbetare']:>9} {result['dokument']:>5} "
              f"{result['vaggtid']:>10.1f} {result['per_dokument']:>10.1f} "
              f"{result['minne']:>10.0f} {speedup:>6.2f}")

    print()
    if len(results) < 2:
        # En enda omgång säger ingenting om parallellism. Slutsatsen nedan
        # skrevs en gång ut efter en körning med bara --workers 1, och lät
        # mycket säkrare än underlaget tillät.
        print(f"Bara en omgång kördes: {results[0]['per_dokument']:.1f} "
              "sekunder per dokument.\nKör flera värden i --workers för att "
              "jämföra.")
        return
    best = max(results, key=lambda r: r["ggr"])
    if best["ggr"] < 1.3:
        print("Fler arbetare ger ingenting. Arbetet är processorbundet: "
              "PyTorch sprider\nredan en modells arbete över alla kärnor, och "
              "fler modeller slåss om samma.\nMer minne hjälper inte.")
    else:
        print(f"Bäst: {best['arbetare']} arbetare gav {best['ggr']:.1f} gånger "
              f"genomströmningen,\ntill {best['minne']:,.0f} MB."
              .replace(",", " "))
        if not args.own_model:
            print("Pröva --own-model: en modell per arbetare kostar minne men "
                  "kan ge mer.")
    print("\nStiger 'per dok' ungefär lika mycket som antalet arbetare är "
          "vinsten en synvilla:\narbetet görs inte fortare, bara mer samtidigt "
          "om samma kärnor.")


if __name__ == "__main__":
    main()
