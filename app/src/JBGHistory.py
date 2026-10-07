"""Resultatfiler som sparas för att kunna jämföras mellan år.

Ett värde som rört sig tusen gånger, bytt tecken eller hoppat en tiopotens
sedan i fjol är nästan säkert fel, och varken modellen eller någon aritmetisk
identitet inom en enskild årsredovisning märker det. Föregående år är den enda
externa referens som finns kvar när den manuella inmatningen upphör.

Men resultatet låg hittills bara i jobbkatalogen, som städas efter en timmes
overksamhet. En körning av 2024 års material hade alltså varit borta innan
någon hunnit jämföra mot den.

Historiken är resultatfiler, inte en egen databas. Skälet är att en myndighet
behöver kunna svara på vad en jämförelse gjordes mot: "beloppet har rört sig
tusen gånger sedan i fjol" går inte att kontrollera om ingen vet vilken
fjolårsfil som avsågs. Filerna är dessutom samma format som tjänsten redan
skriver, så en historikfil kan laddas upp igen efter en ominstallation.
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Lägg den här på en säkerhetskopierad plats. Förvalet ligger i
# installationen och överlever därför inte en ominstallation, vilket är
# precis varför filerna också går att ladda upp igen.
DEFAULT_HISTORY_DIR = Path(__file__).resolve().parents[1] / "config" / "historik"
FILENAME_PATTERN = re.compile(
    r"^(?P<year>\d{4})_(?P<stamp>\d{8}T\d{6})_(?P<funds>\d+)kassor\.json$"
)


def history_dir() -> Path:
    return Path(os.getenv("JBG_HISTORY_DIR") or DEFAULT_HISTORY_DIR)


def _years_and_funds(result: dict) -> tuple[list[str], int]:
    years, funds = set(), 0
    for fund, entries in (result or {}).items():
        if fund.startswith("_") or not isinstance(entries, dict):
            continue
        funds += 1
        years.update(str(year) for year in entries)
    return sorted(years), funds


def save(result: dict, directory=None) -> Path | None:
    """Spara resultatet som historik. Returnerar filen, eller None.

    Misslyckas aldrig uppåt: historiken är en bekvämlighet, och ska inte kunna
    fälla en körning som i övrigt gick bra.
    """
    years, funds = _years_and_funds(result)
    if not years or not funds:
        return None

    target = Path(directory) if directory else history_dir()
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    # Flera år i samma körning namnges efter det senaste; filen innehåller
    # ändå alla, och listningen nedan läser årtalen ur innehållet.
    name = f"{years[-1]}_{stamp}_{funds}kassor.json"
    try:
        target.mkdir(parents=True, exist_ok=True)
        path = target / name
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as ex:
        logger.warning(f"Kunde inte spara historik i {target}: {ex}")
        return None

    logger.info(
        f"Historik sparad: {path.name} ({funds} kassor, "
        f"{'år ' + ', '.join(years)})."
    )
    return path


def available(directory=None) -> list[dict]:
    """Sparade körningar, nyast först, med år och antal kassor.

    Bara det som behövs för att låta användaren välja: filnamn, år, antal
    kassor och när den sparades. Innehållet läses först när någon valt den.
    """
    target = Path(directory) if directory else history_dir()
    if not target.is_dir():
        return []

    entries = []
    for path in target.glob("*.json"):
        match = FILENAME_PATTERN.match(path.name)
        if not match:
            continue
        try:
            stamp = datetime.strptime(match["stamp"], "%Y%m%dT%H%M%S")
        except ValueError:
            continue
        entries.append({
            "fil": path.name,
            "sokvag": str(path),
            "ar": match["year"],
            "kassor": int(match["funds"]),
            "sparad": stamp.isoformat(sep=" ", timespec="minutes"),
        })
    return sorted(entries, key=lambda item: item["sparad"], reverse=True)


def load(path) -> dict:
    """Läs en historikfil. Tom uppslagsbok om den inte går att läsa."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        logger.warning(f"Kunde inte läsa historikfilen {path}: {ex}")
        return {}
