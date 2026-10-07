"""Tester för sammanfattningarna ur förvaltningsberättelsen.

Allt annat verktyget gör är extraktion, där aritmetiken säger till när något
inte går ihop. Det här är generering: ingen kontroll fångar en sammanfattning
som är flytande, rimlig och fel. Kravet på ett ordagrant citat är det enda som
gör en cell kontrollerbar, och testerna här handlar mest om att det kravet
håller.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.src import JBGManagementReport as mr  # noqa: E402

# Ur Livsmedelsarbetarnas årsredovisning 2025.
LIVS = """Livsmedelsarbetarnas a-kassa 1
FÖRVALTNINGSBERÄTTELSE
Styrelsens sammansättning
Ordinarie ledamöter
Kurser och information
Under året har kassans handläggare deltagit i utbildningar och
informationstillfällen både med Sveriges a-kassor och internt. Nytt regelverk
gällande arbetslöshetsförsäkringen trädde i kraft den 1 oktober 2025.
Kassans förväntade framtida utveckling
Livsmedelsarbetarnas arbetslöshetskassa kommer att ha en förhållandevis hög
arbetslöshet även under kommande verksamhetsår. Därtill en osäker
världsekonomi.
Eventualförpliktelser och ställda säkerheter
Kassan har inga ställda säkerheter eller eventualförpliktelser.
""" + "Medlemsantal och statistik enligt bilaga 2. " * 60 + """
Resultaträkning Not 2025 2024
Medlemsavgifter 49 645 47 342
"""


def _svar(**overrides):
    base = {
        topic["key"]: {"sammanfattning": "En sammanfattning.",
                       "citat": "Nytt regelverk gällande "
                                "arbetslöshetsförsäkringen trädde i kraft",
                       "sida": 3}
        for topic in mr.TOPICS
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


# ------------------------------------------------------------- avsnittet
def test_bara_forvaltningsberattelsen_skickas():
    """Att skicka hela årsredovisningen vore dyrare och sämre: modellen skulle
    få resultaträkningen att sammanfatta också."""
    section = mr.extract_section(LIVS)

    assert "Kassans förväntade framtida utveckling" in section
    assert "Medlemsavgifter 49 645" not in section


def test_utan_rubriker_tas_hela_dokumentet():
    text = "En text utan de vanliga rubrikerna. " * 100
    assert mr.extract_section(text) == text[: mr.MAX_SECTION_CHARS]


def test_den_langsta_forekomsten_vinner_over_innehallsforteckningen():
    """GS a-kassa fick ett avsnitt på drygt tusen tecken hämtat ur
    innehållsförteckningen, och alla tre sammanfattningarna kom tillbaka
    tomma."""
    innehall = ("Innehåll\nFörvaltningsberättelse 2\nOm kassan 3\n"
                "Nyckeltal 7\nPersonal 9\n" * 30)
    riktigt = ("FÖRVALTNINGSBERÄTTELSE\nStyrelsen avger härmed ...\n"
               + "Viktiga händelser under året. " * 120)
    text = innehall + "\nResultaträkning 12\n" + riktigt + "\nRESULTATRÄKNING\n"

    vald = mr.extract_section(text)
    assert "Viktiga händelser under året." in vald
    assert not vald.startswith("Förvaltningsberättelse 2")


def test_ett_for_kort_avsnitt_byts_mot_hela_dokumentet():
    """Hellre en dyrare fråga än tre tomma svar."""
    text = "Förvaltningsberättelse\nResultaträkning\n" + "Brödtext. " * 400
    assert len(mr.extract_section(text)) > mr.MIN_SECTION_CHARS


def test_tomt_in_ger_tomt_ut():
    assert mr.extract_section("") == ""


def test_avsnittet_kapas_vid_taket():
    assert len(mr.extract_section("x" * 100000)) == mr.MAX_SECTION_CHARS


# --------------------------------------------------------------- tolkning
def test_ett_fullstandigt_svar_tolkas():
    parsed = mr.parse_response(_svar())
    assert set(parsed) == {topic["key"] for topic in mr.TOPICS}
    assert parsed["handelser"]["sida"] == 3


def test_en_sammanfattning_utan_citat_stryks():
    """Ett citat som saknas gör sammanfattningen okontrollerbar, och då duger
    den inte."""
    parsed = mr.parse_response(_svar(
        handelser={"sammanfattning": "Något hände.", "citat": "", "sida": 4}
    ))
    assert parsed["handelser"]["sammanfattning"] == mr.NOT_STATED
    assert parsed["handelser"]["sida"] == 0


def test_modellens_egen_markering_av_att_uppgift_saknas_respekteras():
    parsed = mr.parse_response(_svar(
        medelsforvaltning={"sammanfattning": mr.NOT_STATED, "citat": "x", "sida": 9}
    ))
    assert parsed["medelsforvaltning"]["citat"] == ""


def test_trasigt_svar_ger_tom_uppslagsbok():
    assert mr.parse_response("{inte json") == {}
    assert mr.parse_response(None) == {}


def test_ett_oläsligt_sidnummer_blir_noll():
    parsed = mr.parse_response(_svar(
        handelser={"sammanfattning": "A", "citat": "b", "sida": "tre"}
    ))
    assert parsed["handelser"]["sida"] == 0


# ------------------------------------------------------- citatet ska finnas
def test_ett_citat_som_star_i_texten_godkanns():
    summaries = mr.parse_response(_svar())
    verdicts = mr.classify_quotes(summaries, LIVS)
    assert verdicts["handelser"][0] == mr.QUOTE_VERIFIED


def test_ett_pahittat_citat_upptacks():
    """Ett citat som inte står i dokumentet är ett påhitt som ser ut som ett
    belägg, och värre än inget citat alls."""
    summaries = mr.parse_response(_svar(
        handelser={"sammanfattning": "Kassan öppnade ett nytt kontor i Kiruna.",
                   "citat": "Under året öppnade kassan ett kontor i Kiruna.",
                   "sida": 2}
    ))
    assert mr.classify_quotes(summaries, LIVS)["handelser"][0] == mr.QUOTE_UNSUPPORTED


def test_radbrytningar_i_citatet_spelar_ingen_roll():
    """Pdf-text bryts på andra ställen än modellen återger."""
    summaries = mr.parse_response(_svar(
        handelser={"sammanfattning": "A",
                   "citat": "kommer att ha en\nförhållandevis hög arbetslöshet",
                   "sida": 3}
    ))
    assert mr.classify_quotes(summaries, LIVS)["handelser"][0] == mr.QUOTE_VERIFIED


def test_tomt_citat_markeras_som_saknat():
    summaries = mr.parse_response(_svar(
        handelser={"sammanfattning": mr.NOT_STATED, "citat": "", "sida": 0}
    ))
    assert mr.classify_quotes(summaries, LIVS)["handelser"][0] == mr.QUOTE_MISSING


# ------------------------------------------------- inskannade dokument
OCR_TEXT = (
    "FORVALTNINGSBERATTELSE\n"
    "Under aret har kassans handlaggare deltagit i utbildningar och "
    "informationstillfallen. Nytt regelverk gallande arbetsloshets-\n"
    "forsakringen tradde i kraft den 1 oktober 2025.\n"
) + "Brodtext om verksamheten. " * 120


def test_modellen_far_stava_ratt_det_tesseract_last_fel():
    """GS a-kassa fick två av tre sammanfattningar strukna trots att de var
    riktiga: tesseract läser "arbetslöshetsförsäkringen" som
    "arbetsloshetsforsakringen", och modellen skriver av det rättstavat."""
    citat = ("Nytt regelverk gällande arbetslöshetsförsäkringen trädde i "
             "kraft den 1 oktober 2025")
    assert mr.quote_support(citat, OCR_TEXT) >= mr.QUOTE_VERIFIED_RATIO


def test_avstavning_vid_radslut_spelar_ingen_roll():
    citat = "gallande arbetsloshetsforsakringen tradde i kraft"
    assert mr.quote_support(citat, OCR_TEXT) >= mr.QUOTE_VERIFIED_RATIO


def test_ett_citat_utan_stod_far_inget_forbarmande():
    citat = "Kassan oppnade under aret ett nytt kontor i Kiruna."
    assert mr.quote_support(citat, OCR_TEXT) < mr.QUOTE_UNSUPPORTED_RATIO


def test_halvt_pahittat_raknas_inte_som_aterfunnet():
    citat = ("Nytt regelverk tradde i kraft och kassan oppnade kontor i "
             "Kiruna under hosten")
    assert mr.quote_support(citat, OCR_TEXT) < mr.QUOTE_VERIFIED_RATIO


# ----------------------------------------------------------------- schemat
def test_schemat_kraver_alla_tre_fragorna():
    required = mr.RESPONSE_SCHEMA["schema"]["required"]
    assert required == [topic["key"] for topic in mr.TOPICS]
    for topic in mr.TOPICS:
        fields = mr.RESPONSE_SCHEMA["schema"]["properties"][topic["key"]]
        assert fields["required"] == ["sammanfattning", "citat", "sida"]


def test_fragorna_namns_i_anropet():
    request = mr.build_request("texten")
    for topic in mr.TOPICS:
        assert topic["key"] in request
    assert "texten" in request


@pytest.mark.parametrize("topic", mr.TOPICS)
def test_varje_rubrik_har_en_fraga(topic):
    assert topic["heading"] and topic["question"]
