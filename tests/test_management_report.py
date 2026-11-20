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
def test_schemat_kraver_alla_fyra_fragorna():
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


# --------------------------------------- finansiella placeringar
PLACERINGSBERATTELSE = (
    "[Sida 3]\nFÖRVALTNINGSBERÄTTELSE\n"
    "Kassans bankmedel uppgick vid årets slut till 20 miljoner kronor.\n"
    "Kassan saknar långfristiga värdepappersinnehav.\n"
    + "Verksamheten följer årets plan. " * 90
    + "\nRESULTATRÄKNING\nKassa och bank 20000\n"
)


def test_placeringar_ar_fjarde_fraga_utan_att_andra_kolumner_flyttas():
    assert [topic["key"] for topic in mr.TOPICS] == [
        "handelser", "utveckling", "medelsforvaltning", "finansiella_placeringar"
    ]
    assert mr.TOPICS[-1]["heading"] == "Finansiella placeringar"


def test_placeringsfragan_omfattar_bankmedel_fonder_och_avsaknad():
    question = mr.TOPICS[-1]["question"].lower()
    for term in ("långfristiga", "kortfristiga", "värdepappersinnehav",
                 "fonder", "likvida medel", "bankmedel"):
        assert term in question
    prompt = mr.SYSTEM_PROMPT.format(
        not_stated=mr.NOT_STATED, not_mentioned=mr.NOT_MENTIONED
    )
    assert "banktillgodohavanden" in prompt
    assert "INTE har placeringar" in prompt
    assert "arbetsmarknadsinsatser" in prompt
    assert "en enda kort mening" in prompt


def test_placeringar_utan_citat_markeras_som_icke_verifierbara():
    parsed = mr.parse_response(_svar(
        finansiella_placeringar={
            "sammanfattning": "Inga placeringar finns.", "citat": "", "sida": 7,
        }
    ))
    assert parsed["finansiella_placeringar"] == {
        "sammanfattning": mr.NOT_VERIFIED, "citat": "", "sida": 0,
    }


def test_modellens_namns_inte_normaliseras_utan_citat():
    parsed = mr.parse_response(_svar(
        finansiella_placeringar={
            "sammanfattning": mr.NOT_MENTIONED, "citat": "någon mening", "sida": 4,
        }
    ))
    assert parsed["finansiella_placeringar"]["citat"] == ""
    assert parsed["finansiella_placeringar"]["sida"] == 0


def test_uttryckligt_saknat_vardepappersinnehav_ar_ett_omnamnande():
    parsed = mr.parse_response(_svar(
        finansiella_placeringar={
            "sammanfattning": "Kassan saknar långfristiga värdepappersinnehav.",
            "citat": "Kassan saknar långfristiga värdepappersinnehav.",
            "sida": 3,
        }
    ))
    entry = parsed["finansiella_placeringar"]
    assert entry["sammanfattning"].startswith("Kassan saknar")
    assert mr.classify_quotes(parsed, PLACERINGSBERATTELSE)[
        "finansiella_placeringar"
    ][0] == mr.QUOTE_VERIFIED


def test_strikt_avgransning_nekar_fallback_till_noter():
    text = (
        "Årsredovisning\nFörvaltningsberättelse\nResultaträkning\n"
        "Not 5. Kortfristiga finansiella placeringar: 300 kronor. " * 40
    )
    assert mr.extract_section(text, strict=True) == ""
    assert "finansiella placeringar" in mr.extract_section(text)


def test_strikt_avgransning_kraver_slutrubrik():
    text = (
        "[Sida 3]\nFörvaltningsberättelse\n"
        + "Kassans verksamhet fortsätter. " * 100
        + "\n[ Sida 5 ] Not 4: Fondinnehav och bankmedel 1 mkr."
    )
    assert mr.extract_section(text, strict=True) == ""


def test_strikt_avgransning_kan_hitta_riktig_forvaltningsberattelse():
    section = mr.extract_section(PLACERINGSBERATTELSE, strict=True)
    assert "Kassans bankmedel" in section
    assert "Kassa och bank 20000" not in section

# ----------------------------------------------------- kvalitetspatch 2

def test_kort_men_verklig_forvaltningsberattelse_godkanns_strikt():
    text = (
        "[Sida 2]\nInnehåll\nFörvaltningsberättelse 3\nResultaträkning 8\n"
        "[Sida 3]\nFÖRVALTNINGSBERÄTTELSE\n"
        "Kassan har placerat medel i aktiefonder. "
        + "Året präglades av god handläggning. " * 19
        + "\n[Sida 4]\nRESULTATRÄKNING\nKassa och bank 9000\n"
    )
    section = mr.extract_section(text, strict=True)
    assert 250 < len(section) < 2000
    assert section.startswith("[Sida 3]")
    assert "aktiefonder" in section
    assert "Kassa och bank 9000" not in section


def test_lang_innehallsforteckning_fortranger_inte_kort_berattelse():
    toc = ("[Sida 1]\nINNEHÅLL\nFörvaltningsberättelse\n"
           + "Viktiga händelser 2\nMedlemmar 3\nVerksamhet 4\n" * 12
           + "Resultaträkning 14\n")
    body = ("[Sida 2]\nFörvaltningsberättelse\n"
            + "Kassan har bankmedel, men inga aktier. " * 15
            + "\n[Sida 3]\nResultaträkning\n")
    found = mr.extract_section(toc + body, strict=True)
    assert "Kassan har bankmedel" in found
    assert "Viktiga händelser 2" not in found


def test_hanvisning_i_loptext_ar_inte_slutrubrik():
    text = ("[Sida 2]\nFörvaltningsberättelse\n"
            + "I resultaträkningen har ett överskott bokförts.\n"
            + "Kassans placeringar redovisas enligt ett reglemente. " * 12
            + "\n[Sida 3]\nResultaträkning\n")
    section = mr.extract_section(text, strict=True)
    assert "Kassans placeringar" in section


def test_verifiering_reparerar_sidnummer_från_citat_pa_forsta_sidan():
    section = ("[Sida 4]\nFörvaltningsberättelse\n"
               "Kassan placerar kapital i räntefonder.\n"
               + "Övrigt om verksamheten. " * 25
               + "\n[Sida 5]\nResultaträkning\n")
    entry = {"sammanfattning": "Kassan har räntefonder.",
             "citat": "Kassan placerar kapital i räntefonder.", "sida": 0}
    checked = mr.verify_placement_page(entry, mr.extract_section(section, strict=True))
    assert checked["sida"] == 4
    assert checked["sidnummer_korrigerat"] is True
    assert checked["citat_kontroll"] == mr.QUOTE_VERIFIED


def test_fel_sidnummer_rattas_men_citat_som_saknar_sidstod_stryks():
    section = ("[Sida 3]\nFörvaltningsberättelse\n"
               "Vi har bankmedel på sparkonto.\n"
               "[Sida 4]\nKassans verksamhet utvecklas.\n")
    entry = {"sammanfattning": "Bankmedel på sparkonto.",
             "citat": "Vi har bankmedel på sparkonto.", "sida": 99}
    assert mr.verify_placement_page(entry, section)["sida"] == 3
    entry["citat"] = "Kassan köpte aktier för 50 miljoner kronor."
    checked = mr.verify_placement_page(entry, section)
    assert checked["sammanfattning"] == mr.NOT_VERIFIED
    assert checked["sida"] == 0
    assert not checked["citat"]


def test_okant_sidnummer_och_samma_citat_pa_flera_sidor_ar_inte_verifierat():
    quote = "Kassan har tillgångar på bankkonto."
    section = ("[Sida 3]\nFörvaltningsberättelse\n" + quote + "\n"
               "[Sida 4]\n" + quote + "\n")
    entry = {"sammanfattning": quote, "citat": quote, "sida": 0}
    assert mr.verify_placement_page(entry, section)["sammanfattning"] == mr.NOT_VERIFIED
    entry["sida"] = 4
    assert mr.verify_placement_page(entry, section)["sida"] == 4


def test_utan_sidmarkorer_kan_placeringar_inte_bekraftas():
    entry = {"sammanfattning": "Kassan har likvida medel.",
             "citat": "Kassan har likvida medel.", "sida": 7}
    assert mr.verify_placement_page(entry, "Kassan har likvida medel.")[
        "sammanfattning"
    ] == mr.NOT_VERIFIED


def test_riktade_traf_far_bara_sidmarkta_relevanta_utdrag():
    section = ("[Sida 2]\nFörvaltningsberättelse\n"
               "Personalens placeringar i utbildningsgrupperna följs upp.\n"
               "[Sida 3]\nKassans banktillgodohavanden har ökat.\n")
    candidates = mr.placement_candidate_passages(section)
    assert len(candidates) == 1
    assert candidates[0].startswith("[Sida 3]")
    assert "banktillgodohavanden" in candidates[0]


def test_riktad_omkontroll_har_ett_fjarde_amne_utan_att_andra_schemas():
    schema = mr.PLACEMENT_RECHECK_SCHEMA["schema"]
    assert schema["required"] == ["sammanfattning", "citat", "sida"]
    payload = json.dumps({"sammanfattning": mr.NOT_MENTIONED,
                          "citat": "", "sida": 0})
    parsed = mr.parse_placement_recheck(payload)
    assert parsed["sammanfattning"] == mr.NOT_MENTIONED
    assert mr.parse_placement_recheck("not json") == {}
