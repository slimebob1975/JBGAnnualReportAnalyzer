"""Tests for the three-level certainty scale.

Asked for a number between 0 and 1, the model answered exactly 1.0 for 106 of
121 values in a real run. A small enum forces a commitment to a described
situation instead of a number that can always be rounded up.

Result files written before the change contain floats, so everything that
reads a certainty has to keep working on both.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.src import JBGMetricSchema as schema  # noqa: E402
from app.src.JBGAnnualReportAnalysis import JBGAnnualReportAnalyzer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KASSOR = ROOT / "app" / "src" / "json" / "kassor.json"
KEY_DEFS = ROOT / "app" / "prompt" / "json" / "nyckeltalsdefinitioner.json"


# ------------------------------------------------------------------ schema
def test_certainty_is_a_closed_enum_of_three_levels():
    built = schema.build_schema(["Skulder"])
    field = built["schema"]["properties"]["nyckeltal"]["items"]["properties"][
        schema.FIELD_CERTAINTY
    ]
    assert field["type"] == "string"
    assert field["enum"] == ["explicit", "härledd", "osäker"]
    # a number is no longer expressible, so 1.0 cannot be returned
    assert "minimum" not in field


def test_each_level_is_described_in_the_schema():
    field = schema.build_schema(["Skulder"])["schema"]["properties"]["nyckeltal"][
        "items"
    ]["properties"][schema.FIELD_CERTAINTY]
    for level in schema.CERTAINTY_LEVELS:
        assert level in field["description"]


def test_prompt_addendum_states_the_three_values():
    text = schema.describe_for_prompt(["Skulder"])
    assert "explicit" in text and "härledd" in text and "osäker" in text
    assert "inte en siffra" in text


@pytest.mark.parametrize(
    "value, level",
    [
        ("explicit", "explicit"),
        ("Härledd", "härledd"),          # case and whitespace tolerant
        ("  osäker ", "osäker"),
        (1.0, "explicit"),                # legacy floats
        (0.95, "explicit"),
        (0.7, "härledd"),
        (0.5, "härledd"),
        (0.45, "osäker"),
        (0.0, "osäker"),
        (None, ""),
        ("nonsense", ""),
        (True, ""),                       # bool is not a certainty
    ],
)
def test_certainty_level_mapping(value, level):
    assert schema.certainty_level(value) == level


def test_ranks_are_ordered():
    assert (
        schema.certainty_rank("explicit")
        > schema.certainty_rank("härledd")
        > schema.certainty_rank("osäker")
        > schema.certainty_rank(None)
    )


# ------------------------------------------------------- conflict ranking
def test_conflicts_prefer_the_more_explicit_value():
    analyzer = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)
    data = {
        "K": {"2023": {"Skulder": [
            {"värde": 42401, "källa": "Sida 14", "säkerhet": "explicit",
             "kommentar": "Summa skulder."},
            {"värde": 7147, "källa": "Sida 3, Sida 9", "säkerhet": "osäker",
             "kommentar": "Gissning."},
        ]}}
    }
    merged, _ = analyzer._merge_conflicted_values_json_objects(data)
    entry = merged["K"]["2023"]["Skulder"]
    # explicit wins despite the alternative citing two pages
    assert entry["värde"] == 42401
    assert entry["säkerhet"] == "explicit"


def test_conflict_ranking_still_works_with_legacy_floats():
    analyzer = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)
    data = {
        "K": {"2023": {"Osäkra fordringar": [
            {"värde": 1965, "källa": "Not 12", "säkerhet": 0.9, "kommentar": "a"},
            {"värde": 477, "källa": "Sida 3, Sida 9", "säkerhet": 0.7, "kommentar": "b"},
        ]}}
    }
    merged, _ = analyzer._merge_conflicted_values_json_objects(data)
    assert merged["K"]["2023"]["Osäkra fordringar"]["värde"] == 1965


def test_mixed_levels_and_floats_compare_sensibly():
    analyzer = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)
    data = {
        "K": {"2023": {"Skulder": [
            {"värde": 100, "källa": "s", "säkerhet": "osäker", "kommentar": "a"},
            {"värde": 200, "källa": "s", "säkerhet": 0.95, "kommentar": "b"},
        ]}}
    }
    merged, _ = analyzer._merge_conflicted_values_json_objects(data)
    assert merged["K"]["2023"]["Skulder"]["värde"] == 200


# --------------------------------------------------------- Excel shading
def _write(tmp_path, certainties):
    data = {"Journalisternas arbetslöshetskassa": {"2024": {
        name: {"värde": 100 + i, "källa": "Sida 10",
               "säkerhet": certainty, "kommentar": "c"}
        for i, (name, certainty) in enumerate(certainties.items())
    }}}
    path = tmp_path / "r.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_excel_shades_by_level(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from app.src.JBGJSONConverter import JsonConverter

    path = _write(tmp_path, {
        "Balansomslutning": "explicit",
        "Eget kapital": "härledd",
        "Skulder": "osäker",
    })
    out = tmp_path / "out.xlsx"
    JsonConverter(path, include_sources=True).to_excel_by_year(
        out, key_def_path=KEY_DEFS, fund_names=KASSOR, findings=[]
    )
    ws = openpyxl.load_workbook(out)["2024"]
    cells = {r[0].value: r[1] for r in ws.iter_rows(min_row=2)}
    assert cells["Balansomslutning"].fill.start_color.rgb.endswith("C6EFCE")
    assert cells["Eget kapital"].fill.start_color.rgb.endswith("FFEB9C")
    assert cells["Skulder"].fill.start_color.rgb.endswith("FFC7CE")
    assert "Säkerhet: explicit" in cells["Balansomslutning"].comment.text


def test_excel_still_shades_an_older_result_file(tmp_path):
    """A result file from before this change must still colour correctly."""
    openpyxl = pytest.importorskip("openpyxl")
    from app.src.JBGJSONConverter import JsonConverter

    path = _write(tmp_path, {"Balansomslutning": 1.0, "Eget kapital": 0.6,
                             "Skulder": 0.3})
    out = tmp_path / "out.xlsx"
    JsonConverter(path, include_sources=True).to_excel_by_year(
        out, key_def_path=KEY_DEFS, fund_names=KASSOR, findings=[]
    )
    ws = openpyxl.load_workbook(out)["2024"]
    cells = {r[0].value: r[1] for r in ws.iter_rows(min_row=2)}
    assert cells["Balansomslutning"].fill.start_color.rgb.endswith("C6EFCE")
    assert cells["Eget kapital"].fill.start_color.rgb.endswith("FFEB9C")
    assert cells["Skulder"].fill.start_color.rgb.endswith("FFC7CE")


def test_legend_describes_the_levels(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from app.src.JBGJSONConverter import JsonConverter

    path = _write(tmp_path, {"Balansomslutning": "explicit"})
    out = tmp_path / "out.xlsx"
    JsonConverter(path, include_sources=True).to_excel_by_year(
        out, key_def_path=KEY_DEFS, fund_names=KASSOR, findings=[]
    )
    legend = openpyxl.load_workbook(out)["Läsanvisning"]
    text = " ".join(
        str(c) for row in legend.iter_rows(values_only=True) for c in row if c
    )
    for level in schema.CERTAINTY_LEVELS:
        assert level in text


def test_csv_carries_the_level_verbatim(tmp_path):
    from app.src.JBGJSONConverter import JsonConverter

    path = _write(tmp_path, {"Balansomslutning": "härledd"})
    out = tmp_path / "out.csv"
    JsonConverter(path, include_sources=True).to_csv(out)
    row = out.read_text(encoding="utf-8-sig").splitlines()[1]
    assert "härledd" in row


# ----------------------------------------------------------------- prompt
def test_prompt_explains_that_the_level_describes_the_method():
    """The distinction that makes the scale discriminate: "härledd" is about
    having had to calculate or interpret, not about feeling unsure."""
    raw = (ROOT / "app" / "prompt" / "GPT-instruktioner_komprimerad.md").read_text(
        encoding="utf-8"
    )
    text = " ".join(raw.split())  # the phrasing wraps across lines
    assert "Säkerhetsnivåer" in text
    assert "inte hur säker du känner dig" in text
    for level in schema.CERTAINTY_LEVELS:
        assert f'"{level}"' in text
    # the old numeric scale must be gone, or the model gets two sets of rules
    assert "= 1.0" not in text
    assert "> 0.9" not in text
