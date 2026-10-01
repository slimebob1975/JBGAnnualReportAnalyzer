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

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.src import JBGMetricSchema as schema
from app.src import JBGNormalisation as normalisation

logger = logging.getLogger(__name__)

FIELD_VALUE = "värde"
FIELD_CERTAINTY = "säkerhet"

SEVERITY_ERROR = "fel"
SEVERITY_WARNING = "varning"
# Inte ett fel, bara något läsaren bör lägga märke till. Kassorna lägger till
# egna poster och noter utöver föreskriften, så en summa som överstiger sina
# uppräknade delposter är väntad och inte ett räknefel.
SEVERITY_INFO = "upplysning"

# Rounding in whole tkr can shift a sum by one either way; anything larger is
# a real difference. A relative tolerance was far too permissive: on a balance
# sheet of 730 million it let 730 000 pass unnoticed.
RELATIVE_TOLERANCE = 0.0
# Below this many values, a single certainty level says nothing about the
# scale; it just means a small sample.
MIN_VALUES_FOR_CERTAINTY_CHECK = 20
ABSOLUTE_TOLERANCE = 1.0
# Two checks disagreeing by the same amount usually share a cause, but only
# once the amount is large enough that the coincidence is unlikely.
LINK_MIN_DIFFERENCE = 10
LINK_MAX_GROUP = 3
# Amount metrics from bilaga 2 are the one place where the funds themselves
# disagree about the unit: some report kronor, some thousands. A value a
# hundredth of what its peers report is a unit, not a fund that is a hundred
# times smaller than everyone else.
UNIT_OUTLIER_FACTOR = 100
# Below this many funds there is no peer group to compare against.
MIN_FUNDS_FOR_UNIT_CHECK = 5
UNIT_CHECK_RULE = "Enhet avviker från övriga kassor"
RATIO_RULE = "Nyckeltal utanför rimligt intervall"
RATIO_FILENAME = "nyckeltalsberakningar.json"
# Reports state amounts in tkr; a figure lifted from running text is often in
# kronor. The factor between them is what tells the two mistakes apart.
KRONOR_PER_TKR = 1000


@dataclass
class CheckResult:
    """A failed check, with the size of the discrepancy when it has one.

    Checks may return a plain string instead. The difference is only worth
    carrying for a straightforward mismatch: for a sign error or a unit
    mix-up the number is an artefact of the fault, not a quantity that could
    match another finding.
    """

    message: str
    difference: float | None = None
    severity: str | None = None


@dataclass
class Finding:
    fund: str
    year: str
    rule: str
    message: str
    severity: str = SEVERITY_WARNING
    metrics: list[str] | None = None
    difference: float | None = None

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
    check: Callable[[dict[str, float]], "str | CheckResult | None"]
    severity: str = SEVERITY_WARNING


def _tolerance(magnitude: float) -> float:
    return max(ABSOLUTE_TOLERANCE, abs(magnitude) * RELATIVE_TOLERANCE)


def _fmt(amount: float) -> str:
    """Swedish thousands separator.

    Formats the number only. A blanket str.replace(",", " ") over the whole
    message also removed the commas in the prose around it.
    """
    return f"{amount:,.0f}".replace(",", "\u00a0")


def _scale_mismatch(target: str, total: float, other: str, parts: float,
                    component_count: int = 1) -> str | None:
    """One side read in kronor where the other is in thousands.

    Småföretagarnas "Finansieringsavgift" came back as 117 308 746 against a
    "Summa avgifter till staten" of 117 309: the same figure, read once off
    the resultaträkning in tkr and once out of the förvaltningsberättelse in
    kronor. Reported as a difference of 117 million it looks like a wild
    misreading; reported as a scale mismatch it points straight at the cell
    to fix.

    The tolerance is per component, because rounding each one to whole tkr
    can move the total by up to a krona times the number of terms.
    """
    slack = KRONOR_PER_TKR * max(1, component_count)
    for high_name, high, low_name, low in (
        (other, parts, target, total),
        (target, total, other, parts),
    ):
        if not low or not high:
            continue
        if abs(high - low * KRONOR_PER_TKR) <= slack:
            return (
                f"Skalfel: {high_name} är {_fmt(high)} medan {low_name} är "
                f"{_fmt(low)}, vilket är samma belopp i kronor respektive "
                "tusental kronor. Ett av värdena är hämtat i fel storhet. "
                "Kontrollera vilken sida som är rätt; beloppen ska anges i "
                "samma enhet som räkningen i övrigt."
            )
    return None


def _provisions_counted_twice(values: dict[str, float]) -> bool:
    """True when equity plus debt alone equals the balance sheet total.

    Where a report presents no separate "Summa skulder", one of the two
    figures ends up containing the provisions. Seen on the same fund in three
    consecutive years.
    """
    total = values["Summa tillgångar"]
    without = values["Summa eget kapital"] + values["Summa skulder"]
    return values["Summa avsättningar"] > 0 and abs(total - without) <= _tolerance(total)


def _balance_identity(values: dict[str, float]) -> "str | CheckResult | None":
    total = values["Summa tillgångar"]
    equity = values["Summa eget kapital"]
    debt = values["Summa skulder"]
    provisions = values["Summa avsättningar"]
    parts = equity + debt + provisions
    diff = total - parts

    if abs(diff) <= _tolerance(total):
        return None

    message = (
        f"Summa tillgångar {_fmt(total)} stämmer inte med "
        f"Summa eget kapital + Summa skulder + Summa avsättningar = {_fmt(parts)} "
        f"(differens {_fmt(diff) if diff < 0 else '+' + _fmt(diff)})."
    )

    if _provisions_counted_twice(values):
        # Both figures are equally consistent with the arithmetic, so name
        # both rather than blaming one.
        message += (
            f" Summa eget kapital + Summa skulder = {_fmt(equity + debt)} är däremot "
            f"exakt lika med balansomslutningen, vilket tyder på att avsättningarna "
            f"{_fmt(provisions)} räknats med två gånger. Antingen ingår de redan i "
            f"eget kapital, som då borde vara {_fmt(total - debt - provisions)} i "
            f"stället för {_fmt(equity)}, eller i skulderna, som då borde vara "
            f"{_fmt(total - equity - provisions)} i stället för {_fmt(debt)}. "
            "Kontrollera vilket mot balansräkningen."
        )

    return CheckResult(message, diff)


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


def _receivable_matches_provision(values: dict[str, float]) -> "str | CheckResult | None":
    """Fordringar and avsättningar for felaktig ersättning describe one thing.

    A claim for wrongly paid benefit is carried as a receivable and provided
    for at the same amount, so the two figures move together. The domain
    expert checks this by hand in the spreadsheet; as a rule it colours the
    cell and joins the findings instead.
    """
    receivable = values["Fordringar felaktig arbetslöshetsersättning"]
    provision = values["Avsättningar felaktig arbetslöshetsersättning"]
    diff = receivable - provision
    if abs(diff) <= _tolerance(receivable or provision):
        return None
    return CheckResult(
        f"Fordringar felaktig arbetslöshetsersättning {_fmt(receivable)} skiljer "
        f"sig från Avsättningar felaktig arbetslöshetsersättning "
        f"{_fmt(provision)} (differens "
        f"{_fmt(diff) if diff < 0 else '+' + _fmt(diff)}). Posterna avser samma "
        "sak och brukar vara lika stora.",
        diff,
    )


RULES: list[Rule] = [
    Rule(
        name="Fordran och avsättning för felaktig ersättning",
        description=(
            "Fordringar felaktig arbetslöshetsersättning ska motsvara "
            "Avsättningar felaktig arbetslöshetsersättning."
        ),
        metrics=["Fordringar felaktig arbetslöshetsersättning",
                 "Avsättningar felaktig arbetslöshetsersättning"],
        check=_receivable_matches_provision,
        severity=SEVERITY_WARNING,
    ),
    Rule(
        name="Balansräkningen balanserar",
        description=(
            "Summa tillgångar ska vara lika med summan av eget kapital, "
            "avsättningar och skulder."
        ),
        metrics=["Summa tillgångar", "Summa eget kapital", "Summa skulder",
                 "Summa avsättningar"],
        check=_balance_identity,
        severity=SEVERITY_ERROR,
    ),
    Rule(
        name="Balansomslutning är positiv",
        description="En balansomslutning ska vara ett positivt belopp.",
        metrics=["Summa tillgångar"],
        check=_non_negative("Summa tillgångar"),
        severity=SEVERITY_WARNING,
    ),
    Rule(
        name="Administrationskostnader anges positivt",
        description="Kostnader rapporteras som positiva belopp, inte med minustecken.",
        metrics=["Summa administrationskostnader"],
        check=_non_negative("Summa administrationskostnader"),
        severity=SEVERITY_WARNING,
    ),
]


def _sum_check(target: str, components: dict[str, float]):
    """Build a check for one subtotal from the specification."""

    def check(values: dict[str, float]) -> "str | CheckResult | None":
        total = values[target]
        parts = sum(values[name] * coefficient for name, coefficient in components.items())
        diff = total - parts
        if abs(diff) <= _tolerance(total or parts):
            return None

        terms = " ".join(
            f"{'+' if c > 0 else '-'} {name}" for name, c in components.items()
        ).lstrip("+ ")

        # A wrong unit is a different fault from a wrong figure, and looks
        # like an enormous error unless it is named for what it is.
        scale = _scale_mismatch(target, total, terms, parts, len(components))
        if scale:
            return scale

        # Föreskriften räknar upp delposterna, men kassorna får lägga till
        # egna. Summan kan därför med rätta vara större än de uppräknade
        # posterna - differensen är då kassans extra post, inte ett räknefel.
        # Alfa-kassans Summa intäkter översteg sina två termer med exakt
        # 70 691 tkr i fem körningar: avgifter från icke anslutna plus
        # ersättning från staten, två egna poster.
        #
        # Mindre än delposterna går däremot inte ihop: en summa kan inte
        # understiga poster den själv innehåller.
        if diff > 0:
            return CheckResult(
                f"{target} {_fmt(total)} är {_fmt(diff)} större än "
                f"{terms} = {_fmt(parts)}. Kassan redovisar sannolikt en eller "
                "flera egna poster utöver dem föreskriften räknar upp. "
                "Kontrollera vilka; det är ingen räknefel.",
                diff,
                severity=SEVERITY_INFO,
            )

        # A net posted without its sign is a different fault from a wrong
        # figure, and the fix is different too.
        if parts and abs(total + parts) <= _tolerance(parts):
            return (
                f"{target} har omvänt tecken: {_fmt(total)} står i dokumentet medan "
                f"{terms} ger {_fmt(parts)}. Nettoposter ska anges matematiskt "
                "riktigt, negativa när kostnaderna överstiger intäkterna."
            )

        return CheckResult(
            f"{target} {_fmt(total)} stämmer inte med {terms} = {_fmt(parts)} "
            f"(differens {_fmt(diff) if diff < 0 else '+' + _fmt(diff)}).",
            diff,
        )

    return check


def _equality_check(target: str, other: str):
    """A note's total must equal the row it explains."""

    def check(values: dict[str, float]) -> "str | CheckResult | None":
        note = values[target]
        row = values[other]
        diff = note - row
        if abs(diff) <= _tolerance(row or note):
            return None

        # Same amount, opposite signs. The definitions ask for this: the
        # statement line is normalised to a positive amount ("Anges som ett
        # positivt belopp") while the note is to be reported as printed
        # ("räkna inte om den"), and a fund that prints its cost note
        # negative satisfies both while failing this check. It was four of
        # six funds on the first subset, which is a rule reporting its own
        # instructions back rather than a finding about the documents.
        if note and row and abs(note + row) <= _tolerance(row):
            return (
                f"{target} är {_fmt(note)} medan posten den specificerar, "
                f"'{other}', är {_fmt(row)}: samma belopp med omvänt tecken. "
                "Noten redovisar posten med kostnadstecken och räkningen "
                "utan, eller tvärtom. Beloppet stämmer; kontrollera bara "
                "vilken teckenkonvention som ska gälla i utdata."
            )

        scale = _scale_mismatch(target, note, other, row)
        if scale:
            return scale

        return CheckResult(
            f"{target} är {_fmt(note)} medan posten den specificerar, "
            f"'{other}', är {_fmt(row)} "
            f"(differens {_fmt(diff) if diff < 0 else '+' + _fmt(diff)}).",
            diff,
        )

    return check


def rules_from_definitions(metrics_path) -> list[Rule]:
    """Turn every "Delposter" in the metric definitions into a check.

    The föreskrift states each subtotal explicitly, so the arithmetic is data
    rather than code: adding a metric with components adds a check, and no
    rule has to be written by hand.
    """
    try:
        definitions = json.loads(Path(metrics_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as ex:
        logger.warning(f"Kunde inte läsa nyckeltalsdefinitionerna: {ex}")
        return []

    built = []
    for entry in definitions:
        target = entry["Nyckeltal"]

        components = entry.get("Delposter")
        if components:
            built.append(
                Rule(
                    name=f"Delsummering: {target}",
                    description=entry.get("Formel", ""),
                    metrics=[target, *components],
                    check=_sum_check(target, components),
                    severity=SEVERITY_WARNING,
                )
            )

        # A note specifies one row of the statements; the two must agree.
        counterpart = entry.get("Motsvarar")
        if counterpart:
            built.append(
                Rule(
                    name=f"Not mot räkning: {counterpart}",
                    description=f"{target} ska vara lika med {counterpart}.",
                    metrics=[target, counterpart],
                    check=_equality_check(target, counterpart),
                    severity=SEVERITY_WARNING,
                )
            )
    return built


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


def validate(result: dict, metrics_path=None) -> list[Finding]:
    """Run every rule over every fund and year that has the needed metrics.

    A rule is skipped, not failed, when a metric it needs is missing: the model
    is told to omit what it cannot find, so absence is expected.
    """
    findings: list[Finding] = []
    rules = RULES + (rules_from_definitions(metrics_path) if metrics_path else [])

    for fund, years in (result or {}).items():
        if not isinstance(years, dict):
            continue
        for year, metrics in years.items():
            if not isinstance(metrics, dict):
                continue
            for rule in rules:
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
                if not problem:
                    continue
                if isinstance(problem, CheckResult):
                    message, difference = problem.message, problem.difference
                else:
                    message, difference = problem, None
                findings.append(
                    Finding(
                        fund=fund,
                        year=str(year),
                        rule=rule.name,
                        message=message,
                        severity=(
                            problem.severity
                            if isinstance(problem, CheckResult) and problem.severity
                            else rule.severity
                        ),
                        metrics=list(rule.metrics),
                        difference=difference,
                    )
                )

    findings.extend(check_unit_consistency(result, metrics_path))
    findings.extend(check_ratio_plausibility(result, metrics_path))
    link_related_findings(findings)
    return findings


def _amount_metrics(metrics_path) -> set[str]:
    """Metrics the definitions mark as an amount rather than a count."""
    try:
        with open(metrics_path, encoding="utf-8") as f:
            definitions = json.load(f)
    except (OSError, json.JSONDecodeError) as ex:
        logger.warning(f"Kunde inte läsa {metrics_path}: {ex}")
        return set()
    return {
        entry["Nyckeltal"]
        for entry in definitions
        if entry.get("Enhet") in ("belopp", "kronor")
    }


def check_unit_consistency(result: dict, metrics_path=None) -> list[Finding]:
    """Flag amounts reported in a different unit from the other funds.

    Bilaga 2 asks for amounts without saying in what unit, and the funds
    answered differently. In one corpus "Totalt belopp återkrav" ran from 122
    to 5 501 628 across 21 funds, with several plainly in tkr and several
    plainly in kronor, including two carrying öre. The column could not be
    summed or compared and nothing noticed, because these are among the
    metrics no arithmetic identity touches.

    The comparison is against the median of the other funds, and only for
    metrics the definitions mark as amounts. Member counts vary by two orders
    of magnitude between the largest and smallest fund quite legitimately; a
    plain outlier test over those would cry wolf every run.

    Clear cases only. A fund whose figure is a fifth of its peers' is not
    resolved by this check and is not meant to be.
    """
    if not metrics_path:
        return []
    amount_metrics = _amount_metrics(metrics_path)
    if not amount_metrics:
        return []

    # metric -> year -> fund -> value
    columns: dict[str, dict[str, dict[str, float]]] = {}
    for fund, years in (result or {}).items():
        if not isinstance(years, dict):
            continue
        for year, metrics in years.items():
            if not isinstance(metrics, dict):
                continue
            for name in amount_metrics:
                value = _numeric(metrics.get(name))
                if value is None or value == 0:
                    # Zero carries no unit, so it says nothing either way.
                    continue
                columns.setdefault(name, {}).setdefault(str(year), {})[fund] = value

    findings: list[Finding] = []
    for name, per_year in columns.items():
        for year, per_fund in per_year.items():
            if len(per_fund) < MIN_FUNDS_FOR_UNIT_CHECK:
                continue
            for fund, value in sorted(per_fund.items()):
                peers = [other for peer, other in per_fund.items() if peer != fund]
                # Samma referens som rättaren använder. Räknade de två var för
                # sig blev de oense om samma kolumn: en kassa kunde lämnas
                # orörd av normaliseringen och ändå anmärkas av kontrollen.
                median = normalisation._reference_value(peers)
                if not median:
                    continue
                ratio = abs(value) / median
                if ratio > 1 / UNIT_OUTLIER_FACTOR and ratio < UNIT_OUTLIER_FACTOR:
                    continue
                direction = "lägre" if ratio < 1 else "högre"
                findings.append(
                    Finding(
                        fund=fund,
                        year=year,
                        rule=UNIT_CHECK_RULE,
                        message=(
                            f"{name} är {_fmt(value)}, vilket är omkring "
                            f"{_fmt(1 / ratio if ratio < 1 else ratio)} gånger "
                            f"{direction} än medianen {_fmt(median)} för övriga "
                            f"{len(peers)} kassor. Bilaga 2 anger inte enhet och "
                            "kassorna rapporterar olika: beloppet är sannolikt i "
                            "kronor där de andra angett tusental kronor, eller "
                            "tvärtom. Kolumnen kan inte summeras förrän detta är "
                            "avgjort."
                        ),
                        severity=SEVERITY_WARNING,
                        metrics=[name],
                    )
                )
    return findings


def link_related_findings(findings: list[Finding]) -> int:
    """Point out findings that are one misreading seen from two sides.

    Service och kommunikation reported Summa skulder exceeding its components
    by exactly 10 000, and separately a note total of 11 781 against a row of
    1 781. One dropped digit, two findings, and nothing saying so. Where two
    checks on the same fund disagree by the same amount and share exactly one
    metric, that metric is almost certainly the cell to fix, and the message
    says which.

    Small differences are left alone. A handful of funds are out by two or
    fifty on unrelated subtotals, and pairing those on an equal number would
    invent a connection that is not there.
    """
    linked = 0
    grouped: dict[tuple, list[Finding]] = {}
    for finding in findings:
        if finding.difference is None:
            continue
        if abs(finding.difference) < LINK_MIN_DIFFERENCE:
            continue
        key = (finding.fund, finding.year, round(abs(finding.difference), 2))
        grouped.setdefault(key, []).append(finding)

    for group in grouped.values():
        if not 2 <= len(group) <= LINK_MAX_GROUP:
            # One finding is nothing to link; a crowd sharing a round number
            # is a coincidence rather than a common cause.
            continue
        for finding in group:
            others = [f for f in group if f is not finding]
            shared = set(finding.metrics or [])
            for other in others:
                shared &= set(other.metrics or [])
            names = ", ".join(f"'{f.rule}'" for f in others)
            finding.message += (
                f" Samma differens uppträder i {names}, vilket tyder på en enda "
                "felläsning snarare än flera."
            )
            if len(shared) == 1:
                finding.message += (
                    f" Kontrollerna har '{next(iter(shared))}' gemensamt; "
                    "kontrollera den posten först."
                )
            linked += 1

    return linked


def log_findings(findings: list[Finding]) -> None:
    if not findings:
        logger.info("Rimlighetskontrollerna av nyckeltalen gav inga anmärkningar.")
        return

    errors = [f for f in findings if f.severity == SEVERITY_ERROR]
    notes = [f for f in findings if f.severity == SEVERITY_INFO]
    logger.warning(
        f"Rimlighetskontrollerna gav {len(findings)} anmärkning(ar), varav "
        f"{len(errors)} allvarliga och {len(notes)} upplysningar som inte är "
        "fel. Kontrollera övriga mot källdokumentet."
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


def _ratio_definitions(metrics_path, ratio_path=None) -> list[dict]:
    path = Path(ratio_path) if ratio_path else (
        Path(metrics_path).parent / RATIO_FILENAME
    )
    if not path.is_file():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as ex:
        logger.warning(f"Kunde inte läsa {path}: {ex}")
        return []


def check_ratio_plausibility(result: dict, metrics_path=None, ratio_path=None
                             ) -> list[Finding]:
    """Flag a derived ratio that cannot be right.

    Errors are easier to see in their consequences than in themselves.
    Småföretagarnas Finansieringsavgift came back in kronor where the rest of
    the income statement was in thousands, and the figure in isolation looked
    like a perfectly ordinary 117 308 746. Divided into the equity it gave a
    cost coverage of 0.02 months - a fund with half a day of reserves - which
    no reader could mistake for a real number.

    The intervals are deliberately wide. Across 24 funds cost coverage ran
    from 0.02 to 8.5 months and liquidity from 0.28 to 10.3, so these bands
    catch impossibilities, not outliers; a fund that is merely unusual must
    pass. Ratios are only computed where every input is present, since a ratio
    built from gaps would produce findings about the gaps.
    """
    if not metrics_path:
        return []
    definitions = _ratio_definitions(metrics_path, ratio_path)
    if not definitions:
        return []

    findings: list[Finding] = []
    for fund, years in (result or {}).items():
        if not isinstance(years, dict):
            continue
        for year, metrics in years.items():
            if not isinstance(metrics, dict):
                continue
            for definition in definitions:
                band = definition.get("Rimligt intervall")
                formula = definition.get("Formel", "")
                if not band or not formula:
                    continue
                names = re.findall(r"\{([^}]+)\}", formula)
                values = {name: _numeric(metrics.get(name)) for name in names}
                if any(value is None for value in values.values()):
                    continue

                expression = formula
                for name in sorted(set(names), key=len, reverse=True):
                    expression = expression.replace(f"{{{name}}}", repr(values[name]))
                # The formula comes from a configuration file rather than
                # from a document, but it is still evaluated, so the
                # substituted expression is checked to be arithmetic and
                # nothing else before it runs.
                if not re.fullmatch(r"[\d\s()+\-*/.eE]+", expression):
                    logger.warning(
                        f"Hoppar över {definition['Nyckeltal']}: formeln "
                        "innehåller annat än aritmetik."
                    )
                    continue
                try:
                    ratio = eval(expression, {"__builtins__": {}}, {})  # noqa: S307
                except (ZeroDivisionError, ArithmeticError, SyntaxError, TypeError):
                    continue

                low, high = band
                if low <= ratio <= high:
                    continue
                findings.append(
                    Finding(
                        fund=fund,
                        year=str(year),
                        rule=RATIO_RULE,
                        message=(
                            f"{definition['Nyckeltal']} blir {ratio:,.2f}, utanför "
                            f"det rimliga intervallet {low}–{high}. Beräkningen "
                            f"bygger på {', '.join(sorted(set(names)))}; något av "
                            "de värdena är sannolikt felläst eller angivet i fel "
                            "enhet."
                        ),
                        severity=SEVERITY_WARNING,
                        metrics=sorted(set(names)),
                    )
                )
    return findings
