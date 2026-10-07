"""Tester för historiken.

Resultatet låg hittills bara i jobbkatalogen, som städas efter en timmes
overksamhet. En körning av fjolårets material hade alltså varit borta innan
någon hunnit jämföra mot den — och föregående år är den enda externa referens
som finns kvar när den manuella inmatningen upphör.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.src import JBGHistory as history  # noqa: E402

RESULT = {
    "Livsmedelsarbetarnas arbetslöshetskassa": {
        "2025": {"Summa tillgångar": {"värde": 46749}}},
    "GS Arbetslöshetskassa": {
        "2025": {"Summa tillgångar": {"värde": 65588}}},
    "_ejanalyserade": [],
}


def test_en_korning_sparas_med_ar_och_antal_kassor(tmp_path):
    saved = history.save(RESULT, tmp_path)

    assert saved.name.startswith("2025_")
    assert saved.name.endswith("_2kassor.json")
    assert json.loads(saved.read_text(encoding="utf-8")) == RESULT


def test_metadata_raknas_inte_som_kassa(tmp_path):
    """Nycklar med inledande understreck är anmärkningar och liknande."""
    saved = history.save(RESULT, tmp_path)
    assert "2kassor" in saved.name


def test_ett_tomt_resultat_sparas_inte(tmp_path):
    assert history.save({}, tmp_path) is None
    assert history.save({"_ejanalyserade": []}, tmp_path) is None
    assert list(tmp_path.glob("*.json")) == []


def test_en_korning_med_flera_ar_namnges_efter_det_senaste(tmp_path):
    """Ett blandat upplägg går att ladda upp i dag, och filen innehåller
    alla åren oavsett vad den heter."""
    blandat = {"Kassan": {"2023": {"A": {"värde": 1}},
                          "2024": {"A": {"värde": 2}},
                          "2025": {"A": {"värde": 3}}}}
    saved = history.save(blandat, tmp_path)

    assert saved.name.startswith("2025_")
    assert set(json.loads(saved.read_text(encoding="utf-8"))["Kassan"]) == {
        "2023", "2024", "2025"
    }


def test_sparade_korningar_listas_nyast_forst(tmp_path):
    (tmp_path / "2023_20240101T000000_23kassor.json").write_text("{}", encoding="utf-8")
    (tmp_path / "2024_20250101T000000_24kassor.json").write_text("{}", encoding="utf-8")
    (tmp_path / "2025_20260101T000000_24kassor.json").write_text("{}", encoding="utf-8")

    listan = history.available(tmp_path)

    assert [post["ar"] for post in listan] == ["2025", "2024", "2023"]
    assert listan[0]["kassor"] == 24


def test_filer_som_inte_ar_historik_ignoreras(tmp_path):
    (tmp_path / "anteckningar.json").write_text("{}", encoding="utf-8")
    (tmp_path / "2025_trasigt_namn.json").write_text("{}", encoding="utf-8")
    assert history.available(tmp_path) == []


def test_en_katalog_som_inte_finns_ger_tom_lista(tmp_path):
    assert history.available(tmp_path / "finns-inte") == []


def test_en_sparad_korning_gar_att_lasa_tillbaka(tmp_path):
    saved = history.save(RESULT, tmp_path)
    assert history.load(saved) == RESULT


def test_en_trasig_fil_ger_tom_uppslagsbok(tmp_path):
    trasig = tmp_path / "trasig.json"
    trasig.write_text("{inte json", encoding="utf-8")
    assert history.load(trasig) == {}


def test_historiken_kan_aldrig_falla_en_korning(tmp_path):
    """En bekvämlighet ska inte kunna stoppa en analys som gick bra."""
    hinder = tmp_path / "en-fil"
    hinder.write_text("inte en katalog", encoding="utf-8")
    assert history.save(RESULT, hinder) is None


def test_katalogen_styrs_av_miljovariabel(tmp_path, monkeypatch):
    monkeypatch.setenv("JBG_HISTORY_DIR", str(tmp_path / "egen"))
    assert history.history_dir() == tmp_path / "egen"
