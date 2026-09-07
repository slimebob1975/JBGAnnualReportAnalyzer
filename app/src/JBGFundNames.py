"""Resolve the fund name the model reports to a canonical one.

The model reads the name off the cover page, so it varies: "Journalisternas
Arbetslöshetskassa" and "Journalisternas arbetslöshetskassa" differ by one
capital letter, and "Byggnads a-kassa" is how the fund writes it on the cover
while kassor.json records "Byggnadsarbetarnas arbetslöshetskassa".

The exporter looked names up with an exact dict match, so in a sample of seven
reports none matched. The practical cost is that the same fund spelled two ways
becomes two JSON keys and two Excel columns, which defeats comparison across
years.
"""

import difflib
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

OFFICIAL_KEY = "Officiellt namn"
SHORT_KEY = "Kort namn"
NUMBER_KEY = "KassaNummer"

# Words that carry no distinguishing information: every fund is an
# arbetslöshetskassa, and the cover page may write it a dozen ways.
_GENERIC_TERMS = (
    "arbetsloshetskassan",
    "arbetsloshetskassa",
    "erkanda arbetsloshetskassan",
    "a-kassan",
    "a-kassa",
    "akassan",
    "akassa",
    "kassan",
)

_FUZZY_CUTOFF = 0.86
_MIN_STEM_LENGTH = 4


def _fold(text: str) -> str:
    """Casefold, strip diacritics and collapse punctuation and whitespace."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = ascii_only.casefold()
    lowered = lowered.replace("&", " och ")
    lowered = re.sub(r"[^a-z0-9\- ]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _stem(text: str) -> str:
    """The distinguishing part of a fund name, with generic words removed."""
    folded = _fold(text)
    for term in _GENERIC_TERMS:
        folded = re.sub(rf"\b{re.escape(term)}\b", " ", folded)
    folded = re.sub(r"\bfor\b", " ", folded)
    return re.sub(r"\s+", " ", folded).strip(" -")


class FundNameResolver:
    def __init__(self, fund_list_path: Union[str, Path]):
        self.path = Path(fund_list_path)
        entries = json.loads(self.path.read_text(encoding="utf-8"))

        self.entries: List[dict] = entries
        self._by_stem: Dict[str, dict] = {}
        self._ambiguous_stems: set = set()

        for entry in entries:
            for candidate in (entry.get(OFFICIAL_KEY), entry.get(SHORT_KEY)):
                stem = _stem(candidate or "")
                if not stem:
                    continue
                existing = self._by_stem.get(stem)
                if existing is not None and existing is not entry:
                    # Two funds reduce to the same stem: never guess between them.
                    self._ambiguous_stems.add(stem)
                self._by_stem.setdefault(stem, entry)

        self._stems = [s for s in self._by_stem if s not in self._ambiguous_stems]

    # ------------------------------------------------------------------
    def resolve(self, reported_name: str) -> Tuple[Optional[dict], str]:
        """Return (entry, how) for a reported name.

        `how` names the strategy that matched, or explains the failure, so the
        log shows why a name was or was not canonicalised.
        """
        if not reported_name or not reported_name.strip():
            return None, "tomt namn"

        stem = _stem(reported_name)
        if not stem:
            return None, "namnet innehåller inget särskiljande ord"

        if stem in self._ambiguous_stems:
            return None, f"tvetydigt namn ({stem!r} matchar flera kassor)"

        entry = self._by_stem.get(stem)
        if entry is not None:
            return entry, "exakt (normaliserad)"

        # "Byggnads" against "Byggnadsarbetarnas": one stem is a prefix of the
        # other. Only accept when exactly one candidate matches.
        if len(stem) >= _MIN_STEM_LENGTH:
            prefixed = [
                s for s in self._stems if s.startswith(stem) or stem.startswith(s)
            ]
            unique = {id(self._by_stem[s]): self._by_stem[s] for s in prefixed}
            if len(unique) == 1:
                return next(iter(unique.values())), "prefix"
            if len(unique) > 1:
                names = sorted(e[SHORT_KEY] for e in unique.values())
                return None, f"prefix matchar flera kassor: {', '.join(names)}"

        close = difflib.get_close_matches(stem, self._stems, n=2, cutoff=_FUZZY_CUTOFF)
        if len(close) == 1:
            return self._by_stem[close[0]], f"ungefärlig ({close[0]!r})"
        if len(close) > 1:
            return None, f"ungefärlig matchning tvetydig: {close}"

        return None, "ingen matchning i kassor.json"

    def canonical_name(self, reported_name: str) -> str:
        entry, _ = self.resolve(reported_name)
        return entry[OFFICIAL_KEY] if entry else reported_name

    def short_name(self, reported_name: str) -> str:
        entry, _ = self.resolve(reported_name)
        return entry[SHORT_KEY] if entry else reported_name


def normalise_result_fund_names(
    result: dict, fund_list_path: Union[str, Path]
) -> Tuple[dict, List[str]]:
    """Rewrite the top-level fund keys of a result to canonical names.

    Unresolved names are kept as-is rather than dropped or guessed at: a wrong
    canonical name is worse than an unnormalised one. Every decision is logged.
    """
    try:
        resolver = FundNameResolver(fund_list_path)
    except (OSError, json.JSONDecodeError) as ex:
        logger.warning(f"Kunde inte läsa kassaregistret {fund_list_path}: {ex}")
        return result, []

    normalised: dict = {}
    unresolved: List[str] = []

    for reported, years in result.items():
        entry, how = resolver.resolve(reported)
        if entry is None:
            unresolved.append(reported)
            logger.warning(
                f"Kunde inte normalisera kassanamnet {reported!r}: {how}. "
                "Behåller namnet som det rapporterades."
            )
            target = reported
        else:
            target = entry[OFFICIAL_KEY]
            if target != reported:
                logger.info(
                    f"Normaliserade kassanamn {reported!r} -> {target!r} "
                    f"(via {how}, kassanummer {entry.get(NUMBER_KEY)})"
                )

        if target in normalised:
            # Two spellings of the same fund in one batch: merge the years.
            logger.info(f"Slår samman två stavningar av {target!r}.")
            for year, metrics in years.items():
                normalised[target].setdefault(year, {}).update(metrics)
        else:
            normalised[target] = dict(years)

    return normalised, unresolved
