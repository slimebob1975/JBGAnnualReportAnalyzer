"""Tester för namn som står intill sin roll.

Bakgrund: i en levererad maskerad årsredovisning stod fem namn kvar fullt
läsbara på styrelsesidan – två styrelseledamöter, två revisorssuppleanter och
en i ledningsgruppen – medan ett tjugotal andra namn på samma sida var
svärtade. Loggen sa "Maskering verifierad: inga av 46 termer återfinns i
utdata", och det var sant: namnen upptäcktes aldrig av NER-modellen, var
därför aldrig termer, och verifieringen hade ingenting att leta efter.

Uppställningen är däremot förutsägbar. Namnet står på egen rad, direkt före
eller efter sin roll.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pymupdf = pytest.importorskip("pymupdf")

from app.src.masking.JBGPDFMasking import (  # noqa: E402
    PDFMasker,
    _is_role_line,
    _looks_like_name_line,
    find_names_by_role_context,
)

# Sidan som den faktiskt extraherades ur dokumentet.
STYRELSESIDAN = """8
Ledningsgruppen
Kassaföreståndare
Avdelningschef
Ulf Hellstenius
Chefsjurist
och IT-chef
HR- och kommunikationschef
Arbetsutskott och försäkringsutskott
Utskotten har sammanträtt 11 gånger (11).
ordförande
Ledamot
Revisorer
Auktoriserad revisor, PwC
Förtroendevald revisor
Anna Jessica Rosenius
Suppleant, förtroendevald revisor
Sissi Alfarhani
Suppleant, förtroendevald revisor
Styrelsen
Styrelsen har sammanträtt 12 gånger (11).
ordförande
Ledamot
Statlig representant, till 12 februari
Jonas Björklund
Arbetstagarledamot ST
Arbetstagarledamot SACO
Niklas Blomqvist
Suppleant
Förändringar i styrelsen
Den 30 september avgick
som suppleant i
styrelsen. Någon ersättare för
har ännu inte utsetts."""


def test_de_fem_namn_som_lackte_hittas():
    assert find_names_by_role_context([STYRELSESIDAN]) == {
        "Ulf Hellstenius",
        "Anna Jessica Rosenius",
        "Sissi Alfarhani",
        "Jonas Björklund",
        "Niklas Blomqvist",
    }


@pytest.mark.parametrize("rad", [
    "Auktoriserad revisor, PwC",
    "Förtroendevald revisor",
    "HR- och kommunikationschef",
    "Arbetsutskott och försäkringsutskott",
    "Styrelsen har sammanträtt 12 gånger (11).",
    "Statlig representant, till 12 februari",
    "Den 30 september avgick",
    "styrelsen. Någon ersättare för",
])
def test_rader_som_inte_ar_namn_tas_inte_med(rad):
    assert rad not in find_names_by_role_context([STYRELSESIDAN + "\n" + rad])


def test_namnet_maste_sta_intill_en_roll():
    """Två versala ord räcker inte. Annars svärtas ortnamn och rubriker."""
    utan_roll = "Verksamheten i siffror\nBorås Hemse\nSundbyberg Ljusdal"
    assert find_names_by_role_context([utan_roll]) == set()


def test_lopande_text_med_ett_rollord_rors_inte():
    """Raden är för lång för att vara en tabellrad, och innehåller mer än ett
    namn."""
    prosa = (
        "Styrelsen och kassaföreståndaren för Arbetslöshetskassan Alfa, "
        "organisationsnummer 816400–5236, avger härmed årsredovisning."
    )
    assert find_names_by_role_context([prosa]) == set()


def test_namn_fore_respektive_efter_rollen_fangas_bada():
    före = "Anna Bergström\nOrdförande"
    efter = "Ordförande\nAnna Bergström"
    assert find_names_by_role_context([före]) == {"Anna Bergström"}
    assert find_names_by_role_context([efter]) == {"Anna Bergström"}


@pytest.mark.parametrize("namn", [
    "Anna Bergh-Nilsson",
    "Nils Åkesson",
    "Sissi Alfarhani",
    "Anna Jessica Rosenius",
])
def test_svenska_namnformer(namn):
    assert find_names_by_role_context([f"{namn}\nSuppleant"]) == {namn}


def test_ett_ensamt_efternamn_racker_inte():
    """Ett ord kan vara en rubrik eller ett bolagsnamn. PwC ska inte svärtas."""
    assert find_names_by_role_context(["PwC\nAuktoriserad revisor"]) == set()


def test_rader_med_siffror_ar_inte_namn():
    assert not _looks_like_name_line("Den 12 februari tillsattes")
    assert not _looks_like_name_line("Styrelsen har sammanträtt 12 gånger")


def test_rollorden_kanns_igen_oavsett_versaler():
    assert _is_role_line("ORDFÖRANDE")
    assert _is_role_line("Suppleant, förtroendevald revisor")
    assert not _is_role_line("Verksamheten i siffror")


# ------------------------------------------------------ svepet över utdata
def _pdf_med_rader(tmp_path, rader, namn="fil.pdf"):
    doc = pymupdf.open()
    page = doc.new_page()
    for i, rad in enumerate(rader):
        page.insert_text((72, 100 + i * 16), rad, fontsize=11)
    ut = tmp_path / namn
    doc.save(ut)
    doc.close()
    return ut


def test_svepet_hittar_ett_namn_som_star_kvar(tmp_path):
    pdf = _pdf_med_rader(tmp_path, ["Jonas Bjorklund", "Arbetstagarledamot ST"])
    assert PDFMasker.find_role_names_in_pdf(pdf) == {"Jonas Bjorklund"}


def test_svepet_ar_tyst_nar_namnet_ar_svartat(tmp_path):
    """Efter svärtning finns rollen kvar men inte namnet."""
    pdf = _pdf_med_rader(tmp_path, ["Arbetstagarledamot ST", "Suppleant"])
    assert PDFMasker.find_role_names_in_pdf(pdf) == set()


def test_svepet_ar_oberoende_av_termlistan(tmp_path):
    """Hela poängen: det fungerar även för namn som aldrig upptäcktes."""
    pdf = _pdf_med_rader(tmp_path, ["Nils Akesson", "Suppleant"])
    assert PDFMasker.find_role_names_in_pdf(pdf)


# ------------------------------------- radstrukturen ändras av svärtningen
def test_namnet_under_ett_svartat_namn_hittas_forst_efterat():
    """Det fall som fällde nio dokument.

    Före svärtning låg ett annat namn mellan rollen och "Peter Pålsson", som
    därför inte var rollgranne. När det namnet svärtades försvann raden ur
    textlagret och Pålsson hamnade direkt under rubriken. Detektorn såg
    ingenting före, svepet såg honom efter, och filen kastades.
    """
    fore = "Revisorer suppleanter\nAnna Andersson\nPeter Pålsson\nPWC"
    efter = "Revisorer suppleanter\nPeter Pålsson\nPWC"

    assert find_names_by_role_context([fore]) == {"Anna Andersson"}
    assert find_names_by_role_context([efter]) == {"Peter Pålsson"}


def test_citattecken_runt_namnet_diskvalificerar_det_inte():
    """Verifikatsidan skriver namnet inom citattecken, och det namnet stod
    kvar i den levererade filen utan att ens flaggas."""
    sida = (
        'Namnet som returnerades från svenskt BankID var\n'
        '"ELISABETH CAMNER"\n'
        'Signerade 2026-05-18 14:33:49 CEST (+0200)'
    )
    assert find_names_by_role_context([sida]) == {"ELISABETH CAMNER"}


def test_versala_namn_kanns_igen():
    assert find_names_by_role_context(["NILS AKESSON\nOrdförande"]) == {"NILS AKESSON"}


def test_signeringssidans_formulering_raknas_som_roll():
    assert _is_role_line("Namnet som returnerades från svenskt BankID var")
    assert _is_role_line("Undertecknat av")
    assert not _is_role_line("Dokument-ID 09222115557578355370")


def test_vanlig_text_med_tva_versala_ord_rors_fortfarande_inte():
    """Kontroll att det vidgade mönstret inte börjat svälja rubriker."""
    sida = (
        "Kassans organisation\n"
        "Livsmedelsarbetarnas arbetslöshetskassa har sitt säte i Stockholm.\n"
        "Sveriges a-kassor\n"
        "Ordinarie ledamöter"
    )
    assert find_names_by_role_context([sida]) == set()


# ------------------------------------------------ reserv när ordlistan missar
def test_spanreserven_hittar_det_ordlistan_missar(tmp_path):
    """De två dokument som återstod föll på detta: namnen fanns i termlistan,
    stod läsbara i utdata, och gick ändå inte att placera."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Ordforande", fontsize=11)
    page.insert_text((72, 120), "Anna  Svensson", fontsize=11)  # dubbelt mellanrum
    pdf = tmp_path / "span.pdf"
    doc.save(pdf)
    doc.close()

    with pymupdf.open(pdf) as d:
        sida = d[0]
        assert PDFMasker._locate_term_in_spans(sida, "Anna Svensson")


def test_spanreserven_hittar_inget_som_inte_finns(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Summa tillgangar 63 853", fontsize=11)
    pdf = tmp_path / "ren.pdf"
    doc.save(pdf)
    doc.close()

    with pymupdf.open(pdf) as d:
        assert PDFMasker._locate_term_in_spans(d[0], "Anna Svensson") == []


# ------------------------------------- sidhuvud som ser ut som ett namn
def _pdf_med_banderoll(tmp_path, sidor=30):
    """Varje sida får e-signeringstjänstens banderoll överst, som i
    Unionens årsredovisning, plus en rollrad intill."""
    doc = pymupdf.open()
    for _ in range(sidor):
        page = doc.new_page()
        page.insert_text((72, 60), "TeamEngine E-Signing", fontsize=8)
        page.insert_text((72, 80), "Suppleant", fontsize=11)
        page.insert_text((72, 120), "Summa tillgangar 46 749", fontsize=11)
    ut = tmp_path / "banderoll.pdf"
    doc.save(ut)
    doc.close()
    return ut


def test_en_banderoll_pa_varje_sida_underkanner_inte_dokumentet():
    """Det som kostade Unionens årsredovisning en hel dag.

    "TeamEngine E-Signing" har ett namns form, står intill en rollrad och är
    ingen person. Svärtningen sorterade bort den som för vanlig för att vara
    ett namn. Svepet gjorde det inte, och underkände filen.
    """
    assert find_names_by_role_context(
        ["TeamEngine E-Signing\nSuppleant"]
    ) == {"TeamEngine E-Signing"}, "formen är ett namns, det är inte felet"


def test_svepet_sorterar_bort_det_svartningen_redan_sorterat_bort(tmp_path):
    pdf = _pdf_med_banderoll(tmp_path)
    assert PDFMasker.find_role_names_in_pdf(pdf) == set()


def test_ett_namn_som_star_en_gang_slapps_fortfarande_igenom(tmp_path):
    """Filtret får inte svälja riktiga namn: en styrelseledamot står på en
    sida, inte på trettio."""
    doc = pymupdf.open()
    for n in range(30):
        page = doc.new_page()
        page.insert_text((72, 60), "TeamEngine E-Signing", fontsize=8)
        if n == 0:
            page.insert_text((72, 100), "Nils Akesson", fontsize=11)
            page.insert_text((72, 120), "Suppleant", fontsize=11)
    pdf = tmp_path / "blandat.pdf"
    doc.save(pdf)
    doc.close()

    assert PDFMasker.find_role_names_in_pdf(pdf) == {"Nils Akesson"}


# -------------------------------- delvis träff fick tidigare blockera reserven
def test_bada_sokvagarna_kors_alltid(tmp_path):
    """Felet som underkände GS a-kassa.

    Reserven kördes bara när ordlistan inte hittade någonting alls. En term
    som förekommer flera gånger på en sida kunde därmed bli delvis svärtad:
    ordlistan hittade några förekomster, och eftersom dess lista inte var tom
    frågades reserven aldrig om resten.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Svensson", fontsize=11)
    page.insert_text((72, 130), "Svensson", fontsize=11)
    pdf = tmp_path / "tva.pdf"
    doc.save(pdf)
    doc.close()

    with pymupdf.open(pdf) as d:
        sida = d[0]
        via_ord = PDFMasker._locate_term(sida, "Svensson")
        via_span = PDFMasker._locate_term_in_spans(sida, "Svensson")
        samman = PDFMasker._merge_rects(via_ord, via_span)

    assert len(samman) >= max(len(via_ord), len(via_span)), (
        "unionen får aldrig vara mindre än den bästa enskilda sökvägen"
    )


def test_samma_rektangel_fran_bada_hallen_raknas_en_gang(tmp_path):
    """Hittar båda sökvägarna samma förekomst ska den svärtas en gång."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Svensson", fontsize=11)
    pdf = tmp_path / "en.pdf"
    doc.save(pdf)
    doc.close()

    with pymupdf.open(pdf) as d:
        sida = d[0]
        via_ord = PDFMasker._locate_term(sida, "Svensson")
        samman = PDFMasker._merge_rects(via_ord, via_ord)

    assert len(samman) == len(via_ord)


def test_merge_klarar_tomma_listor():
    assert PDFMasker._merge_rects([], None, []) == []


# --------------------------------- sidans text byggs om en gång, inte per term
def test_sidans_struktur_beraknas_en_gang_per_sida(tmp_path):
    """`get_text("dict")` bygger om hela sidans textstruktur vid varje anrop.
    Reserven anropade den en gång per term: 16,6 av 24 sekunders maskering,
    mot 0,1 för ordlistan som får sin lista färdig."""
    doc = pymupdf.open()
    page = doc.new_page()
    for i, namn in enumerate(("Anna Svensson", "Bertil Nilsson", "Cecilia Ek")):
        page.insert_text((72, 100 + i * 20), namn, fontsize=11)
    pdf = tmp_path / "sida.pdf"
    doc.save(pdf)
    doc.close()

    with pymupdf.open(pdf) as d:
        sida = d[0]
        anrop = []
        original = sida.get_text

        def rakna(*args, **kwargs):
            anrop.append(args[0] if args else "")
            return original(*args, **kwargs)

        sida.get_text = rakna
        lines = PDFMasker._page_lines(sida)
        for namn in ("Anna Svensson", "Bertil Nilsson", "Cecilia Ek"):
            assert PDFMasker._locate_term_in_spans(sida, namn, lines=lines)

        assert anrop.count("dict") == 1, "sidan ska byggas om en gång"


def test_reserven_fungerar_aven_utan_forberedda_rader(tmp_path):
    """Anropas den utan `lines` ska den klara sig själv."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Anna Svensson", fontsize=11)
    pdf = tmp_path / "ensam.pdf"
    doc.save(pdf)
    doc.close()

    with pymupdf.open(pdf) as d:
        assert PDFMasker._locate_term_in_spans(d[0], "Anna Svensson")
