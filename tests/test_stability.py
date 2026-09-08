"""Tests for the repeat-extraction stability check.

Across three runs of an unchanged, poorly scanned document, seven of eighteen
values moved — including the same figure being read under two different
headings. The check turns "the number moved between runs" into "the tool
flagged a disagreement", which is the difference between an unreliable figure
and a known-uncertain one.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.src import JBGValidation as validation  # noqa: E402
from app.src.JBGAnnualReportAnalysis import JBGAnnualReportAnalyzer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _analyzer(second_reading):
    a = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)
    a.metrics_path = ROOT / "app" / "prompt" / "json" / "nyckeltalsdefinitioner.json"
    a.fund_list_path = ROOT / "app" / "src" / "json" / "kassor.json"
    a._schema_cache = None
    a.stability_findings = []
    a._analyse_chunks = lambda chunks, the_year, model, purpose=None: second_reading
    return a


def _result(**metrics):
    return {
        "Livsmedelsarbetarnas arbetslöshetskassa": {
            "2025": {
                name: {"värde": value, "källa": "Sida 2", "säkerhet": "explicit",
                       "kommentar": "c"}
                for name, value in metrics.items()
            }
        }
    }


# ------------------------------------------------------------- comparison
def test_identical_readings_produce_no_findings():
    first = _result(Balansomslutning=63853, **{"Eget kapital": 16339})
    a = _analyzer([_result(Balansomslutning=63853, **{"Eget kapital": 16339})])
    assert a._check_extraction_stability(first, ["t"], 2025, "m", "x.pdf") == []


def test_a_changed_value_is_flagged():
    first = _result(Balansomslutning=63853)
    a = _analyzer([_result(Balansomslutning=16026)])
    findings = a._check_extraction_stability(first, ["t"], 2025, "m", "x.pdf")

    assert len(findings) == 1
    assert findings[0].rule == JBGAnnualReportAnalyzer.STABILITY_RULE
    assert findings[0].metrics == ["Balansomslutning"]
    assert "63853" in findings[0].message and "16026" in findings[0].message
    assert findings[0].severity == validation.SEVERITY_WARNING


def test_the_first_reading_is_kept():
    """A disagreement says the figure is uncertain, not that the second
    attempt is better."""
    first = _result(Balansomslutning=63853)
    a = _analyzer([_result(Balansomslutning=16026)])
    a._check_extraction_stability(first, ["t"], 2025, "m", "x.pdf")
    fund = "Livsmedelsarbetarnas arbetslöshetskassa"
    assert first[fund]["2025"]["Balansomslutning"]["värde"] == 63853


def test_a_value_missing_on_the_second_reading_is_flagged():
    first = _result(Balansomslutning=63853)
    a = _analyzer([_result()])
    findings = a._check_extraction_stability(first, ["t"], 2025, "m", "x.pdf")
    assert len(findings) == 1
    assert "inget värde alls" in findings[0].message


def test_only_the_unstable_metric_is_flagged():
    first = _result(Balansomslutning=63853, Skulder=42401)
    a = _analyzer([_result(Balansomslutning=16026, Skulder=42401)])
    findings = a._check_extraction_stability(first, ["t"], 2025, "m", "x.pdf")
    assert [f.metrics[0] for f in findings] == ["Balansomslutning"]


def test_a_failed_repeat_reports_nothing_rather_than_everything():
    """If the second call fails, every value would look unstable. That would
    be noise, not information."""
    first = _result(Balansomslutning=63853, Skulder=42401)
    a = _analyzer([])
    assert a._check_extraction_stability(first, ["t"], 2025, "m", "x.pdf") == []


@pytest.mark.parametrize(
    "first_value, second_value",
    [(63853, 63853.0), (63853, "63853"), (63853, "63 853"), (63853, "63853,00")],
)
def test_formatting_differences_are_not_instability(first_value, second_value):
    first = _result(Balansomslutning=first_value)
    a = _analyzer([_result(Balansomslutning=second_value)])
    assert a._check_extraction_stability(first, ["t"], 2025, "m", "x.pdf") == []


# --------------------------------------------------------------- plumbing
def test_findings_follow_the_fund_rename():
    """Findings are raised per file, before fund names are canonicalised. If
    the key does not follow, the Excel export cannot colour the cell."""
    a = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)
    a.fund_list_path = ROOT / "app" / "src" / "json" / "kassor.json"
    a.stability_findings = [
        validation.Finding(fund="Livs", year="2025", rule="Instabil avläsning",
                           message="m", metrics=["Balansomslutning"])
    ]
    a._canonicalise_finding_funds()
    assert a.stability_findings[0].fund == "Livsmedelsarbetarnas arbetslöshetskassa"


def test_the_check_is_limited_to_ocred_documents_by_default():
    """Every unstable result observed so far was on an OCR-ed file, and the
    check doubles the extraction cost for whatever it covers."""
    assert JBGAnnualReportAnalyzer.VERIFY_OCR_EXTRACTION is True
    assert JBGAnnualReportAnalyzer.VERIFY_ALL_EXTRACTIONS is False


def test_repeat_calls_are_attributed_separately():
    """Otherwise the cost of the check hides inside the extraction total."""
    from app.src import JBGUsage as usage

    assert usage.PURPOSE_STABILITY != usage.PURPOSE_EXTRACTION
    seen = {}

    a = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)
    a.MAX_CONCURRENT_CHUNKS = 1
    a._analyse_chunk = lambda i, n, c, y, m, purpose=None: seen.setdefault("p", purpose)
    a._analyse_chunks(["one"], the_year=2025, model="m", purpose=usage.PURPOSE_STABILITY)
    assert seen["p"] == usage.PURPOSE_STABILITY


def test_serialised_findings_reach_the_result_file():
    finding = validation.Finding(
        fund="K", year="2025", rule=JBGAnnualReportAnalyzer.STABILITY_RULE,
        message="Två avläsningar gav olika värden", metrics=["Balansomslutning"],
    )
    payload = finding.as_dict()
    json.dumps(payload, ensure_ascii=False)
    assert payload["kontroll"] == "Instabil avläsning"
    assert payload["berörda_nyckeltal"] == ["Balansomslutning"]
