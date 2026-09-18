"""Tester för att person- och samordningsnummer upptäcks och svärtas.

Den tidigare kontrollen var `\\d{6}[-+]\\d{4}` och täckte därmed bara den
tiosiffriga formen med skiljetecken. Tolvsiffriga nummer missades helt – just
den form som står i e-signeringsrutorna, där de här dokumenten faktiskt bär
personuppgifter. Verifieringen kunde inte fånga det heller: den kontrollerar
att de termer som hittats är borta, och ett nummer som aldrig upptäcktes var
aldrig en term.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pymupdf = pytest.importorskip("pymupdf")

from app.src.masking.JBGPDFMasking import (  # noqa: E402
    PDFMasker,
    _looks_like_identity_number,
    _luhn_ok,
    find_identity_numbers,
)


# --------------------------------------------------------------- formerna
@pytest.mark.parametrize("text,expected", [
    ("Underskrift 650412-1234 styrelseledamot", "650412-1234"),
    ("Undertecknat 650412+1234", "650412+1234"),
    ("BankID 19811218-9876 signerat", "19811218-9876"),
    ("Signerat av 198112189876 kl 14:02", "198112189876"),
    ("Personnummer 8112189876 i registret", "8112189876"),
    ("Samordningsnummer 650472-1234", "650472-1234"),
])
def test_varje_form_av_personnummer_hittas(text, expected):
    assert find_identity_numbers(text) == {expected}


def test_tolvsiffrigt_nummer_hittades_inte_av_det_gamla_monstret():
    """Regressionen som föranledde ändringen."""
    import re

    gammalt = re.compile(r"\b\d{6}[-+]\d{4}\b")
    text = "Signerat 19811218-9876"

    assert not gammalt.search(text)
    assert find_identity_numbers(text) == {"19811218-9876"}


def test_ett_streck_ur_en_pdf_duger_lika_bra():
    """Pdf-läsare levererar ibland en annan streckvariant."""
    assert find_identity_numbers("650412\u20131234") == {"650412\u20131234"}


def test_flera_nummer_i_samma_text():
    found = find_identity_numbers("Ordförande 650412-1234, revisor 19811218-9876")
    assert found == {"650412-1234", "19811218-9876"}


def test_numret_returneras_ordagrant():
    """Termen används för att lokalisera och svärta, så skiljetecknet måste
    följa med precis som det står."""
    assert find_identity_numbers("Pnr 650412+1234")  == {"650412+1234"}


# ------------------------------------------------------------ inte nummer
def test_organisationsnummer_svartas_inte():
    """Det gamla mönstret svärtade a-kassans eget organisationsnummer ur
    varje årsredovisning. Formen är densamma; månadsplatsen skiljer dem åt."""
    assert find_identity_numbers("Organisationsnummer 802005-5000") == set()


@pytest.mark.parametrize("text", [
    "Fakturanummer 1234567890",
    "Referens 202401011234",
    "Belopp 1 234 567 890 kronor",
    "Bankgiro 5566-7788",
    "Konto 5566778899",
    "Datum 2025-12-31",
])
def test_siffror_som_inte_ar_personnummer_lamnas(text):
    assert find_identity_numbers(text) == set()


def test_orimligt_datum_avvisas():
    assert find_identity_numbers("651312-1234") == set()   # månad 13
    assert find_identity_numbers("650432-1234") == set()   # dag 32


# ------------------------------------------------------------- kontrollsiffra
def test_kontrollsiffran_kravs_bara_utan_skiljetecken():
    """Ett skiljetecken är starkt belägg i sig. En OCR-tolkad siffra som
    blivit fel ska inte leda till att numret lämnas omaskerat – där är en
    onödig svärtning det billigare felet."""
    assert _looks_like_identity_number("6504121235", has_separator=True)
    assert not _looks_like_identity_number("6504121235", has_separator=False)


def test_luhn_pa_kanda_nummer():
    assert _luhn_ok("8112189876")
    assert _luhn_ok("4604300014")
    assert not _luhn_ok("8112189875")


# --------------------------------------------------------- svepet över utdata
def _pdf_med_text(tmp_path, text, namn="fil.pdf"):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), text, fontsize=11)
    ut = tmp_path / namn
    doc.save(ut)
    doc.close()
    return ut


def test_svepet_hittar_nummer_i_en_fardig_pdf(tmp_path):
    pdf = _pdf_med_text(tmp_path, "Signerat av 19811218-9876")
    assert PDFMasker.find_identity_numbers_in_pdf(pdf) == {"19811218-9876"}


def test_svepet_ar_tyst_nar_det_inte_finns_nagot(tmp_path):
    pdf = _pdf_med_text(tmp_path, "Summa tillgangar 63 853 tkr")
    assert PDFMasker.find_identity_numbers_in_pdf(pdf) == set()


def test_svepet_ar_oberoende_av_termlistan(tmp_path):
    """Poängen med kontrollen: den svarar på 'finns det nummer kvar?', inte
    på 'är de termer vi hittade borta?'."""
    pdf = _pdf_med_text(tmp_path, "Ordforande 650412-1234")
    assert PDFMasker.find_identity_numbers_in_pdf(pdf)
