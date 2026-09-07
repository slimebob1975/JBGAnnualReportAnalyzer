"""Strict JSON schema for the metric extraction call, and the conversion
between the model's flat reply and the nested structure the exporters expect.

Why a flat reply
----------------
The service's internal shape is

    {fund: {year: {metric: {värde, källa, säkerhet, kommentar}}}}

which cannot be expressed as a strict JSON schema: OpenAI's strict mode needs
every property named up front and `additionalProperties: false`, and fund names
and years are data, not schema. So the model is asked for

    {"kassa": ..., "år": ..., "nyckeltal": [{"namn": ..., "värde": ...}, ...]}

where `namn` is an enum of the 18 metric names from
nyckeltalsdefinitioner.json. That is strictly checkable, it makes an
unrecognised or invented metric name impossible, and a metric the model cannot
find is simply absent from the list rather than present with a null value.
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_NAME = "nyckeltalsextraktion"

FIELD_NAME = "namn"
FIELD_VALUE = "värde"
FIELD_SOURCE = "källa"
FIELD_CERTAINTY = "säkerhet"
FIELD_COMMENT = "kommentar"
FIELD_FUND = "kassa"
FIELD_YEAR = "år"
FIELD_METRICS = "nyckeltal"

METRIC_KEY = "Nyckeltal"

# Certainty as three named levels rather than a free float.
#
# Asked for a number between 0 and 1, the model answered exactly 1.0 for 88%
# of values in a real run, which makes the scale, and the colour coding built
# on it, close to useless. A small enum forces a commitment to a described
# situation instead of a number that can always be rounded up.
CERTAINTY_EXPLICIT = "explicit"
CERTAINTY_DERIVED = "härledd"
CERTAINTY_UNCERTAIN = "osäker"

CERTAINTY_LEVELS = {
    CERTAINTY_EXPLICIT: (
        "Beloppet står ordagrant i dokumentet under en rubrik som otvetydigt "
        "motsvarar nyckeltalet."
    ),
    CERTAINTY_DERIVED: (
        "Beloppet är uträknat, sammansatt av flera poster, eller hämtat under "
        "en rubrik som du behövt tolka."
    ),
    CERTAINTY_UNCERTAIN: (
        "Kvalificerad gissning som bör kontrolleras mot källdokumentet."
    ),
}

# Numeric equivalents, for sorting conflicts and for reading older result
# files that still contain a float.
CERTAINTY_RANK = {
    CERTAINTY_EXPLICIT: 1.0,
    CERTAINTY_DERIVED: 0.6,
    CERTAINTY_UNCERTAIN: 0.25,
}


def certainty_rank(value) -> float:
    """Comparable number for a certainty, whether a level or a legacy float."""
    if isinstance(value, str):
        return CERTAINTY_RANK.get(value.strip().casefold(), 0.0)
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def certainty_level(value) -> str:
    """The level a value belongs to, mapping legacy floats onto the scale."""
    if isinstance(value, str) and value.strip().casefold() in CERTAINTY_RANK:
        return value.strip().casefold()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value >= 0.9:
            return CERTAINTY_EXPLICIT
        if value >= 0.5:
            return CERTAINTY_DERIVED
        return CERTAINTY_UNCERTAIN
    return ""


def load_metric_names(metrics_path: str | Path) -> list[str]:
    metrics = json.loads(Path(metrics_path).read_text(encoding="utf-8"))
    return [m[METRIC_KEY] for m in metrics]


def build_schema(metric_names: list[str]) -> dict[str, Any]:
    """Build the strict response schema for the given metric names."""
    return {
        "name": SCHEMA_NAME,
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [FIELD_FUND, FIELD_YEAR, FIELD_METRICS],
            "properties": {
                FIELD_FUND: {
                    "type": "string",
                    "description": (
                        "Namnet på arbetslöshetskassan som årsredovisningen gäller, "
                        "så fullständigt som det står i dokumentet."
                    ),
                },
                FIELD_YEAR: {
                    "type": ["integer", "null"],
                    "description": (
                        "Räkenskapsåret som värdena gäller. Null endast om året "
                        "inte går att fastställa."
                    ),
                },
                FIELD_METRICS: {
                    "type": "array",
                    "description": (
                        "Endast de nyckeltal som faktiskt återfinns i utdraget. "
                        "Utelämna nyckeltal du inte hittar - lägg inte till dem "
                        "med värdet null."
                    ),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            FIELD_NAME,
                            FIELD_VALUE,
                            FIELD_SOURCE,
                            FIELD_CERTAINTY,
                            FIELD_COMMENT,
                        ],
                        "properties": {
                            FIELD_NAME: {
                                "type": "string",
                                "enum": metric_names,
                                "description": "Nyckeltalets primära namn.",
                            },
                            FIELD_VALUE: {
                                "type": ["number", "null"],
                                "description": (
                                    "Beloppet som ett tal utan tusenavgränsare. "
                                    "Kostnader och skulder anges som positiva tal."
                                ),
                            },
                            FIELD_SOURCE: {
                                "type": "string",
                                "description": "Sidnummer och rubrik där värdet hittades.",
                            },
                            FIELD_CERTAINTY: {
                                "type": "string",
                                "enum": list(CERTAINTY_LEVELS),
                                "description": " ".join(
                                    f"'{level}': {text}"
                                    for level, text in CERTAINTY_LEVELS.items()
                                ),
                            },
                            FIELD_COMMENT: {
                                "type": "string",
                                "description": (
                                    "Obligatorisk motivering: hur värdet hittades och "
                                    "varför du är säker eller osäker."
                                ),
                            },
                        },
                    },
                },
            },
        },
    }


def flat_to_nested(payload: dict[str, Any], fallback_year: int | None = None) -> dict[str, Any]:
    """Convert the model's flat reply into {fund: {year: {metric: {...}}}}.

    Metrics carrying a null value are dropped: the schema tells the model to
    omit them, and one that slips through carries no information.
    """
    fund = (payload.get(FIELD_FUND) or "").strip()
    if not fund:
        logger.warning("GPT-svaret saknar kassanamn. Hoppar över detta svar.")
        return {}

    year = payload.get(FIELD_YEAR) or fallback_year
    if year is None:
        logger.warning(f"GPT-svaret för {fund} saknar år. Hoppar över detta svar.")
        return {}

    metrics: dict[str, Any] = {}
    for item in payload.get(FIELD_METRICS) or []:
        name = (item.get(FIELD_NAME) or "").strip()
        if not name:
            continue
        if item.get(FIELD_VALUE) is None:
            continue
        metrics[name] = {
            FIELD_VALUE: item.get(FIELD_VALUE),
            FIELD_SOURCE: item.get(FIELD_SOURCE, ""),
            FIELD_CERTAINTY: item.get(FIELD_CERTAINTY),
            FIELD_COMMENT: item.get(FIELD_COMMENT, ""),
        }

    if not metrics:
        return {}
    return {fund: {str(year): metrics}}


def describe_for_second_pass(metric_names: list[str]) -> str:
    """Instruction for the targeted follow-up call.

    The first pass asks for all 18 metrics at once and occasionally misses one
    in a long document. This asks again for only the ones that came back
    absent, which is both a narrower task and a cheap one.
    """
    listed = "\n".join(f"- {name}" for name in metric_names)
    return (
        "## Riktad omsökning\n"
        "En första genomgång av detta dokument hittade alla nyckeltal utom "
        f"följande {len(metric_names)}:\n{listed}\n\n"
        "Din uppgift nu är att leta igen efter **enbart** dessa. Titta särskilt "
        "i noter, flerårsöversikter och tabeller som är lätta att förbigå.\n\n"
        f"Hittar du dem inte heller nu ska du utelämna dem ur `{FIELD_METRICS}`. "
        "Ett utelämnat nyckeltal är ett korrekt svar: det betyder att posten "
        "inte finns i dokumentet. Gissa inte, och hämta inte ett närliggande "
        "belopp som egentligen avser något annat.\n"
    )


def describe_for_prompt(metric_names: list[str]) -> str:
    """A short reminder of the contract, appended to the system prompt.

    Structured outputs enforce the shape, but stating the omission rule in
    words measurably reduces the "found nothing, so here is null with säkerhet
    0.1" behaviour that generated every merge conflict in the sample runs.
    """
    return (
        "## Svarsformat\n"
        "Svaret valideras mot ett JSON-schema, så du behöver inte återge strukturen.\n"
        "Två regler är avgörande:\n"
        f"1. `{FIELD_METRICS}` ska **endast** innehålla nyckeltal som faktiskt "
        "återfinns i det utdrag du fått. Hittar du inte ett nyckeltal ska du "
        "**utelämna det helt** ur listan. Lägg aldrig till ett nyckeltal med "
        f"`{FIELD_VALUE}: null` och låg `{FIELD_CERTAINTY}` bara för att det "
        "efterfrågas - utdraget är en del av en längre årsredovisning och "
        "posten finns oftast på en sida du inte ser.\n"
        f"2. `{FIELD_COMMENT}` är obligatorisk för varje nyckeltal du tar med.\n"
        f"3. `{FIELD_CERTAINTY}` är ett av tre värden, inte en siffra: "
        + ", ".join(f"`{level}`" for level in CERTAINTY_LEVELS)
        + ".\n"
    )
