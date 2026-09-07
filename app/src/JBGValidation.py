"""Arithmetic checks on the extracted metrics.

The model reads figures out of prose and tables, so the useful question is not
"is it confident" but "do the numbers agree with each other". A balance sheet
has identities that must hold, and when one fails it points at exactly which
figure was misread.

On a seven-report sample, six funds satisfied
    Balansomslutning = Eget kapital + Skulder + Utgående avsättningar
to the krona, and the seventh was out by 2 867 tkr in two consecutive runs.
That is a real extraction error, and nothing in the pipeline noticed it.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.src import JBGMetricSchema as schema

logger = logging.getLogger(__name__)

FIELD_VALUE = "värde"
FIELD_CERTAINTY = "säkerhet"

SEVERITY_ERROR = "fel"
SEVERITY_WARNING = "varning"

# Relative tolerance for sums reported in whole tkr. Tight, because the sample
# data balances exactly; loosening it would hide the errors worth finding.
RELATIVE_TOLERANCE = 0.001
# Below this many values, a single certainty level says nothing about the
# scale; it just means a small sample.
MIN_VALUES_FOR_CERTAINTY_CHECK = 20
ABSOLUTE_TOLERANCE = 2.0


@dataclass
class Finding:
    fund: str
    year: str
    rule: str
    message: str
    severity: str = SEVERITY_WARNING
    metrics: list[str] | None = None

    def __str__(self) -> str:
        return f"[{self.fund} {self.year}] {self.rule}: {self.message}"

    def as_dict(self) -> dict:
        return {
            "kassa": self.fund,
            "år": self.year,
            "kontroll": self.rule,
            "allvarlighet": self.severity,
            "anmärkning": self.message,
            "berörda_nyckeltal": list(self.metrics or []),
        }


@dataclass
class Rule:
    name: str
    description: str
    metrics: list[str]
    check: Callable[[dict[str, float]], str | None]
    severity: str = SEVERITY_WARNING


def _tolerance(magnitude: float) -> float:
    return max(ABSOLUTE_TOLERANCE, abs(magnitude) * RELATIVE_TOLERANCE)


def _fmt(amount: float) -> str:
    """Swedish thousands separator.

    Formats the number only. A blanket str.replace(",", " ") over the whole
    message also removed the commas in the prose around it.
    """
    return f"{amount:,.0f}".replace(",", "\u00a0")


def _provisions_counted_twice(values: dict[str, float]) -> bool:
    """True when Eget kapital + Skulder alone equals the balance sheet total.

    A real pattern in the corpus: most funds satisfy
    EK + Skulder + Avsättningar = BO, but where a report presents no separate
    "Summa skulder" one of the two figures ends up containing the provisions.
    """
    total = values["Balansomslutning"]
    without = values["Eget kapital"] + values["Skulder"]
    return values["Utgående avsättningar"] > 0 and abs(total - without) <= _tolerance(total)


def _balance_identity(values: dict[str, float]) -> str | None:
    total = values["Balansomslutning"]
    equity = values["Eget kapital"]
    debt = values["Skulder"]
    provisions = values["Utgående avsättningar"]
    parts = equity + debt + provisions
    diff = total - parts

    if abs(diff) <= _tolerance(total):
        return None

    message = (
        f"Balansomslutning {_fmt(total)} stämmer inte med "
        f"Eget kapital + Skulder + Utgående avsättningar = {_fmt(parts)} "
        f"(differens {_fmt(diff) if diff < 0 else '+' + _fmt(diff)})."
    )

    if _provisions_counted_twice(values):
        # Both figures are equally consistent with the arithmetic, so name
        # both rather than blaming one. An earlier version pointed at Skulder,
        # and on the one real case the culprit was more likely Eget kapital.
        message += (
            f" Eget kapital + Skulder = {_fmt(equity + debt)} är däremot exakt "
            f"lika med balansomslutningen, vilket tyder på att avsättningarna "
            f"{_fmt(provisions)} räknats med två gånger. Antingen ingår de redan "
            f"i Eget kapital, som då borde vara {_fmt(total - debt - provisions)} "
            f"i stället för {_fmt(equity)}, eller i Skulder, som då borde vara "
            f"{_fmt(total - equity - provisions)} i stället för {_fmt(debt)}. "
            "Kontrollera vilket mot balansräkningen."
        )

    return message


def _current_assets_within_total(values: dict[str, float]) -> str | None:
    current, total = values["Omsättningstillgångar"], values["Balansomslutning"]
    if current <= total + _tolerance(total):
        return None
    return (
        f"Omsättningstillgångar {_fmt(current)} är större än "
        f"Balansomslutning {_fmt(total)}"
    )


def _cash_within_current_assets(values: dict[str, float]) -> str | None:
    cash, current = values["Kassa och bank"], values["Omsättningstillgångar"]
    if cash <= current + _tolerance(current):
        return None
    return (
        f"Kassa och bank {_fmt(cash)} är större än "
        f"Omsättningstillgångar {_fmt(current)}"
    )


def _non_negative(metric: str) -> Callable[[dict[str, float]], str | None]:
    def check(values: dict[str, float]) -> str | None:
        value = values[metric]
        if value >= 0:
            return None
        return (
            f"{metric} är negativt ({_fmt(value)}). Enligt instruktionerna ska "
            "belopp anges som positiva tal."
        )

    return check


RULES: list[Rule] = [
    Rule(
        name="Balansräkningen balanserar",
        description=(
            "Balansomslutning ska vara lika med summan av eget kapital, "
            "skulder och avsättningar."
        ),
        metrics=["Balansomslutning", "Eget kapital", "Skulder", "Utgående avsättningar"],
        check=_balance_identity,
        severity=SEVERITY_ERROR,
    ),
    Rule(
        name="Omsättningstillgångar ryms i balansomslutningen",
        description="Omsättningstillgångar kan inte överstiga balansomslutningen.",
        metrics=["Omsättningstillgångar", "Balansomslutning"],
        check=_current_assets_within_total,
        severity=SEVERITY_ERROR,
    ),
    Rule(
        name="Kassa och bank ryms i omsättningstillgångarna",
        description="Kassa och bank är en del av omsättningstillgångarna.",
        metrics=["Kassa och bank", "Omsättningstillgångar"],
        check=_cash_within_current_assets,
        severity=SEVERITY_ERROR,
    ),
    Rule(
        name="Balansomslutning är positiv",
        description="En balansomslutning ska vara ett positivt belopp.",
        metrics=["Balansomslutning"],
        check=_non_negative("Balansomslutning"),
        severity=SEVERITY_WARNING,
    ),
    Rule(
        name="Administrationskostnader anges positivt",
        description="Kostnader rapporteras som positiva belopp, inte med minustecken.",
        metrics=["Administrationskostnader"],
        check=_non_negative("Administrationskostnader"),
        severity=SEVERITY_WARNING,
    ),
]


def _numeric(entry: Any) -> float | None:
    if not isinstance(entry, dict):
        return None
    value = entry.get(FIELD_VALUE)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("\u00a0", "").replace(" ", "").replace("kr", "")
        cleaned = cleaned.replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def validate(result: dict) -> list[Finding]:
    """Run every rule over every fund and year that has the needed metrics.

    A rule is skipped, not failed, when a metric it needs is missing: the model
    is told to omit what it cannot find, so absence is expected.
    """
    findings: list[Finding] = []

    for fund, years in (result or {}).items():
        if not isinstance(years, dict):
            continue
        for year, metrics in years.items():
            if not isinstance(metrics, dict):
                continue
            for rule in RULES:
                values = {}
                missing = False
                for name in rule.metrics:
                    number = _numeric(metrics.get(name))
                    if number is None:
                        missing = True
                        break
                    values[name] = number
                if missing:
                    continue
                try:
                    problem = rule.check(values)
                except Exception as ex:  # pragma: no cover - a rule must never crash a run
                    logger.warning(f"Kontrollen '{rule.name}' kunde inte utföras: {ex}")
                    continue
                if problem:
                    findings.append(
                        Finding(
                            fund=fund,
                            year=str(year),
                            rule=rule.name,
                            message=problem,
                            severity=rule.severity,
                            metrics=list(rule.metrics),
                        )
                    )

    return findings


def log_findings(findings: list[Finding]) -> None:
    if not findings:
        logger.info("Rimlighetskontrollerna av nyckeltalen gav inga anmärkningar.")
        return

    errors = [f for f in findings if f.severity == SEVERITY_ERROR]
    logger.warning(
        f"Rimlighetskontrollerna gav {len(findings)} anmärkning(ar), "
        f"varav {len(errors)} allvarliga. Kontrollera dessa mot källdokumentet."
    )
    for finding in findings:
        logger.warning(str(finding))


def certainty_histogram(result: dict) -> dict[str, int]:
    """Count reported certainty levels.

    Logged after every run so the calibration is visible. Legacy float values
    are mapped onto the same three levels so older result files can still be
    summarised.
    """
    bands = {level: 0 for level in schema.CERTAINTY_LEVELS}
    bands["saknas"] = 0
    for years in (result or {}).values():
        if not isinstance(years, dict):
            continue
        for metrics in years.values():
            if not isinstance(metrics, dict):
                continue
            for entry in metrics.values():
                raw = entry.get(FIELD_CERTAINTY) if isinstance(entry, dict) else None
                level = schema.certainty_level(raw)
                bands[level if level in bands else "saknas"] += 1
    return bands


def log_certainty_histogram(result: dict) -> None:
    """Log how the values were arrived at.

    No warning on a high "explicit" share. A run came in at 98% explicit and
    the three exceptions were exactly the values that deserved checking: two
    Skulder figures computed by subtraction and one genuine judgement call.
    For a lookup task against structured financial statements, nearly
    everything being an explicit reading is the expected outcome, not a sign
    the scale has failed. Only a complete absence of variation says that.
    """
    bands = certainty_histogram(result)
    total = sum(bands.values())
    if not total:
        return
    summary = ", ".join(f"{k}: {v}" for k, v in bands.items() if v)
    logger.info(f"Fördelning av angiven säkerhet ({total} nyckeltal): {summary}")

    levels_used = [k for k, v in bands.items() if v and k != "saknas"]
    if total >= MIN_VALUES_FOR_CERTAINTY_CHECK and len(levels_used) <= 1:
        logger.warning(
            f"Samtliga {total} nyckeltal fick samma säkerhetsnivå "
            f"({levels_used[0] if levels_used else 'ingen'}). Skalan skiljer då "
            "inte mellan värden som bör kontrolleras och värden som inte "
            "behöver det."
        )


def findings_by_cell(findings: list[Finding]) -> dict[tuple, list[Finding]]:
    """Index findings by (fund, year, metric) so an exporter can mark cells."""
    index: dict[tuple, list[Finding]] = {}
    for finding in findings:
        for metric in finding.metrics or []:
            index.setdefault((finding.fund, finding.year, metric), []).append(finding)
    return index
