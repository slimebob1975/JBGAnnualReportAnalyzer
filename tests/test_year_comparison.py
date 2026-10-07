"""Tester för jämförelsen mellan år.

Trösklarna är mätta, inte gissade. På 234 jämförbara par mellan 2024 och 2025
var medianrörelsen 1,16 gånger, nittionde percentilen 6,4 och nittionionde 24.
En tröskel vid tio hade flaggat var elfte par, och de flesta med rätta:
Akademikernas finansiella intäkter föll faktiskt från 68 878 till 2 869.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.src import JBGYearComparison as yc  # noqa: E402


def _fund(name="Kassan", **years):
    return {name: {
        year: {metric: {"värde": value} for metric, value in metrics.items()}
        for year, metrics in years.items()
    }}


# ----------------------------------------------------------- vad som listas
def test_en_vanlig_rorelse_listas_inte():
    """Medianrörelsen mellan två år är 1,16 gånger. Det är inte en upplysning."""
    result = _fund(**{"2024": {"Medlemsavgifter": 1217951},
                      "2025": {"Medlemsavgifter": 1338451}})
    assert yc.compare(result) == []


def test_en_stor_men_verklig_rorelse_listas_utan_att_anmarkas():
    """Akademikernas finansiella intäkter föll faktiskt 24 gånger."""
    result = _fund(**{"2024": {"Finansiella intäkter": 68878},
                      "2025": {"Finansiella intäkter": 2869}})
    movements = yc.compare(result)

    assert len(movements) == 1
    assert round(movements[0]["kvot"]) == 24
    assert yc.findings(movements) == []


def test_en_fellasning_syns_i_listan_aven_under_troskeln():
    """GS a-kassas "Antal ersättningsdagar 22 286 -> 306 045" är fel, men
    13,7 gånger är för lite för att anmärka på. Den ska synas ändå."""
    result = _fund(**{"2024": {"Antal ersättningsdagar": 22286},
                      "2025": {"Antal ersättningsdagar": 306045}})
    movements = yc.compare(result)

    assert movements and round(movements[0]["kvot"], 1) == 13.7
    assert yc.findings(movements) == []


def test_storst_forst():
    result = _fund(**{
        "2024": {"A": 1000, "B": 1000, "C": 1000},
        "2025": {"A": 3000, "B": 100000, "C": 10000}})
    assert [m["nyckeltal"] for m in yc.compare(result)] == ["B", "C", "A"]


# ------------------------------------------------------------ vad som tystas
def test_sma_tal_ger_inga_rorelser():
    """En notpost som går från -832 till 1 rör sig 832 gånger utan att betyda
    någonting, och det är det lilla talet som avgör det."""
    result = _fund(**{"2024": {"Not till X: Fö": -832},
                      "2025": {"Not till X: Fö": 1}})
    assert yc.compare(result) == []


def test_ett_nyckeltal_som_saknas_ett_ar_ar_en_lucka_inte_en_rorelse():
    """Statistiken i bilaga 2 lades om inför 2025: tio av 64 värden saknas i
    2023 års rapporter, och den frånvaron säger ingenting om siffrorna."""
    result = _fund(**{"2024": {"Antal sanktion": 5000},
                      "2025": {"Antal sanktion": 5200, "Antal domar": 900}})
    assert [m["nyckeltal"] for m in yc.compare(result)] == []


def test_nollor_jamfors_inte():
    result = _fund(**{"2024": {"A": 0}, "2025": {"A": 50000}})
    assert yc.compare(result) == []


def test_metadata_i_resultatet_rors_inte():
    result = _fund(**{"2024": {"A": 1000}, "2025": {"A": 500000}})
    result["_ejanalyserade"] = [{"fil": "x.pdf", "orsak": "y"}]
    result["_redovisningsenhet"] = {"Kassan": "tkr"}
    assert len(yc.compare(result)) == 1


def test_ett_enda_ar_ger_ingenting_att_jamfora():
    assert yc.compare(_fund(**{"2025": {"A": 1000}})) == []


# ------------------------------------------------------------ anmärkningarna
def test_tusen_gangers_rorelse_anmarks():
    """Det är enhet eller felläsning, inte verksamhet."""
    result = _fund(**{"2024": {"Finansieringsavgift": 3104},
                      "2025": {"Finansieringsavgift": 3104000}})
    notes = yc.findings(yc.compare(result))

    assert len(notes) == 1
    assert "1\u00a0000 gånger" in notes[0]["meddelande"]
    assert notes[0]["ar"] == "2025"
    assert notes[0]["nyckeltal"] == "Finansieringsavgift"


def test_meddelandet_har_hela_meningar():
    """Tusentalsavgränsaren får inte äta meningens kommatecken."""
    result = _fund(**{"2024": {"A": 3104}, "2025": {"A": 3104000}})
    message = yc.findings(yc.compare(result))[0]["meddelande"]

    assert "(2025), en förändring" in message
    assert message.endswith("årsredovisningarna.")


def test_ett_teckenbyte_ar_ingen_anmarkning_i_sig():
    """En kassa som går från överskott till underskott byter tecken på årets
    resultat varje gång det händer."""
    result = _fund(**{"2024": {"Årets resultat": 5000},
                      "2025": {"Årets resultat": -4000}})
    movements = yc.compare(result)

    assert movements and movements[0]["teckenbyte"]
    assert yc.findings(movements) == []


# ------------------------------------------------------------ flera år i rad
def test_varje_par_av_pa_varandra_foljande_ar_jamfors():
    result = _fund(**{"2023": {"A": 1000}, "2024": {"A": 1100}, "2025": {"A": 600000}})
    movements = yc.compare(result)

    assert [(m["fran_ar"], m["till_ar"]) for m in movements] == [("2024", "2025")]


@pytest.mark.parametrize("value,expected", [(300, True), (99, False)])
def test_golvet_galler_det_mindre_vardet(value, expected):
    result = _fund(**{"2024": {"A": value}, "2025": {"A": value * 50}})
    assert bool(yc.compare(result)) is expected
