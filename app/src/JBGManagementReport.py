"""Sammanfattningar ur förvaltningsberättelsen.

Allt annat verktyget gör är extraktion: en siffra hämtas, sidan antecknas, och
aritmetiken säger till när något inte går ihop. Det här är generering, och
skillnaden är inte akademisk. Ingen kontroll fångar en sammanfattning som är
flytande, rimlig och fel, och sådana är svårare att upptäcka än ett felaktigt
tal, eftersom de inte sticker ut mot någonting.

Därför kravet på stöd. Varje sammanfattning ska följas av en ordagrann mening
ur dokumentet och sidan den står på. En cell med citat går att kontrollera på
tio sekunder; en cell utan går inte att kontrollera alls. Saknas stöd ska
cellen läsa att uppgiften inte framgår, inte innehålla något rimligt.

Den tredje frågan, om medelsförvaltningen, väntas bli ett krav först nästa år.
Tomma celler är därför det väntade utfallet för 2025 — men de ska vara tomma
av rätt skäl.
"""

import json
import logging
import re
import unicodedata
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

NOT_STATED = "Framgår inte av årsredovisningen"

# Hur citatet förhåller sig till texten det påstås komma ur.
QUOTE_VERIFIED = "återfunnet"
QUOTE_APPROXIMATE = "ungefärligt"
QUOTE_UNSUPPORTED = "utan stöd"
QUOTE_MISSING = "saknas"

# Rubrikerna i den ordning de ska stå i bladet.
TOPICS = (
    {
        "key": "handelser",
        "heading": "Årets viktigaste händelser",
        "question": (
            "Vilka är de viktigaste händelserna under året enligt "
            "förvaltningsberättelsen?"
        ),
    },
    {
        "key": "utveckling",
        "heading": "Förväntad framtida utveckling och arbetslöshetsnivå",
        "question": (
            "Vad säger kassan om den förväntade framtida utvecklingen och om "
            "arbetslöshetsnivån nästkommande år?"
        ),
    },
    {
        "key": "medelsforvaltning",
        "heading": "Kontroll av medelsförvaltningen",
        "question": (
            "Hur beskriver kassan att den säkerställt en tillfredsställande "
            "kontroll av medelsförvaltningen?"
        ),
    },
)

SYSTEM_PROMPT = """Du sammanfattar delar av en svensk a-kassas förvaltningsberättelse.

Regler som gäller utan undantag:

1. Sammanfatta bara det som står i texten. Lägg inte till slutsatser, omdömen
   eller sådant du vet om branschen i övrigt.
2. Varje sammanfattning ska åtföljas av ett ordagrant citat ur texten som
   stöder den, samt sidnumret citatet står på. Citatet ska vara en
   sammanhängande mening eller del av mening, kopierad exakt.
3. Finns inget stöd i texten för en fråga: sätt sammanfattningen till
   "{not_stated}", lämna citatet tomt och sidan till 0. Hitta inte på.
4. Skriv på svenska, i löpande text, tre till fem meningar per sammanfattning.
5. Texten är maskerad: namn på personer är borttagna. Nämn inte detta och
   försök inte gissa vilka de var."""

RESPONSE_SCHEMA = {
    "name": "forvaltningsberattelse",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [topic["key"] for topic in TOPICS],
        "properties": {
            topic["key"]: {
                "type": "object",
                "additionalProperties": False,
                "required": ["sammanfattning", "citat", "sida"],
                "properties": {
                    "sammanfattning": {"type": "string"},
                    "citat": {"type": "string"},
                    "sida": {"type": "integer"},
                },
            }
            for topic in TOPICS
        },
    },
}

# Avsnittet börjar vid rubriken och slutar där räkningarna tar vid.
SECTION_START = re.compile(r"\bf[öo]rvaltningsber[äa]ttelse\b", re.IGNORECASE)
SECTION_END = re.compile(
    r"\b(?:resultatr[äa]kning|balansr[äa]kning|noter\s+till)\b", re.IGNORECASE
)
# Nog för en förvaltningsberättelse, och ett tak mot att hela dokumentet
# skickas när rubrikerna inte går att hitta.
MAX_SECTION_CHARS = 40000
# En förvaltningsberättelse är längre än så här. Kortare betyder att rubriken
# hittades någon annanstans - oftast i innehållsförteckningen, där nästa rad
# redan är "Resultaträkning".
MIN_SECTION_CHARS = 2000


def extract_section(text: str) -> str:
    """Förvaltningsberättelsen, om den går att hitta, annars hela dokumentet.

    Att skicka hela årsredovisningen vore både dyrare och sämre: modellen
    skulle få resultaträkningen och noterna att sammanfatta också, och frågan
    gäller inte dem.

    Rubriken förekommer oftast flera gånger - en gång i innehållsförteckningen
    och en gång där avsnittet faktiskt börjar - så varje förekomst prövas och
    den längsta vinner. Att ta den första gav GS a-kassa ett avsnitt på drygt
    tusen tecken hämtat ur innehållsförteckningen, och alla tre
    sammanfattningarna kom tillbaka tomma.
    """
    if not text:
        return ""

    candidates = []
    for start in SECTION_START.finditer(text):
        rest = text[start.start():]
        end = SECTION_END.search(rest, 1)
        candidates.append(rest[: end.start()] if end else rest)

    best = max(candidates, key=len) if candidates else ""
    if len(best) < MIN_SECTION_CHARS:
        # Rubrikerna stod tätt, troligen i innehållsförteckningen. Hellre hela
        # dokumentet än nästan ingenting: en dyrare fråga är bättre än tre
        # tomma svar.
        logger.info(
            f"Förvaltningsberättelsen gick inte att avgränsa ({len(best)} "
            "tecken). Skickar hela dokumentet i stället."
        )
        best = text
    return best[:MAX_SECTION_CHARS]


def build_request(section: str) -> str:
    questions = "\n".join(
        f"{index}. {topic['question']} (nyckel: {topic['key']})"
        for index, topic in enumerate(TOPICS, start=1)
    )
    return (
        f"Besvara följande frågor utifrån texten nedan.\n\n{questions}\n\n"
        f"--- FÖRVALTNINGSBERÄTTELSE ---\n{section}"
    )


def parse_response(raw: str) -> dict:
    """Svaret som en uppslagsbok, med tomma fält där stöd saknas.

    Ett citat som inte finns är allvarligare än ett som saknas: det förra ser
    ut som belägg. Saknas citat nollställs därför även sammanfattningen.
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as ex:
        logger.warning(f"Kunde inte tolka sammanfattningen: {ex}")
        return {}

    result = {}
    for topic in TOPICS:
        entry = parsed.get(topic["key"]) or {}
        summary = (entry.get("sammanfattning") or "").strip()
        quote = (entry.get("citat") or "").strip()
        try:
            page = int(entry.get("sida") or 0)
        except (TypeError, ValueError):
            page = 0

        if not quote or summary == NOT_STATED:
            summary, quote, page = NOT_STATED, "", 0
        result[topic["key"]] = {
            "sammanfattning": summary,
            "citat": quote,
            "sida": page,
        }
    return result


# Hur stor del av citatet som måste återfinnas sammanhängande i texten.
# Ordagrant på ett digitalt dokument, men en OCR-tolkad sida innehåller
# felläsningar som modellen städar bort när den citerar, och då stämmer inte
# tecknen även om meningen är rätt.
QUOTE_VERIFIED_RATIO = 0.85
# Under den här nivån finns inget stöd att tala om, och då är citatet inte en
# städad avskrift utan något annat.
QUOTE_UNSUPPORTED_RATIO = 0.5


def quote_support(quote: str, section: str) -> float:
    """Hur stor andel av citatet som går att återfinna sammanhängande.

    1,0 betyder ordagrant. En OCR-tolkad text ger lägre värden utan att
    citatet för den skull är påhittat: tesseract läser "arbetslöshetskassan"
    som "arbetsloshetskassan" och modellen skriver av det rättstavat.
    """
    needle, haystack = _normalise(quote), _normalise(section)
    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0
    match = SequenceMatcher(None, needle, haystack, autojunk=False).find_longest_match(
        0, len(needle), 0, len(haystack)
    )
    return match.size / len(needle)


def classify_quotes(summaries: dict, section: str) -> dict:
    """Bedöm varje citat: återfunnet, ungefärligt eller utan stöd.

    Tidigare ströks allt som inte gick att återfinna ordagrant, och på ett
    inskannat dokument innebar det att två av tre sammanfattningar
    försvann trots att de var riktiga. Att kasta bort uppgiften är inte
    försiktigt, det är bara tomt. Ett citat som nästan stämmer behålls därför
    och märks, så att läsaren kan bedöma det själv.
    """
    verdicts = {}
    for topic in TOPICS:
        entry = summaries.get(topic["key"]) or {}
        quote = entry.get("citat") or ""
        if not quote:
            verdicts[topic["key"]] = (QUOTE_MISSING, 0.0)
            continue
        ratio = quote_support(quote, section)
        if ratio >= QUOTE_VERIFIED_RATIO:
            verdicts[topic["key"]] = (QUOTE_VERIFIED, ratio)
        elif ratio >= QUOTE_UNSUPPORTED_RATIO:
            verdicts[topic["key"]] = (QUOTE_APPROXIMATE, ratio)
        else:
            verdicts[topic["key"]] = (QUOTE_UNSUPPORTED, ratio)
    return verdicts


def _normalise(text: str) -> str:
    """Bara bokstäver och siffror, utan diakriter.

    Tre skillnader står för nästan allt som skiljer ett citat från texten det
    kommer ur, och ingen av dem handlar om innehållet. Radbrytningar i pdf:en
    hamnar på andra ställen än i modellens avskrift. Avstavningen vid radslut
    finns i texten men inte i citatet. Och tesseract läser "arbetslöshets-
    kassan" som "arbetsloshetskassan", varpå modellen skriver av det
    rättstavat. Allt tre försvinner om jämförelsen görs på bokstäverna.
    """
    folded = unicodedata.normalize("NFKD", text or "")
    letters = [
        char.casefold()
        for char in folded
        if char.isalnum() and not unicodedata.combining(char)
    ]
    return "".join(letters)
