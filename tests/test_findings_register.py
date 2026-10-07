"""Tester för registret över återkommande anmärkningar.

Skillnaden mellan brus och mönster avgör vad som är värt att åtgärda, och den
syns inte i en enskild körning. Alfa-kassans Summa intäkter avvek med exakt
70 691 fem körningar i rad innan någon kände igen beloppet; Visions Summa
tillgångar avvek med 99 670 i en enda körning och aldrig mer.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.src import JBGFindingsRegister as register  # noqa: E402
from app.src.JBGValidation import Finding  # noqa: E402

ALFA = Finding(
    fund="Arbetslöshetskassan Alfa", year="2025",
    rule="Delsummering: Summa intäkter",
    message="Summa intäkter 415 254 är 70 691 större än ...",
    metrics=["Summa intäkter", "Medlemsavgifter"],
)
VISION = Finding(
    fund="Vision", year="2025", rule="Delsummering: Summa tillgångar",
    message="Summa tillgångar 180 195 stämmer inte med ...",
    metrics=["Summa tillgångar"],
)


def test_en_anmarkning_raknas_upp_vid_varje_korning(tmp_path):
    for _ in range(5):
        register.record([ALFA], tmp_path)

    entries = register.summary(tmp_path)
    assert len(entries) == 1
    assert entries[0]["antal"] == 5
    assert entries[0]["kassa"] == "Arbetslöshetskassan Alfa"


def test_det_envisaste_star_forst(tmp_path):
    for _ in range(5):
        register.record([ALFA], tmp_path)
    register.record([VISION], tmp_path)

    antal = [entry["antal"] for entry in register.summary(tmp_path)]
    assert antal == [5, 1]


def test_beloppen_i_meddelandet_skiljer_inte_anmarkningarna_at(tmp_path):
    """Beloppen ändras mellan körningar även när det är samma sak som
    anmärks. Kassa, nyckeltal och kontroll är det som identifierar den."""
    register.record([ALFA], tmp_path)
    annan_lydelse = Finding(
        fund=ALFA.fund, year=ALFA.year, rule=ALFA.rule,
        message="Summa intäkter 415 300 är 70 737 större än ...",
        metrics=ALFA.metrics,
    )
    register.record([annan_lydelse], tmp_path)

    entries = register.summary(tmp_path)
    assert len(entries) == 1
    assert entries[0]["antal"] == 2


def test_senaste_lydelsen_sparas(tmp_path):
    register.record([ALFA], tmp_path)
    senare = Finding(fund=ALFA.fund, year=ALFA.year, rule=ALFA.rule,
                     message="Den senaste lydelsen.", metrics=ALFA.metrics)
    register.record([senare], tmp_path)

    assert register.summary(tmp_path)[0]["senaste_meddelande"] == "Den senaste lydelsen."


def test_olika_kassor_halls_isar(tmp_path):
    register.record([ALFA, VISION], tmp_path)
    assert len(register.summary(tmp_path)) == 2


def test_samma_kontroll_pa_olika_ar_ar_olika_poster(tmp_path):
    register.record([ALFA], tmp_path)
    forra_aret = Finding(fund=ALFA.fund, year="2024", rule=ALFA.rule,
                         message="x", metrics=ALFA.metrics)
    register.record([forra_aret], tmp_path)

    assert len(register.summary(tmp_path)) == 2


def test_forst_och_senast_noteras(tmp_path):
    entry = register.record([ALFA], tmp_path)
    post = next(iter(entry.values()))
    assert post["forst"] == post["senast"]


def test_ett_tomt_register_ger_tom_lista(tmp_path):
    assert register.summary(tmp_path) == []
    assert register.load(tmp_path) == {}


def test_en_trasig_registerfil_ignoreras(tmp_path):
    register.register_path(tmp_path).write_text("{inte json", encoding="utf-8")
    assert register.load(tmp_path) == {}


def test_registret_kan_aldrig_falla_en_korning(tmp_path):
    hinder = tmp_path / "en-fil"
    hinder.write_text("inte en katalog", encoding="utf-8")
    assert register.record([ALFA], hinder) is not None


def test_registret_gar_att_lasa_som_vanlig_json(tmp_path):
    """Filen ska gå att öppna och förstå utan verktyget."""
    register.record([ALFA], tmp_path)
    data = json.loads(register.register_path(tmp_path).read_text(encoding="utf-8"))

    assert len(data) == 1
    post = next(iter(data.values()))
    assert set(post) >= {"kassa", "ar", "kontroll", "nyckeltal", "antal"}
