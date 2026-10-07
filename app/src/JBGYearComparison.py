"""Jämförelse mellan år.

Alla andra kontroller arbetar inom en enskild årsredovisning: delsummor mot
delposter, noter mot räkningar, två avläsningar av samma dokument. Det duger
så länge någon matar in siffrorna för hand parallellt, för då finns en extern
referens. När den inmatningen upphör kontrollerar verktyget sig självt mot sig
självt, och ett värde som är fel på ett sätt aritmetiken råkar tåla - läst ur
fel kolumn, rätt form men fel rad - har ingenting som motsäger det.

Föregående år är den enda externa referens som finns kvar.

Trösklarna är mätta, inte gissade. På 234 jämförbara par mellan 2024 och 2025
var medianrörelsen 1,16 gånger, nittionde percentilen 6,4 och nittionionde 24.
En tröskel vid tio hade alltså flaggat var elfte par, och de flesta med rätta:
Akademikernas finansiella intäkter föll faktiskt från 68 878 till 2 869.

Därför två nivåer. Anmärkning bara vid hundra gånger eller mer, där rörelsen
inte rimligen är verklig. Och ett blad som listar varje rörelse över det
dubbla, sorterat, utan allvarlighetsgrad - där syns GS a-kassas
"Antal ersättningsdagar 22 286 -> 306 045" nära toppen, en felläsning på 13,7
gånger som ingen tröskel vågar anmärka på men som en människa ser direkt.
"""

import logging

logger = logging.getLogger(__name__)

# Golvet gäller det mindre av de två värdena, inte det större. En notpost som
# går från -832 till 1 rör sig 832 gånger utan att betyda någonting, och det
# är det lilla talet som avgör det. Mätt med golvet på det mindre värdet föll
# 234 av 278 par kvar och bara ett översteg hundra gånger; mätt på det större
# var nittionionde percentilen 832.
MIN_MAGNITUDE = 100
# Rörelser över det dubbla kommer med i bladet.
LISTED_RATIO = 2.0
# Vid hundra gånger är rörelsen inte verklig. Det är enhet eller felläsning.
FINDING_RATIO = 100.0

RULE_NAME = "Orimlig förändring mot föregående år"


def _belopp(value: float) -> str:
    """Tal med mellanslag mellan tusentalen, utan att röra meningens tecken."""
    return f"{value:,.0f}".replace(",", "\u00a0")


def _numeric(entry):
    if isinstance(entry, dict):
        entry = entry.get("värde")
    if isinstance(entry, bool):
        return None
    return float(entry) if isinstance(entry, (int, float)) else None


def _metrics_by_year(result: dict) -> dict:
    """Kassa -> år -> nyckeltal -> tal, utan metadata och tomma värden."""
    out: dict[str, dict[str, dict[str, float]]] = {}
    for fund, years in (result or {}).items():
        if fund.startswith("_") or not isinstance(years, dict):
            continue
        for year, metrics in years.items():
            if not isinstance(metrics, dict):
                continue
            values = {
                name: value
                for name, entry in metrics.items()
                if (value := _numeric(entry)) is not None
            }
            if values:
                out.setdefault(fund, {})[str(year)] = values
    return out


def compare(result: dict) -> list[dict]:
    """Alla rörelser mellan på varandra följande år, största först.

    Ett nyckeltal som finns det ena året men inte det andra är en lucka, inte
    en rörelse, och tas inte med. Statistiken i bilaga 2 lades om inför 2025 -
    tio av 64 värden saknas i 2023 års rapporter - och den sortens frånvaro
    säger ingenting om siffrorna.
    """
    movements = []
    for fund, by_year in _metrics_by_year(result).items():
        years = sorted(by_year)
        for earlier, later in zip(years, years[1:], strict=False):
            before, after = by_year[earlier], by_year[later]
            for name in sorted(set(before) & set(after)):
                first, second = before[name], after[name]
                if not first or not second:
                    continue
                if min(abs(first), abs(second)) < MIN_MAGNITUDE:
                    continue
                ratio = max(abs(first), abs(second)) / min(abs(first), abs(second))
                flipped = (first < 0) != (second < 0)
                if ratio < LISTED_RATIO and not flipped:
                    continue
                movements.append({
                    "kassa": fund,
                    "nyckeltal": name,
                    "fran_ar": earlier,
                    "till_ar": later,
                    "fran": first,
                    "till": second,
                    "kvot": ratio,
                    "teckenbyte": flipped,
                })
    movements.sort(key=lambda m: (-m["kvot"], m["kassa"], m["nyckeltal"]))
    if movements:
        logger.info(
            f"Jämförelse mellan år: {len(movements)} rörelser över "
            f"{LISTED_RATIO:g} gånger."
        )
    return movements


def findings(movements: list[dict]) -> list[dict]:
    """De rörelser som inte rimligen är verkliga.

    Hundra gånger eller mer. Ett teckenbyte räcker inte: en kassa som går från
    överskott till underskott byter tecken på årets resultat varje gång det
    händer, och det är ingen felläsning.
    """
    serious = [m for m in movements if m["kvot"] >= FINDING_RATIO]
    return [
        {
            "kassa": m["kassa"],
            "ar": m["till_ar"],
            "nyckeltal": m["nyckeltal"],
            "meddelande": (
                f"{m['nyckeltal']} har gått från {_belopp(m['fran'])} "
                f"({m['fran_ar']}) till {_belopp(m['till'])} ({m['till_ar']}), "
                f"en förändring på {_belopp(m['kvot'])} gånger. Så stora "
                "rörelser är sällan verkliga; kontrollera enhet och avläsning "
                "mot båda årsredovisningarna."
            ),
        }
        for m in serious
    ]
