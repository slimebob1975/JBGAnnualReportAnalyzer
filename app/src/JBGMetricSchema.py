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
from typing import Any, Dict, List, Union

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


def load_metric_names(metrics_path: Union[str, Path]) -> List[str]:
    metrics = json.loads(Path(metrics_path).read_text(encoding="utf-8"))
    return [m[METRIC_KEY] for m in metrics]


def build_schema(metric_names: List[str]) -> Dict[str, Any]:
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
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                                "description": "Din egen bedömning av säkerheten, 0-1.",
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


def flat_to_nested(payload: Dict[str, Any], fallback_year: Union[int, None] = None) -> Dict[str, Any]:
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

    metrics: Dict[str, Any] = {}
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


def describe_for_prompt(metric_names: List[str]) -> str:
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
    )
