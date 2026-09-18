"""Tests for the two-sheet export and the derived ratios.

The source column doubles the width of the sheet and is only wanted when
tracing a figure back to its page, so the figures and the source references
get a sheet each. The ratios were worked out by hand in the spreadsheet by
the person who uses it; these keep them correct when a row moves.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

openpyxl = pytest.importorskip("openpyxl")

from app.src.JBGJSONConverter import JsonConverter  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KEY_DEFS = ROOT / "app" / "prompt" / "json" / "nyckeltalsdefinitioner.json"
KASSOR = ROOT / "app" / "src" / "json" / "kassor.json"
RATIOS = ROOT / "app" / "prompt" / "json" / "nyckeltalsberakningar.json"

RATIO_INPUTS = {
    "Summa omsättningstillgångar": 60000,
    "Fordringar statligt bidrag till arbetslöshetsersättning": 5000,
    "Fordringar felaktig arbetslöshetsersättning": 1000,
    "Not till Övriga fordringar: Källskatt arbetslöshetsersättning": 500,
    "Summa skulder": 40000,
    "Skulder arbetslöshetsersättning": 9000,
    "Not till Övriga skulder: Källskatt arbetslöshetsersättning": 800,
    "Summa eget kapital": 16339,
    "Summa tillgångar": 63853,
    "Summa administrationskostnader": 24000,
    "Finansieringsavgift": 30000,
    "Finansiella kostnader": 120,
    "Totalt antal medlemmar 31 december": 250000,
    "Antal medlemmar som fått ersättning": 12000,
}


def _export(tmp_path, funds, include_sources=True):
    data = {
        fund: {"2025": {
            name: {"värde": value, "källa": "Sida 7", "säkerhet": "explicit",
                   "kommentar": ""}
            for name, value in metrics.items()
        }}
        for fund, metrics in funds.items()
    }
    source = tmp_path / "resultat.json"
    source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out.xlsx"
    JsonConverter(source, include_sources=include_sources).to_excel_by_year(
        out, key_def_path=KEY_DEFS, fund_names=KASSOR, findings=[]
    )
    return openpyxl.load_workbook(out)


def _row_of(ws, label):
    for row in range(1, ws.max_row + 1):
        if ws.cell(row, 1).value == label:
            return row
    raise AssertionError(f"hittade inte raden {label!r}")


# ----------------------------------------------------------------- sheets
def test_the_figures_and_the_sources_get_a_sheet_each(tmp_path):
    wb = _export(tmp_path, {"Livsmedelsarbetarnas arbetslöshetskassa": RATIO_INPUTS})

    assert wb.sheetnames == ["2025", "2025 med källa", "Läsanvisning"]
    assert [wb["2025"].cell(1, c).value for c in (1, 2)] == [
        "Nyckeltal", "Livsmedelsarbetarnas"
    ]
    assert wb["2025 med källa"].cell(1, 3).value == "källa"


def test_the_clean_sheet_has_no_source_columns(tmp_path):
    wb = _export(tmp_path, {"Livsmedelsarbetarnas arbetslöshetskassa": RATIO_INPUTS})
    headers = [c.value for c in wb["2025"][1]]

    assert "källa" not in headers


def test_without_sources_there_is_only_one_sheet_per_year(tmp_path):
    wb = _export(
        tmp_path, {"Livsmedelsarbetarnas arbetslöshetskassa": RATIO_INPUTS},
        include_sources=False,
    )
    assert wb.sheetnames == ["2025", "Läsanvisning"]


# ----------------------------------------------------------------- ratios
def test_the_ratios_are_written_to_the_clean_sheet_only(tmp_path):
    wb = _export(tmp_path, {"Livsmedelsarbetarnas arbetslöshetskassa": RATIO_INPUTS})

    assert _row_of(wb["2025"], "Soliditet")
    with pytest.raises(AssertionError):
        _row_of(wb["2025 med källa"], "Soliditet")


def test_a_ratio_points_at_the_rows_it_divides(tmp_path):
    """Written as a formula so the derivation stays visible and a corrected
    input updates the ratio without a new run."""
    wb = _export(tmp_path, {"Livsmedelsarbetarnas arbetslöshetskassa": RATIO_INPUTS})
    ws = wb["2025"]

    equity = _row_of(ws, "Summa eget kapital")
    members = _row_of(ws, "Totalt antal medlemmar 31 december")
    admin = _row_of(ws, "Summa administrationskostnader")

    assert ws.cell(_row_of(ws, "Soliditet"), 2).value.startswith("=IFERROR(")
    assert f"B{equity}" in ws.cell(_row_of(ws, "Soliditet"), 2).value
    per_member = ws.cell(_row_of(ws, "Administrationskostnad per medlem (kr)"), 2).value
    assert f"B{admin} / B{members} * 1000" in per_member


def test_every_ratio_reaches_a_fund_with_all_its_inputs(tmp_path):
    wb = _export(tmp_path, {"Livsmedelsarbetarnas arbetslöshetskassa": RATIO_INPUTS})
    ws = wb["2025"]

    for ratio in json.loads(RATIOS.read_text(encoding="utf-8")):
        cell = ws.cell(_row_of(ws, ratio["Nyckeltal"]), 2)
        assert cell.value, f"{ratio['Nyckeltal']} beräknades inte"


def test_a_fund_missing_an_input_gets_no_ratio_and_a_reason(tmp_path):
    """A zero computed from gaps looks like a measurement."""
    wb = _export(tmp_path, {
        "Livsmedelsarbetarnas arbetslöshetskassa": RATIO_INPUTS,
        "GS arbetslöshetskassa": {"Summa eget kapital": 100},
    })
    ws = wb["2025"]
    gs_column = [c.value for c in ws[1]].index("GS") + 1
    cell = ws.cell(_row_of(ws, "Soliditet"), gs_column)

    assert cell.value is None
    assert "värde saknas" in cell.comment.text


def test_the_ratio_definitions_only_reference_metrics_that_exist():
    """A typo in a metric name would silently produce an empty column."""
    import re

    known = {e["Nyckeltal"] for e in json.loads(KEY_DEFS.read_text(encoding="utf-8"))}
    for ratio in json.loads(RATIOS.read_text(encoding="utf-8")):
        for name in re.findall(r"\{([^}]+)\}", ratio["Formel"]):
            assert name in known, f"{ratio['Nyckeltal']} hänvisar till okänt {name!r}"


def test_an_export_without_a_ratio_file_still_works(tmp_path):
    """The ratios are an addition, not a prerequisite."""
    data = {"Livsmedelsarbetarnas arbetslöshetskassa": {"2025": {
        "Summa tillgångar": {"värde": 1, "källa": "s", "säkerhet": "explicit",
                             "kommentar": ""}}}}
    source = tmp_path / "r.json"
    source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "o.xlsx"

    JsonConverter(source, include_sources=True).to_excel_by_year(
        out, key_def_path=KEY_DEFS, fund_names=KASSOR, findings=[],
        ratio_def_path=tmp_path / "finns-inte.json",
    )

    wb = openpyxl.load_workbook(out)
    assert "2025" in wb.sheetnames
    with pytest.raises(AssertionError):
        _row_of(wb["2025"], "Soliditet")
