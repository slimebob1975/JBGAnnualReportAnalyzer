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


# ------------------------------------------- dokument som inte kunde analyseras
SKIPPED = [
    {"fil": "GS a-kassa Årsredovisning 2025.pdf",
     "orsak": "Maskeringen misslyckades: en känslig term står kvar."},
    {"fil": "Årsredovisning 2025(172140).pdf",
     "orsak": "Maskeringen misslyckades: ett namn står kvar bredvid sin roll."},
]


def _export_with_skipped(tmp_path, skipped):
    data = {"Livsmedelsarbetarnas arbetslöshetskassa": {"2025": {
        name: {"värde": value, "källa": "Sida 7", "säkerhet": "explicit",
               "kommentar": ""}
        for name, value in RATIO_INPUTS.items()
    }}}
    if skipped:
        data["_ejanalyserade"] = skipped
    source = tmp_path / "resultat.json"
    source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out.xlsx"
    JsonConverter(source, include_sources=True).to_excel_by_year(
        out, key_def_path=KEY_DEFS, fund_names=KASSOR, findings=[]
    )
    return openpyxl.load_workbook(out)


def test_overhoppade_dokument_syns_overst_i_bladet(tmp_path):
    """En kolumn som saknas är svårare att upptäcka än en cell som är fel:
    den som summerar raden får ett rimligt tal, bara räknat på färre kassor."""
    ws = _export_with_skipped(tmp_path, SKIPPED)["2025"]

    # Nämnaren var antalet kassor i fliken, vilket inte är antalet uppladdade
    # dokument så snart körningen omfattar flera år.
    assert "2 uppladdade dokument" in ws.cell(1, 1).value
    assert ws.cell(1, 1).font.bold
    assert ws.cell(2, 1).value == "Nyckeltal"
    assert ws.freeze_panes == "B3"


def test_utan_overhoppade_ser_bladet_ut_som_forr(tmp_path):
    ws = _export_with_skipped(tmp_path, [])["2025"]

    assert ws.cell(1, 1).value == "Nyckeltal"
    assert ws.freeze_panes == "B2"


def test_orsaken_star_i_lasanvisningen(tmp_path):
    ws = _export_with_skipped(tmp_path, SKIPPED)["Läsanvisning"]
    text = "\n".join(
        str(ws.cell(r, c).value or "")
        for r in range(1, ws.max_row + 1) for c in (1, 2)
    )

    assert "Ej analyserade dokument (2)" in text
    for entry in SKIPPED:
        assert entry["fil"] in text
        assert entry["orsak"] in text


def test_nyckeltalsformlerna_foljer_med_nedat(tmp_path):
    """Banderollen skjuter alla rader ett steg ned. Formlerna byggs av de
    rader som faktiskt skrevs, så de ska peka rätt ändå."""
    ws = _export_with_skipped(tmp_path, SKIPPED)["2025"]

    equity = _row_of(ws, "Summa eget kapital")
    soliditet = ws.cell(_row_of(ws, "Soliditet"), 2).value
    assert f"B{equity}" in soliditet
    assert equity > 2, "raderna borde ha förskjutits av banderollen"


def test_banderollen_namner_bara_arets_flikar(tmp_path):
    """Källfliken ska ha samma varning, annars kan den läsas som fullständig."""
    wb = _export_with_skipped(tmp_path, SKIPPED)
    assert "uppladdade dokument" in wb["2025 med källa"].cell(1, 1).value


# ------------------------------------------ fliken med förvaltningsberättelsen
SUMMARIES = {"Livsmedelsarbetarnas arbetslöshetskassa": {
    "handelser": {"sammanfattning": "Nytt regelverk trädde i kraft.",
                  "citat": "Nytt regelverk gällande arbetslöshetsförsäkringen",
                  "sida": 3},
    "utveckling": {"sammanfattning": "Fortsatt hög arbetslöshet väntas.",
                   "citat": "förhållandevis hög arbetslöshet", "sida": 3},
    "medelsforvaltning": {"sammanfattning": "Framgår inte av årsredovisningen",
                          "citat": "", "sida": 0}}}


def _export_with_summaries(tmp_path, summaries):
    data = {"Livsmedelsarbetarnas arbetslöshetskassa": {"2025": {
        "Summa tillgångar": {"värde": 46749, "källa": "Sida 5",
                             "säkerhet": "explicit", "kommentar": ""}}}}
    if summaries:
        data["_forvaltningsberattelse"] = summaries
    source = tmp_path / "resultat.json"
    source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out.xlsx"
    JsonConverter(source, include_sources=True).to_excel_by_year(
        out, key_def_path=KEY_DEFS, fund_names=KASSOR, findings=[]
    )
    return openpyxl.load_workbook(out)


def test_citatet_star_bredvid_den_sammanfattning_det_stoder(tmp_path):
    """Inte samlat längst ut: den som läser en sammanfattning ska kunna
    kontrollera den utan att leta."""
    ws = _export_with_summaries(tmp_path, SUMMARIES)["Förvaltningsberättelse"]
    rubriker = [ws.cell(1, c).value for c in range(1, 11)]

    assert rubriker[0] == "Kassa"
    assert rubriker[2] == "Citat som stöder"
    assert rubriker[3] == "Sida"
    assert ws.cell(2, 2).value.startswith("Nytt regelverk")
    assert ws.cell(2, 3).value.startswith("Nytt regelverk gällande")


def test_tom_uppgift_sager_att_den_saknas(tmp_path):
    """Kravet på medelsförvaltning väntas först nästa år. Tomma celler är rätt
    utfall — men de ska vara tomma av rätt skäl."""
    ws = _export_with_summaries(tmp_path, SUMMARIES)["Förvaltningsberättelse"]
    assert ws.cell(2, 8).value == "Framgår inte av årsredovisningen"
    assert not ws.cell(2, 9).value


def test_ingen_flik_nar_inga_sammanfattningar_finns(tmp_path):
    wb = _export_with_summaries(tmp_path, None)
    assert "Förvaltningsberättelse" not in wb.sheetnames


def test_texten_radbryts_sa_att_den_gar_att_lasa(tmp_path):
    ws = _export_with_summaries(tmp_path, SUMMARIES)["Förvaltningsberättelse"]
    assert ws.cell(2, 2).alignment.wrap_text
    assert ws.column_dimensions["B"].width > 40


def test_ett_ungefarligt_citat_markeras_i_stallet_for_att_strykas(tmp_path):
    """GS a-kassa fick två av tre sammanfattningar strukna trots att de var
    riktiga. Att kasta uppgiften är inte försiktigt, det är bara tomt."""
    summaries = {"Livsmedelsarbetarnas arbetslöshetskassa": {
        "handelser": {"sammanfattning": "Nytt regelverk trädde i kraft.",
                      "citat": "Nytt regelverk gällande arbetslöshetsförsäkringen",
                      "sida": 3, "citat_kontroll": "ungefärligt",
                      "citat_stod": 0.72},
        "utveckling": {"sammanfattning": "Hög arbetslöshet väntas.",
                       "citat": "förhållandevis hög arbetslöshet", "sida": 3,
                       "citat_kontroll": "återfunnet", "citat_stod": 1.0},
        "medelsforvaltning": {"sammanfattning": "Framgår inte av årsredovisningen",
                              "citat": "", "sida": 0,
                              "citat_kontroll": "saknas", "citat_stod": 0.0}}}
    ws = _export_with_summaries(tmp_path, summaries)["Förvaltningsberättelse"]

    ungefarligt, aterfunnet = ws.cell(2, 3), ws.cell(2, 6)
    assert ungefarligt.value, "citatet ska finnas kvar, inte strykas"
    assert ungefarligt.fill.start_color.rgb[-6:] == JsonConverter.FLAGGED_FILL
    assert "72" in ungefarligt.comment.text
    assert aterfunnet.comment is None, "ett återfunnet citat ska inte märkas"


# ----------------------------------------- vad som kom in och vad som kom ut
def _export_with_summary(tmp_path, summary):
    data = {"Livsmedelsarbetarnas arbetslöshetskassa": {"2025": {
        "Summa tillgångar": {"värde": 46749, "källa": "Sida 5",
                             "säkerhet": "explicit", "kommentar": ""}}}}
    if summary:
        data["_korningen"] = summary
    source = tmp_path / "resultat.json"
    source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out.xlsx"
    JsonConverter(source, include_sources=True).to_excel_by_year(
        out, key_def_path=KEY_DEFS, fund_names=KASSOR, findings=[]
    )
    return openpyxl.load_workbook(out)


def _lasanvisning_text(wb):
    ws = wb["Läsanvisning"]
    return "\n".join(
        str(ws.cell(r, c).value or "")
        for r in range(1, ws.max_row + 1) for c in (1, 2)
    )


def test_korningens_siffror_star_i_lasanvisningen(tmp_path):
    wb = _export_with_summary(tmp_path, {
        "uppladdade_dokument": 24, "ej_analyserade": 1,
        "kassa_ar_kombinationer": 23})
    text = _lasanvisning_text(wb)

    assert "Uppladdade dokument: 24" in text
    assert "Ej analyserade: 1" in text
    assert "Kombinationer av kassa och år i utdata: 23" in text
    assert "Antalet stämmer inte" not in text


def test_en_kassa_som_tappats_pa_vagen_sags_ut(tmp_path):
    """Syns annars bara genom att någon råkar räkna kolumner."""
    wb = _export_with_summary(tmp_path, {
        "uppladdade_dokument": 24, "ej_analyserade": 1,
        "kassa_ar_kombinationer": 22})

    assert "Antalet stämmer inte" in _lasanvisning_text(wb)
