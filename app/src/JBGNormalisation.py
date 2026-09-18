"""Decisions the definitions already make, taken out of the model's hands.

Two kinds of correction live here, and they share a rationale: the answer is
determined by something we know, so asking a language model to get it right
twice in a row is a worse method than computing it.

**Signs.** Nine metrics are documented as "anges som ett positivt belopp" and
nine note sums must carry the sign of the row they specify. Wording the
instruction more carefully helped - note-versus-row findings halved - but it
moved the problem rather than removing it: of 52 values that differed between
two readings of the same document, 20 were the same amount with the sign
flipped. That is the model exercising a judgement it should not have been
given.

**Units.** Bilaga 2 asks for amounts without saying in what unit, and the
funds answer differently. `Utbetald arbetslöshetsersättning` arrived as 2 924
from one fund and 192 397 000 from the median of the others - the same
quantity in millions and in kronor. No arithmetic identity covers these
metrics, so nothing else would notice. Here the answer is not determined by
the definitions but by the other 23 funds, which is nearly as good provided
the correction is conservative and says so.

Both record what they did in the entry's comment. A correction nobody can see
is worse than none.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FIELD_VALUE = "värde"
FIELD_COMMENT = "kommentar"

POSITIVE_MARKER = "positivt belopp"
COUNTERPART_FIELD = "Motsvarar"
AMOUNT_UNITS = ("belopp", "kronor")

# Factors a Swedish annual report plausibly uses for an amount.
UNIT_FACTORS = (1000, 1_000_000)
# Only correct a value that is clearly in another unit ...
UNIT_MIN_DEVIATION = 100
# ... and only when the correction lands near the other funds.
UNIT_MAX_RESIDUAL = 10
MIN_FUNDS_FOR_UNIT_NORMALISATION = 5


def _load(metrics_path) -> list[dict]:
    try:
        with open(metrics_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as ex:
        logger.warning(f"Kunde inte läsa {metrics_path}: {ex}")
        return []


def _numeric(entry) -> float | None:
    if not isinstance(entry, dict):
        return None
    value = entry.get(FIELD_VALUE)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _note(entry: dict, text: str) -> None:
    existing = entry.get(FIELD_COMMENT) or ""
    entry[FIELD_COMMENT] = f"{existing} {text}".strip() if existing else text


def sign_conventions(metrics_path) -> tuple[set[str], dict[str, str]]:
    """Metrics that must be positive, and notes that follow another row."""
    positive: set[str] = set()
    counterparts: dict[str, str] = {}
    for entry in _load(metrics_path):
        name = entry.get("Nyckeltal")
        if not name:
            continue
        if POSITIVE_MARKER in (entry.get("Specifika instruktioner") or ""):
            positive.add(name)
        counterpart = entry.get(COUNTERPART_FIELD)
        if counterpart:
            counterparts[name] = counterpart
    return positive, counterparts


def normalise_signs(result: dict, metrics_path) -> int:
    """Force the documented sign convention. Returns the number of changes.

    Amounts are never rescaled, only their sign is set: a cost of -2 924
    becomes 2 924 where the definition says costs are positive, and a note sum
    takes the sign of the row it specifies. A note whose counterpart is absent
    is left alone, since there is then nothing to agree with.
    """
    positive, counterparts = sign_conventions(metrics_path)
    if not positive and not counterparts:
        return 0

    changed = 0
    for fund, years in (result or {}).items():
        if not isinstance(years, dict):
            continue
        for year, metrics in years.items():
            if not isinstance(metrics, dict):
                continue

            for name in positive:
                entry = metrics.get(name)
                value = _numeric(entry)
                if value is None or value >= 0:
                    continue
                entry[FIELD_VALUE] = abs(value)
                _note(entry, "Tecken normaliserat: posten anges som ett positivt belopp.")
                changed += 1
                logger.debug(f"{fund} {year}: {name} {value} -> {abs(value)}")

            for name, counterpart in counterparts.items():
                entry = metrics.get(name)
                value = _numeric(entry)
                reference = _numeric(metrics.get(counterpart))
                if value is None or reference is None or value == 0 or reference == 0:
                    continue
                if (value < 0) == (reference < 0):
                    continue
                entry[FIELD_VALUE] = -value
                _note(
                    entry,
                    f"Tecken normaliserat efter '{counterpart}' i räkningen.",
                )
                changed += 1
                logger.debug(f"{fund} {year}: {name} {value} -> {-value}")

    if changed:
        logger.info(f"Teckenkonvention: {changed} värden normaliserade.")
    return changed


def amount_metrics(metrics_path) -> set[str]:
    return {
        entry["Nyckeltal"]
        for entry in _load(metrics_path)
        if entry.get("Enhet") in AMOUNT_UNITS
    }


def _reference_value(values: list[float]) -> float | None:
    """What the funds mostly mean, in kronor.

    A value with öre can only be kronor, so where several funds report them
    they settle the question on their own. Otherwise the median stands in,
    which is sound as long as most funds agree - and where they do not, the
    residual test below refuses the correction anyway.
    """
    with_ore = [abs(v) for v in values if abs(v) % 1]
    basis = with_ore if len(with_ore) >= 3 else [abs(v) for v in values]
    basis.sort()
    return basis[len(basis) // 2] if basis else None


def normalise_units(result: dict, metrics_path) -> list[tuple[str, str, str, float, float]]:
    """Convert amounts reported in thousands or millions into kronor.

    Returns what it changed as (fund, year, metric, from, to), so the caller
    can report it rather than have figures quietly move between runs.

    Deliberately timid. A value must be at least a hundredfold away from what
    the other funds report before it is touched at all, and the chosen factor
    must bring it within a factor of ten of them; anything else is left as it
    stands for a human to judge. This corrects the fund that wrote tkr where
    the rest wrote kronor. It does not correct a fund that is merely unusual.
    """
    metrics = amount_metrics(metrics_path)
    if not metrics:
        return []

    # metric -> year -> fund -> value
    columns: dict[str, dict[str, dict[str, float]]] = {}
    for fund, years in (result or {}).items():
        if not isinstance(years, dict):
            continue
        for year, entries in years.items():
            if not isinstance(entries, dict):
                continue
            for name in metrics:
                value = _numeric(entries.get(name))
                if value is None or value == 0:
                    continue
                columns.setdefault(name, {}).setdefault(str(year), {})[fund] = value

    applied = []
    for name, per_year in columns.items():
        for year, per_fund in per_year.items():
            if len(per_fund) < MIN_FUNDS_FOR_UNIT_NORMALISATION:
                continue
            for fund, value in sorted(per_fund.items()):
                peers = [v for peer, v in per_fund.items() if peer != fund]
                reference = _reference_value(peers)
                if not reference:
                    continue
                if abs(value) * UNIT_MIN_DEVIATION > reference:
                    continue  # not far enough off to be a unit

                best = min(
                    UNIT_FACTORS,
                    key=lambda f: abs(abs(value) * f / reference - 1),
                )
                converted = value * best
                residual = max(abs(converted), reference) / min(abs(converted), reference)
                if residual > UNIT_MAX_RESIDUAL:
                    continue  # the correction would not explain the gap

                entry = result[fund][year][name]
                entry[FIELD_VALUE] = converted
                unit = "tusental kronor" if best == 1000 else "miljoner kronor"
                _note(
                    entry,
                    f"Omräknat från {unit} till kronor ({value:g} × {best}). "
                    "Beloppet avvek kraftigt från övriga kassor, som redovisar "
                    "i kronor.",
                )
                applied.append((fund, str(year), name, value, converted))

    if applied:
        logger.info(
            f"Enhet: {len(applied)} belopp omräknade till kronor "
            f"({len({a[2] for a in applied})} nyckeltal)."
        )
        for fund, year, name, before, after in applied:
            logger.info(f"  {fund} {year}: {name} {before:g} -> {after:g}")
    return applied


def default_metrics_path() -> Path:
    return Path(__file__).resolve().parents[1] / "prompt" / "json" / (
        "nyckeltalsdefinitioner.json"
    )
