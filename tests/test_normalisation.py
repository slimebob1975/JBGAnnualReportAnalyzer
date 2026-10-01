"""Tests for corrections the model should never have been asked to make.

Two readings of the same document disagreed on 52 values, and 20 of those
were the same amount with the sign flipped — a convention the definitions
already state. Separately, `Utbetald arbetslöshetsersättning` arrived as
2 924 from one fund and 192 397 000 as the median of the rest: the same
quantity in different units, on a metric no arithmetic identity touches.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.src import JBGNormalisation as norm  # noqa: E402
from app.src import JBGValidation as validation  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KEY_DEFS = ROOT / "app" / "prompt" / "json" / "nyckeltalsdefinitioner.json"
RATIOS = ROOT / "app" / "prompt" / "json" / "nyckeltalsberakningar.json"

AMOUNT = "Utbetald arbetslöshetsersättning"


def _fund(name="Kassan", year="2025", **metrics):
    return {name: {year: {k: {"värde": v, "kommentar": ""}
                          for k, v in metrics.items()}}}


# ------------------------------------------------------------------- signs
def test_a_cost_written_negative_is_made_positive():
    result = _fund(**{"Summa administrationskostnader": -48000})
    assert norm.normalise_signs(result, KEY_DEFS) == 1
    assert result["Kassan"]["2025"]["Summa administrationskostnader"]["värde"] == 48000


def test_a_note_takes_the_sign_of_the_row_it_specifies():
    """The real Akademikernas case: note -2 923 688 against a row of
    +2 923 688, reported for four of six funds as a difference of twice the
    amount."""
    result = _fund(**{
        "Kostnad arbetslöshetsersättning": 2923688,
        "Not till Kostnad arbetslöshetsersättning: Summa": -2923688,
    })
    norm.normalise_signs(result, KEY_DEFS)
    metrics = result["Kassan"]["2025"]

    assert metrics["Not till Kostnad arbetslöshetsersättning: Summa"]["värde"] == 2923688
    assert validation.validate(result, KEY_DEFS) == []


def test_the_correction_says_what_it_did():
    """A correction nobody can see is worse than none."""
    result = _fund(**{"Summa administrationskostnader": -48000})
    norm.normalise_signs(result, KEY_DEFS)
    comment = result["Kassan"]["2025"]["Summa administrationskostnader"]["kommentar"]

    assert "Tecken normaliserat" in comment


def test_a_note_without_its_counterpart_is_left_alone():
    """With nothing to agree with, there is no convention to enforce."""
    result = _fund(**{"Not till Kostnad arbetslöshetsersättning: Summa": -2923688})
    assert norm.normalise_signs(result, KEY_DEFS) == 0


def test_an_amount_is_never_rescaled_only_its_sign_set():
    result = _fund(**{"Summa administrationskostnader": -48000.75})
    norm.normalise_signs(result, KEY_DEFS)
    assert result["Kassan"]["2025"]["Summa administrationskostnader"]["värde"] == 48000.75


def test_a_metric_with_no_documented_convention_is_untouched():
    result = _fund(**{"Årets resultat": -406})
    assert norm.normalise_signs(result, KEY_DEFS) == 0


# ------------------------------------------------------------------- units
def _corpus(values: dict) -> dict:
    return {fund: {"2025": {AMOUNT: {"värde": v, "kommentar": ""}}}
            for fund, v in values.items()}


def test_thousands_are_converted_to_kronor_against_the_other_funds():
    result = _corpus({
        "Fastighets": 418273, "Byggnads": 210500000, "Handels": 300100000,
        "IF Metall": 192397000.50, "Kommunal": 410200000, "Seko": 150300000.25,
    })
    applied = norm.normalise_units(result, KEY_DEFS)

    assert [(a[0], a[4]) for a in applied] == [("Fastighets", 418273000)]
    assert "tusental kronor" in result["Fastighets"]["2025"][AMOUNT]["kommentar"]


def test_a_converted_value_no_longer_looks_like_a_unit_error():
    result = _corpus({
        "Fastighets": 418273, "Byggnads": 210500000, "Handels": 300100000,
        "IF Metall": 192397000.50, "Kommunal": 410200000, "Seko": 150300000.25,
    })
    norm.normalise_units(result, KEY_DEFS)
    assert validation.check_unit_consistency(result, KEY_DEFS) == []


def test_a_correction_that_would_not_explain_the_gap_is_refused():
    """Akademikernas 2 924 against a median of 192 397 000 is a thousandfold
    out even after the best available factor. Left for a human, and still
    flagged."""
    result = _corpus({
        "Akademikernas": 2924, "Byggnads": 210500000, "Handels": 300100000,
        "IF Metall": 192397000.50, "Kommunal": 410200000, "Seko": 150300000.25,
    })
    assert norm.normalise_units(result, KEY_DEFS) == []
    assert result["Akademikernas"]["2025"][AMOUNT]["värde"] == 2924
    assert [f.fund for f in validation.check_unit_consistency(result, KEY_DEFS)] == [
        "Akademikernas"
    ]


def test_a_fund_that_is_merely_unusual_is_not_corrected():
    result = _corpus({
        "Liten": 20000000, "Byggnads": 210500000, "Handels": 300100000,
        "IF Metall": 192397000.50, "Kommunal": 410200000, "Seko": 150300000.25,
    })
    assert norm.normalise_units(result, KEY_DEFS) == []


def test_too_few_funds_means_no_basis_for_a_correction():
    result = _corpus({"A": 418273, "B": 210500000, "C": 300100000})
    assert norm.normalise_units(result, KEY_DEFS) == []


def test_counts_are_never_rescaled():
    result = {
        fund: {"2025": {"Totalt antal medlemmar 31 december": {"värde": v}}}
        for fund, v in {"A": 700, "B": 500000, "C": 300000, "D": 9000,
                        "E": 4000, "F": 250000}.items()
    }
    assert norm.normalise_units(result, KEY_DEFS) == []


# ------------------------------------------------------------------ ratios
def test_an_impossible_ratio_is_flagged():
    """Småföretagarnas Finansieringsavgift came in kronor where the rest of
    the income statement was in thousands. The figure looked ordinary; the
    cost coverage it produced was half a day."""
    result = _fund(**{
        "Summa eget kapital": 16339,
        "Summa administrationskostnader": 24000,
        "Finansieringsavgift": 117308746,
        "Finansiella kostnader": 120,
    })
    findings = validation.check_ratio_plausibility(result, KEY_DEFS)

    assert any("Kostnadstäckning" in f.message for f in findings)
    assert any("fel enhet" in f.message for f in findings)


def test_an_ordinary_fund_passes_every_band():
    result = _fund(**{
        "Summa eget kapital": 16339,
        "Summa administrationskostnader": 24000,
        "Finansieringsavgift": 30000,
        "Finansiella kostnader": 120,
        "Totalt antal medlemmar 31 december": 40000,
        "Antal medlemmar som fått ersättning": 3200,
        "Summa omsättningstillgångar": 60000,
        "Fordringar statligt bidrag till arbetslöshetsersättning": 5000,
        "Fordringar felaktig arbetslöshetsersättning": 1000,
        "Not till Övriga fordringar: Källskatt arbetslöshetsersättning": 500,
        "Summa skulder": 40000,
        "Skulder arbetslöshetsersättning": 9000,
        "Not till Övriga skulder: Källskatt arbetslöshetsersättning": 800,
        "Summa tillgångar": 63853,
    })
    assert validation.check_ratio_plausibility(result, KEY_DEFS) == []


def test_a_ratio_missing_an_input_is_not_computed():
    """A ratio built from gaps would produce findings about the gaps."""
    result = _fund(**{"Summa eget kapital": 16339})
    assert validation.check_ratio_plausibility(result, KEY_DEFS) == []


@pytest.mark.parametrize("ratio", ["Soliditet", "Kassalikviditet",
                                   "Kostnadstäckning (månader)"])
def test_every_ratio_has_a_band(ratio):
    import json

    definitions = {d["Nyckeltal"]: d for d in json.loads(
        RATIOS.read_text(encoding="utf-8"))}
    low, high = definitions[ratio]["Rimligt intervall"]
    assert low < high


def test_the_bands_admit_the_whole_observed_range():
    """Across 24 funds cost coverage ran 0.02–8.5 and liquidity 0.28–10.3.
    The bands catch impossibilities, not outliers."""
    import json

    definitions = {d["Nyckeltal"]: d["Rimligt intervall"] for d in json.loads(
        RATIOS.read_text(encoding="utf-8"))}
    observed = {
        "Soliditet": (0.43, 0.91),
        "Kassalikviditet": (0.28, 10.27),
        "Administrationskostnad per medlem (kr)": (298.07, 1332.72),
        "Ersättningstagare per medlem": (0.03, 0.21),
    }
    for name, (low, high) in observed.items():
        band_low, band_high = definitions[name]
        assert band_low < low and high < band_high, f"{name} ligger utanför bandet"


# --------------------------------------------- referensen rör sig när kolumnen rättas
def _column(values: dict) -> dict:
    return {fund: {"2025": {AMOUNT: {"värde": v, "kommentar": ""}}}
            for fund, v in values.items()}


# Kolumnen som den såg ut i körningen den 17 september 2026.
UTBETALD_2025 = {
    "Akademikernas": 2924, "Alfa": 2081, "Byggnads": 1234983481,
    "Elektrikernas": 97084698, "Fastighets": 418273, "Finans": 152011,
    "GS": 282070914, "Handels": 865087575, "Hotell": 374598000,
    "IF Metall": 426920012, "Journalisternas": 70133, "Kommunal": 3542833,
    "Ledarnas": 73226886, "Livs": 186613, "Lärarnas": 291728097,
    "Pappers": 421904068, "STs": 511450000, "Seko": 1086186476,
    "Småföretagarnas": 590903, "Sveriges arbetares": 1360979734,
    "Säljarnas": 192397000, "Transport": 1801362020, "Unionens": 528213,
    "Vision": 1086186476,
}


def test_hela_kolumnen_hamnar_i_kronor():
    """Ett varv räckte inte. Kommunalarbetarnas låg 52 gånger under en median
    på cirka 186 miljoner och lämnades orörd; när åtta andra kassor väl lyfts
    till kronor var medianen 418 miljoner och samma värde låg 118 gånger
    under."""
    result = _column(UTBETALD_2025)

    norm.normalise_units(result, KEY_DEFS)

    assert validation.check_unit_consistency(result, KEY_DEFS) == []
    assert result["Kommunal"]["2025"][AMOUNT]["värde"] == 3542833000
    assert result["Akademikernas"]["2025"][AMOUNT]["värde"] == 2924000000


def test_kassor_som_redan_var_i_kronor_lamnas_orda():
    result = _column(UTBETALD_2025)
    norm.normalise_units(result, KEY_DEFS)

    for fund in ("Byggnads", "Transport", "Säljarnas"):
        assert result[fund]["2025"][AMOUNT]["värde"] == UTBETALD_2025[fund]


def test_ett_belopp_raknas_om_hogst_en_gang():
    result = _column(UTBETALD_2025)
    applied = norm.normalise_units(result, KEY_DEFS)

    rort = [(a[0], a[2]) for a in applied]
    assert len(rort) == len(set(rort))
    for fund, *_ in applied:
        assert result[fund]["2025"][AMOUNT]["kommentar"].count("Omräknat") == 1


def test_faktorn_valjs_pa_logaritmiskt_avstand():
    """Linjärt avstånd från 1 straffar en faktor som skjuter över målet långt
    hårdare än en som hamnar under: för 2 924 mot medianen 418 miljoner ger
    ×1000 avståndet 0,99 och ×1 000 000 avståndet 5,99, så det uppenbart
    felaktiga tusentalet vann."""
    result = _column(UTBETALD_2025)
    norm.normalise_units(result, KEY_DEFS)

    varde = result["Akademikernas"]["2025"][AMOUNT]["värde"]
    assert varde == 2924 * 1_000_000
    assert "miljoner kronor" in result["Akademikernas"]["2025"][AMOUNT]["kommentar"]


def test_rattaren_och_kontrollen_anvander_samma_referens():
    """Räknade de var för sig blev de oense om samma kolumn: en kassa kunde
    lämnas orörd av normaliseringen och ändå anmärkas av kontrollen."""
    peers = [v for f, v in UTBETALD_2025.items() if f != "Kommunal"]
    assert validation.normalisation._reference_value is norm._reference_value
    assert norm._reference_value(peers) > 0


def test_iterationen_stannar_nar_inget_mer_andras():
    """Taket finns för att ingen körning ska fastna, inte för att det ska
    behövas."""
    result = _column(UTBETALD_2025)
    forsta = norm.normalise_units(result, KEY_DEFS)
    andra = norm.normalise_units(result, KEY_DEFS)

    assert forsta
    assert andra == []
