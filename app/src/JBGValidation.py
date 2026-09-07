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
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

FIELD_VALUE = "värde"

SEVERITY_ERROR = "fel"
SEVERITY_WARNING = "varning"

# Relative tolerance for sums reported in whole tkr. Tight, because the sample
# data balances exactly; loosening it would hide the errors worth finding.
RELATIVE_TOLERANCE = 0.001
ABSOLUTE_TOLERANCE = 2.0


@dataclass
class Finding:
    fund: str
    year: str
    rule: str
    message: str
    severity: str = SEVERITY_WARNING
    metrics: Optional[List[str]] = None

    def __str__(self) -> str:
        return f"[{self.fund} {self.year}] {self.rule}: {self.message}"


@dataclass
class Rule:
    name: str
    description: str
    metrics: List[str]
    check: Callable[[Dict[str, float]], Optional[str]]
    severity: str = SEVERITY_WARNING


def _tolerance(magnitude: float) -> float:
    return max(ABSOLUTE_TOLERANCE, abs(magnitude) * RELATIVE_TOLERANCE)


def _balance_identity(values: Dict[str, float]) -> Optional[str]:
    total = values["Balansomslutning"]
    parts = values["Eget kapital"] + values["Skulder"] + values["Utgående avsättningar"]
    diff = total - parts
    if abs(diff) <= _tolerance(total):
        return None
    return (
        f"Balansomslutning {total:,.0f} stämmer inte med "
        f"Eget kapital + Skulder + Utgående avsättningar = {parts:,.0f} "
        f"(differens {diff:+,.0f})"
    ).replace(",", " ")


def _current_assets_within_total(values: Dict[str, float]) -> Optional[str]:
    current, total = values["Omsättningstillgångar"], values["Balansomslutning"]
    if current <= total + _tolerance(total):
        return None
    return (
        f"Omsättningstillgångar {current:,.0f} är större än "
        f"Balansomslutning {total:,.0f}"
    ).replace(",", " ")


def _cash_within_current_assets(values: Dict[str, float]) -> Optional[str]:
    cash, current = values["Kassa och bank"], values["Omsättningstillgångar"]
    if cash <= current + _tolerance(current):
        return None
    return (
        f"Kassa och bank {cash:,.0f} är större än "
        f"Omsättningstillgångar {current:,.0f}"
    ).replace(",", " ")


def _non_negative(metric: str) -> Callable[[Dict[str, float]], Optional[str]]:
    def check(values: Dict[str, float]) -> Optional[str]:
        value = values[metric]
        if value >= 0:
            return None
        return (
            f"{metric} är negativt ({value:,.0f}). Enligt instruktionerna ska "
            "belopp anges som positiva tal."
        ).replace(",", " ")

    return check


RULES: List[Rule] = [
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


def _numeric(entry: Any) -> Optional[float]:
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


def validate(result: dict) -> List[Finding]:
    """Run every rule over every fund and year that has the needed metrics.

    A rule is skipped, not failed, when a metric it needs is missing: the model
    is told to omit what it cannot find, so absence is expected.
    """
    findings: List[Finding] = []

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


def log_findings(findings: List[Finding]) -> None:
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


def findings_by_cell(findings: List[Finding]) -> Dict[tuple, List[Finding]]:
    """Index findings by (fund, year, metric) so an exporter can mark cells."""
    index: Dict[tuple, List[Finding]] = {}
    for finding in findings:
        for metric in finding.metrics or []:
            index.setdefault((finding.fund, finding.year, metric), []).append(finding)
    return index
