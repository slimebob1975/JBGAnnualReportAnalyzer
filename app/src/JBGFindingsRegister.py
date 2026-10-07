"""Register över anmärkningar som återkommer mellan körningar.

Skillnaden mellan brus och mönster är den som avgör vad som är värt att
åtgärda, och den syns inte i en enskild körning. Under hösten 2026 fick vi
den bara genom att en människa råkade känna igen samma belopp fem körningar i
rad: Alfa-kassans Summa intäkter avvek med exakt 70 691 varje gång, vilket
visade sig vara två egna intäktsposter och inte ett räknefel.

Två andra observationer gick åt andra hållet. Visions Summa tillgångar avvek
med 99 670 i en enda körning och aldrig mer. Den sexteckens term i GS a-kassa
som svärtas sedan dess utan att någon vet om det var en person eller ett
vanligt svenskt ord. Båda låg som fotnoter i backloggen, vilket är fel plats.

Registret räknar i stället. En anmärkning som kommer tillbaka varje gång är
något att göra något åt; en som dykt upp en gång är sannolikt slumpen.
"""

import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

REGISTER_FILENAME = "anmarkningsregister.json"


def register_path(directory) -> Path:
    return Path(directory) / REGISTER_FILENAME


def _key(finding) -> str:
    """Kassa, nyckeltal och kontroll identifierar en återkommande anmärkning.

    Inte meddelandet: beloppen i det ändras mellan körningar även när det är
    samma sak som anmärks.
    """
    metrics = "+".join(sorted(getattr(finding, "metrics", None) or []))
    return f"{finding.fund}|{finding.year}|{finding.rule}|{metrics}"


def record(findings: list, directory) -> dict:
    """Räkna upp varje anmärkning i registret och spara. Returnerar registret.

    Misslyckas aldrig uppåt. Ett register som inte går att skriva ska inte
    kunna fälla en körning som i övrigt gick bra.
    """
    directory = Path(directory)
    existing = load(directory)
    today = date.today().isoformat()

    for finding in findings or []:
        key = _key(finding)
        entry = existing.setdefault(key, {
            "kassa": finding.fund,
            "ar": finding.year,
            "kontroll": finding.rule,
            "nyckeltal": sorted(getattr(finding, "metrics", None) or []),
            "antal": 0,
            "forst": today,
        })
        entry["antal"] += 1
        entry["senast"] = today
        # Meddelandet sparas som det såg ut sist: beloppen ändras, och det är
        # det senaste som är värt att slå upp.
        entry["senaste_meddelande"] = finding.message

    try:
        directory.mkdir(parents=True, exist_ok=True)
        register_path(directory).write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as ex:
        logger.warning(f"Kunde inte skriva anmärkningsregistret: {ex}")
        return existing

    returning = sum(1 for entry in existing.values() if entry["antal"] > 1)
    logger.info(
        f"Anmärkningsregister: {len(existing)} unika anmärkningar totalt, "
        f"varav {returning} har återkommit."
    )
    return existing


def load(directory) -> dict:
    path = register_path(directory)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as ex:
        logger.warning(f"Kunde inte läsa anmärkningsregistret: {ex}")
        return {}


def summary(directory) -> list[dict]:
    """Registret sorterat, det envisaste först.

    Samma antal gånger sorteras på kassa och kontroll, så att listan ser
    likadan ut mellan körningar och går att jämföra med blotta ögat.
    """
    entries = list(load(directory).values())
    entries.sort(key=lambda entry: (
        -entry.get("antal", 0), entry.get("kassa", ""), entry.get("kontroll", "")
    ))
    return entries
