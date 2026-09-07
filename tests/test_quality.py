"""Tests for Step 4: canonical fund names, arithmetic validation, and the
certainty and comment fields reaching the exports."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.src import JBGValidation as validation  # noqa: E402
from app.src.JBGFundNames import (  # noqa: E402
    FundNameResolver,
    normalise_result_fund_names,
)

ROOT = Path(__file__).resolve().parents[1]
KASSOR = ROOT / "app" / "src" / "json" / "kassor.json"
KEY_DEFS = ROOT / "app" / "prompt" / "json" / "nyckeltalsdefinitioner.json"


@pytest.fixture(scope="module")
def resolver():
    return FundNameResolver(KASSOR)


# ------------------------------------------------------------- fund names
@pytest.mark.parametrize(
    "reported, expected_short",
    [
        # Every one of these came out of a real run and none of them matched
        # the old exact-dict lookup.
        ("Byggnads a-kassa", "Byggnadsarbetarnas"),
        ("Lärarnas a-kassa", "Lärarnas"),
        ("Journalisternas Arbetslöshetskassa", "Journalisternas"),
        ("Finans- och Försäkringsbranschens Arbetslöshetskassa", "Finans- och Försäkringsbranschens"),
        ("Livsmedelsarbetarnas", "Livsmedelsarbetarnas"),
        ("Fastighets", "Fastighets"),
        ("Kommunalarbetarnas", "Kommunalarbetarnas"),
        # other spellings the cover pages use
        ("Arbetslöshetskassan Handels", "Handels"),
        ("Handels a-kassa", "Handels"),
        ("Unionens Arbetslöshetskassa", "Unionens"),
        ("IF Metalls a-kassa", "IF Metalls"),
        ("Akademikernas erkända arbetslöshetskassa", "Akademikernas"),
        ("Arbetslöshetskassan för service och kommunikation", "Sekos"),
    ],
)
def test_real_reported_names_resolve(resolver, reported, expected_short):
    assert resolver.short_name(reported) == expected_short


def test_case_difference_alone_used_to_break_the_lookup(resolver):
    a = resolver.canonical_name("Journalisternas Arbetslöshetskassa")
    b = resolver.canonical_name("Journalisternas arbetslöshetskassa")
    assert a == b == "Journalisternas arbetslöshetskassa"


@pytest.mark.parametrize("unknown", ["Något Okänt a-kassa", "Fiktiva yrkens a-kassa", ""])
def test_unknown_names_are_left_alone_not_guessed(resolver, unknown):
    """A wrong canonical name is worse than an unnormalised one."""
    entry, how = resolver.resolve(unknown)
    assert entry is None, how
    assert resolver.canonical_name(unknown) == unknown


def test_every_official_and_short_name_resolves_to_itself(resolver):
    entries = json.loads(KASSOR.read_text(encoding="utf-8"))
    for entry in entries:
        for spelling in (entry["Officiellt namn"], entry["Kort namn"]):
            assert resolver.canonical_name(spelling) == entry["Officiellt namn"], spelling


def test_two_spellings_of_one_fund_are_merged():
    result = {
        "Journalisternas Arbetslöshetskassa": {"2023": {"Eget kapital": {"värde": 1}}},
        "Journalisternas arbetslöshetskassa": {"2024": {"Eget kapital": {"värde": 2}}},
    }
    merged, unresolved = normalise_result_fund_names(result, KASSOR)
    assert unresolved == []
    assert list(merged) == ["Journalisternas arbetslöshetskassa"]
    assert set(merged["Journalisternas arbetslöshetskassa"]) == {"2023", "2024"}


def test_unresolved_names_are_reported_back():
    result = {"Helt Okänd kassa": {"2023": {}}}
    merged, unresolved = normalise_result_fund_names(result, KASSOR)
    assert unresolved == ["Helt Okänd kassa"]
    assert "Helt Okänd kassa" in merged


def test_missing_fund_register_is_not_fatal(tmp_path):
    result = {"X": {"2023": {}}}
    merged, unresolved = normalise_result_fund_names(result, tmp_path / "nope.json")
    assert merged == result


# ------------------------------------------------------------- validation
def _fund(**metrics):
    return {"K": {"2023": {k: {"värde": v} for k, v in metrics.items()}}}


def test_balanced_balance_sheet_produces_no_findings():
    result = _fund(
        **{"Balansomslutning": 63853, "Eget kapital": 16339, "Skulder": 42401,
           "Utgående avsättningar": 5113}
    )
    assert validation.validate(result) == []


def test_the_real_discrepancy_is_caught():
    """Finans- och Försäkringsbranschens 2024, reproduced in two runs."""
    result = _fund(
        **{"Balansomslutning": 45776, "Eget kapital": 20000, "Skulder": 25776,
           "Utgående avsättningar": 2867}
    )
    findings = validation.validate(result)
    assert len(findings) == 1
    assert findings[0].severity == validation.SEVERITY_ERROR
    assert "-2 867" in findings[0].message or "+2 867" in findings[0].message


def test_rounding_in_tkr_does_not_trip_the_check():
    result = _fund(
        **{"Balansomslutning": 730143, "Eget kapital": 300000, "Skulder": 430142,
           "Utgående avsättningar": 0}
    )
    assert validation.validate(result) == []


def test_missing_metrics_skip_the_rule_rather_than_failing_it():
    """The model is told to omit what it cannot find, so absence is normal."""
    result = _fund(**{"Balansomslutning": 63853, "Eget kapital": 16339})
    assert validation.validate(result) == []


def test_impossible_subtotals_are_caught():
    result = _fund(**{"Omsättningstillgångar": 90000, "Balansomslutning": 63853})
    assert any("Omsättningstillgångar" in f.message for f in validation.validate(result))

    result = _fund(**{"Kassa och bank": 50000, "Omsättningstillgångar": 38976})
    assert any("Kassa och bank" in f.message for f in validation.validate(result))


def test_negative_cost_is_flagged_since_the_prompt_asks_for_positives():
    result = _fund(**{"Administrationskostnader": -58493})
    findings = validation.validate(result)
    assert len(findings) == 1
    assert findings[0].severity == validation.SEVERITY_WARNING


def test_string_values_are_still_checked():
    result = {"K": {"2023": {
        "Balansomslutning": {"värde": "63 853"},
        "Eget kapital": {"värde": "16339"},
        "Skulder": {"värde": "42401"},
        "Utgående avsättningar": {"värde": "5113"},
    }}}
    assert validation.validate(result) == []


def test_malformed_result_does_not_raise():
    for junk in [None, {}, {"K": None}, {"K": {"2023": None}}, {"K": {"2023": {"M": 5}}}]:
        assert validation.validate(junk) == []


def test_findings_index_by_cell():
    result = _fund(**{"Balansomslutning": 100, "Eget kapital": 1, "Skulder": 1,
                      "Utgående avsättningar": 1})
    index = validation.findings_by_cell(validation.validate(result))
    assert ("K", "2023", "Balansomslutning") in index
    assert ("K", "2023", "Skulder") in index


# ----------------------------------------------------------------- export
@pytest.fixture
def sample_json(tmp_path):
    data = {
        "Journalisternas arbetslöshetskassa": {
            "2024": {
                "Balansomslutning": {
                    "värde": 15959, "källa": "Sida 10, Balansräkning",
                    "säkerhet": 1.0, "kommentar": "Summa tillgångar.",
                },
                "Eget kapital": {
                    "värde": 9000, "källa": "Sida 10",
                    "säkerhet": 0.45, "kommentar": "Osäker tolkning.",
                },
            }
        }
    }
    path = tmp_path / "r.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_csv_now_carries_certainty_and_comment(sample_json, tmp_path):
    from app.src.JBGJSONConverter import JsonConverter

    out = tmp_path / "out.csv"
    JsonConverter(sample_json, include_sources=True).to_csv(out)
    header = out.read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.split(";") == ["Fund", "Year", "Key", "Value", "Source", "Certainty", "Comment"]


def test_csv_without_sources_stays_minimal(sample_json, tmp_path):
    from app.src.JBGJSONConverter import JsonConverter

    out = tmp_path / "out.csv"
    JsonConverter(sample_json, include_sources=False).to_csv(out)
    header = out.read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.split(";") == ["Fund", "Year", "Key", "Value"]


def test_excel_shades_by_certainty_and_attaches_notes(sample_json, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from app.src.JBGJSONConverter import JsonConverter

    out = tmp_path / "out.xlsx"
    JsonConverter(sample_json, include_sources=True).to_excel_by_year(
        out, key_def_path=KEY_DEFS, fund_names=KASSOR, findings=[]
    )

    wb = openpyxl.load_workbook(out)
    assert "Läsanvisning" in wb.sheetnames
    ws = wb["2024"]
    # short name in the header, resolved through the fund register
    assert ws.cell(row=1, column=2).value == "Journalisternas"

    cells = {r[0].value: r for r in ws.iter_rows(min_row=2)}
    high = cells["Balansomslutning"][1]
    low = cells["Eget kapital"][1]
    assert high.fill.start_color.rgb.endswith("C6EFCE")   # high certainty, green
    assert low.fill.start_color.rgb.endswith("FFC7CE")    # low certainty, red
    assert "Summa tillgångar" in high.comment.text
    assert "Säkerhet: 1.0" in high.comment.text


def test_excel_marks_cells_named_in_a_finding(sample_json, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from app.src.JBGJSONConverter import JsonConverter

    finding = validation.Finding(
        fund="Journalisternas arbetslöshetskassa",
        year="2024",
        rule="Balansräkningen balanserar",
        message="Testanmärkning",
        severity=validation.SEVERITY_ERROR,
        metrics=["Balansomslutning"],
    )
    out = tmp_path / "out.xlsx"
    JsonConverter(sample_json, include_sources=True).to_excel_by_year(
        out, key_def_path=KEY_DEFS, fund_names=KASSOR, findings=[finding]
    )

    wb = openpyxl.load_workbook(out)
    ws = wb["2024"]
    cells = {r[0].value: r for r in ws.iter_rows(min_row=2)}
    flagged = cells["Balansomslutning"][1]
    # the flag colour wins over the certainty colour
    assert flagged.fill.start_color.rgb.endswith("E1BEE7")
    assert "Testanmärkning" in flagged.comment.text
    assert "Testanmärkning" in "".join(
        str(c) for row in wb["Läsanvisning"].iter_rows(values_only=True) for c in row if c
    )


# ------------------------------------------------------- packaging: no pandas
def test_csv_export_needs_no_pandas(sample_json, tmp_path, monkeypatch):
    """pandas was a hard dependency of the whole package purely so that to_csv
    could write a semicolon-separated file. It is optional now."""
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "pandas" or name.startswith("pandas."):
            raise ImportError("No module named 'pandas'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)

    from app.src.JBGJSONConverter import JsonConverter

    out = tmp_path / "out.csv"
    JsonConverter(sample_json, include_sources=True).to_csv(out)
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].split(";") == ["Fund", "Year", "Key", "Value", "Source", "Certainty", "Comment"]
    assert len(lines) == 3  # header plus two metrics


def test_csv_values_are_not_mangled(sample_json, tmp_path):
    from app.src.JBGJSONConverter import JsonConverter

    out = tmp_path / "out.csv"
    JsonConverter(sample_json, include_sources=True).to_csv(out)
    rows = out.read_text(encoding="utf-8-sig").splitlines()[1:]
    values = {r.split(";")[2]: r.split(";")[3] for r in rows}
    assert values["Balansomslutning"] == "15959"
    assert values["Eget kapital"] == "9000"


def test_dataframe_still_works_when_pandas_is_present(sample_json):
    pytest.importorskip("pandas")
    from app.src.JBGJSONConverter import JsonConverter

    df = JsonConverter(sample_json, include_sources=True).to_dataframe()
    assert list(df.columns) == ["Fund", "Year", "Key", "Value", "Source", "Certainty", "Comment"]
    assert len(df) == 2
