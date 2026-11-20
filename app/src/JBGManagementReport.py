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

Frågan om kontroll av medelsförvaltningen väntas bli ett krav först nästa
år. En annan fråga gäller kassans finansiella placeringar, inklusive
bankmedel. En uppgift ska alltid gå att kontrollera mot förvaltningsberättelsen.
"""

import json
import logging
import re
import unicodedata
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

# Formuleringen påstod tidigare "Framgår inte av årsredovisningen", vilket
# säger mer än vi vet. Granskaren satte fingret på det: tomma celler kan lika
# gärna betyda att modellen inte hittade uppgiften som att den saknas. För
# Pappersindustriarbetarnas stod cellen tom trots att berättelsen diskuterar
# varsel, framtida arbetsbelastning och branschrisker.
NOT_STATED = "Ingen uppgift hittad"
# Den nya frågan skiljer mellan att inget nämns och att avsnittet inte gick
# att avgränsa från noter och balansräkning.
NOT_MENTIONED = "Nämns inte"
NOT_ASSESSABLE = "Kan inte bedömas"
NOT_VERIFIED = "Ingen verifierbar uppgift"
PLACEMENTS_KEY = "finansiella_placeringar"

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
            "arbetslöshetsnivån nästkommande år? Räkna hit allt som berör "
            "det: varsel och uppsägningar, läget i branschen eller på "
            "arbetsmarknaden, konjunktur och sysselsättning, väntad "
            "ärendemängd eller arbetsbelastning, och medlemsutvecklingen. "
            "Kassan behöver inte använda orden \"framtida utveckling\" för "
            "att det ska räknas."
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
    {
        "key": PLACEMENTS_KEY,
        "heading": "Finansiella placeringar",
        "question": (
            "Nämner kassan något om sina finansiella tillgångar eller sin "
            "förvaltning av finansiella medel? Sök även efter långfristiga "
            "och kortfristiga finansiella placeringar, placeringar, "
            "värdepappersinnehav, fonder, likvida medel, bankmedel, "
            "banktillgodohavanden och närliggande formuleringar. "
            "Vad säger kassan konkret om innehav, fördelning, förändringar "
            "eller avsaknad av sådana tillgångar?"
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
   "{not_stated}" (utom för finansiella_placeringar, se punkt 7),
   lämna citatet tomt och sidan till 0. Hitta inte på.
   Leta dock ordentligt först. Kassorna skriver sällan under de rubriker
   frågan använder, och det som efterfrågas står ofta utspritt i
   berättelsen snarare än samlat under en egen rubrik.
4. Skriv på svenska, i löpande text, tre till fem meningar per sammanfattning,
   utom för finansiella_placeringar där en enda kort mening helst räcker.
5. Texten är maskerad: namn på personer är borttagna. Nämn inte detta och
   försök inte gissa vilka de var.
6. Sammanfatta det kassan faktiskt skriver, inte vad den borde ha skrivit.
   Att beskriva osäkerhet om framtiden är inte samma sak som att lämna en
   prognos; återge det som står och lägg inte till en bedömning av om det
   räcker.
7. För finansiella_placeringar: räkna en uttrycklig uppgift om att kassan
   INTE har placeringar som ett omnämnande, och återge den. Skilj på faktiska
   värdepappersplaceringar/fonder och banktillgodohavanden/likvida medel:
   att kassan har pengar på banken innebär inte att den äger värdepapper.
   Ordet placeringar om exempelvis personal eller arbetsmarknadsinsatser
   räknas inte. Hämta inte uppgifter från balansräkning eller noter.
   Begränsa sammanfattningen till de fakta som framgår av just det angivna
   stödcitatet. Om ett citat inte styrker flera olika uppgifter, ta bara med
   en av dem i sammanfattningen. Skriv inga belopp eller innehav som inte
   framgår av citatet. Kopiera sidnumret från en [Sida N]-markör.
   Om inget relevant nämns, skriv "{not_mentioned}" utan citat och med sida 0.
   Påstå aldrig att placeringar saknas bara för att de inte omnämns."""

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

# Kräv en rubrik på egen rad. En hänvisning i löptexten till en
# förvaltningsberättelse eller resultaträkning får inte avgränsa avsnittet.
SECTION_START = re.compile(
    r"(?im)^[ \t]*(?:styrelsens[ \t]+)?f[öo]rvaltningsber[äa]ttels(?:e|en)"
    r"(?:[ \t]+(?:20\d{2}|\d{1,2}))?[ \t]*$"
)
SECTION_END = re.compile(
    r"(?im)^[ \t]*(?:(?:resultatr[äa]kning|balansr[äa]kning)\b|"
    r"resultat[ \t]*-[ \t]*och[ \t]+balansr[äa]kning\b|"
    r"noter[ \t]+till\b|till[äa]ggsupplysningar\b)"
)
PAGE_MARKER = re.compile(r"(?m)^\[Sida[ \t]+(-?\d+)\][ \t]*$")
MAX_SECTION_CHARS = 40000
# Kortare årsredovisningar kan ha under 2 000 tecken i berättelsen. Skydda
# istället mot innehållsförteckningar genom att granska deras uppställning.
MIN_SECTION_CHARS = 250


def _is_contents_page(section: str) -> bool:
    """Avfärda en rubrik i innehållsförteckningen, inte kort brödtext."""
    lines = [
        line.strip() for line in section.splitlines()
        if line.strip() and not PAGE_MARKER.fullmatch(line.strip())
    ][:24]
    if len(lines) < 4:
        return False
    page_references = sum(
        bool(re.search(r"(?:\.{2,}|[ \t])\d{1,3}$", line)) for line in lines
    )
    narrative = sum(bool(re.search(r"[.!?](?:[ \t]*$)", line)) for line in lines)
    return page_references >= 3 and page_references >= len(lines) * 0.35 and narrative <= 1


def extract_section(text: str, *, strict: bool = False) -> str:
    """Extrahera förvaltningsberättelsen mellan två verkliga rubriker.

    strict=True kräver en slutrubrik och ett fullständigt, rimligt avsnitt.
    De tre ursprungliga frågorna får oförändrat hela dokumentet som fallback.
    Sidmarkören framför rubriken behålls så att citat på första sidan kan
    kopplas till ett faktiskt sidnummer.
    """
    if not text:
        return ""

    markers = list(PAGE_MARKER.finditer(text))
    candidates = []
    for start in SECTION_START.finditer(text):
        end = SECTION_END.search(text, start.end())
        if strict and end is None:
            continue
        # Om slutrubrik saknas kan enbart legacy-frågorna använda utdraget.
        body = text[start.start():end.start() if end else len(text)]
        if len(body) < MIN_SECTION_CHARS or _is_contents_page(body):
            continue
        if strict and len(body) > MAX_SECTION_CHARS:
            continue

        prefix = ""
        for marker in reversed(markers):
            if marker.start() < start.start():
                prefix = marker.group(0) + "\n"
                break
        candidates.append(prefix + body)

    if candidates:
        # De faktiska berättelserna är vanligen längre än TOC-träffarna.
        best = max(candidates, key=len)
        return best[:MAX_SECTION_CHARS] if not strict else best

    if strict:
        return ""
    logger.info(
        "Förvaltningsberättelsen gick inte att avgränsa. "
        "Skickar hela dokumentet i stället."
    )
    return text[:MAX_SECTION_CHARS]

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

        if topic["key"] == PLACEMENTS_KEY:
            if summary in (NOT_STATED, NOT_MENTIONED, ""):
                summary, quote, page = NOT_MENTIONED, "", 0
            elif not quote:
                # Modellen påstår sig ha hittat något men saknar belägg.
                summary, quote, page = NOT_VERIFIED, "", 0
        elif not quote or summary == NOT_STATED:
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

# En riktad andra kontroll görs endast om det finns ord som faktiskt kan
# syfta på finansiella tillgångar i det strikt avgränsade avsnittet.
PLACEMENT_TERMS = re.compile(
    r"\b(?:kapital\s*-?\s*placering\w*|finansiell\w*\s+placering\w*|"
    r"v[äa]rdepapper\w*|v[äa]rdepappersinnehav\w*|"
    r"r[äa]ntefond\w*|aktiefond\w*|fonder\b|fondinnehav\w*|"
    r"likvida\s+medel|bankmedel\w*|banktillgodohavand\w*|"
    r"bankkonton?\b|kassa\s+och\s+bank|"
    r"kapitalf[öo]rvaltning\w*|placeringspolicy\w*|"
    r"placeringsreglemente\w*|placeringsportf[öo]lj\w*|"
    r"placering\w*|r[äa]nteb[äa]rande\s+(?:tillg[åa]ngar|papper))\b",
    re.IGNORECASE,
)
NON_FINANCIAL_PLACEMENT = re.compile(
    r"\b(?:personal|anst[äa]ll|praktik|arbetsmarknad|arbetss[öo]kand|"
    r"elever|studenter|jobbcoach|bemanning)\w*\b",
    re.IGNORECASE,
)

PLACEMENT_RECHECK_SCHEMA = {
    "name": "finansiella_placeringar_omkontroll",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["sammanfattning", "citat", "sida"],
        "properties": RESPONSE_SCHEMA["schema"]["properties"][PLACEMENTS_KEY]["properties"],
    },
}

PLACEMENT_RECHECK_PROMPT = """Du gör en oberoende kontroll av en enda fråga ur en
svensk a-kassas förvaltningsberättelse. Underlaget består BARA av utdrag
ur förvaltningsberättelsen. Varje utdrag har sidmarkören [Sida N].

Fråga: Nämner texten finansiella placeringar, fonder, värdepapper,
kapitalförvaltning, bankmedel, likvida medel eller liknande? Allmänna
personalplaceringar och arbetsmarknadsinsatser är inte relevant.

Om ja: ge en enda kort mening som endast återger vad ett valt, ordagrant
stödcitat direkt säger, kopiera det exakta citatet och dess [Sida N].
Om inga relevanta uppgifter finns: returnera "Nämns inte", citat "", sida 0.
Hitta inte på uppgifter. Använd inte balansräkning eller noter."""


def _numbered_pages(section: str) -> list[tuple[int, str]]:
    markers = list(PAGE_MARKER.finditer(section or ""))
    return [
        (int(marker.group(1)), section[marker.end():
                                        markers[i + 1].start() if i + 1 < len(markers)
                                        else len(section)])
        for i, marker in enumerate(markers)
    ]


def placement_candidate_passages(section: str, *, limit: int = 12) -> list[str]:
    """Korta, sidsatta textrader för en eventuell andra AI-kontroll.

    En träff är en anledning att kontrollera, inte ett bevis på placeringar.
    Utan sidmarkörer görs ingen ny AI-kontroll: då kan resultatet ändå inte
    verifieras mot rätt sida.
    """
    passages = []
    seen = set()
    for page, body in _numbered_pages(section):
        page_hits = 0
        # PDF-radbrytningar och avstavningar får inte förstöra sökorden.
        compact = re.sub(r"(?<=\w)-\s+(?=\w)", "", body)
        compact = re.sub(r"\s+", " ", compact)
        for match in PLACEMENT_TERMS.finditer(compact):
            left = max(0, match.start() - 180)
            right = min(len(compact), match.end() + 250)
            context = compact[left:right].strip()
            if match.group().casefold().startswith("placering") and (
                NON_FINANCIAL_PLACEMENT.search(context)
                and not re.search(r"\b(?:kapital|finansiell|tillg[åa]ng|"
                                  r"fond|bank|v[äa]rdepapper|r[äa]nte)\w*\b", context,
                                  re.IGNORECASE)
            ):
                continue
            token = (page, _normalise(context))
            if token in seen:
                continue
            seen.add(token)
            passages.append(f"[Sida {page}]\n{context}")
            page_hits += 1
            if len(passages) >= limit:
                return passages
            if page_hits >= 3:
                break
    return passages


def build_placement_recheck_request(passages: list[str]) -> str:
    return (
        "Kontrollera enbart dessa träffar i förvaltningsberättelsen. "
        "En ordträff behöver inte vara ett verkligt omnämnande.\n\n"
        + "\n\n---\n\n".join(passages)
    )


def parse_placement_recheck(raw: str) -> dict:
    try:
        entry = json.loads(raw)
        if not isinstance(entry, dict):
            return {}
        # Återanvänd samma normalisering och statusregler som första anropet.
        result = parse_response(json.dumps({PLACEMENTS_KEY: entry}, ensure_ascii=False))
        return result.get(PLACEMENTS_KEY, {})
    except (ValueError, TypeError, AttributeError):
        return {}


def verify_placement_page(entry: dict, section: str) -> dict:
    """Kräv att citatet återfinns på en bestämd sida i berättelsen.

    Rätta modellens sidnummer när endast en sida ger stöd. Ett citat som
    förekommer på flera sidor utan entydigt sidnummer godkänns inte.
    Denna kontroll verifierar citatets existens, inte hela sammanfattningens
    semantiska riktighet (därför begränsar prompten dess omfattning).
    """
    result = dict(entry)
    summary = result.get("sammanfattning", "")
    quote = result.get("citat") or ""
    if summary in (NOT_MENTIONED, NOT_ASSESSABLE, NOT_VERIFIED):
        result.update({"citat": "", "sida": 0})
        return result
    if not quote:
        result.update({"sammanfattning": NOT_VERIFIED, "citat": "", "sida": 0,
                       "citat_kontroll": QUOTE_MISSING, "citat_stod": 0.0})
        return result

    matches = []
    for page, body in _numbered_pages(section):
        if page <= 0:
            continue
        score = quote_support(quote, body)
        if score >= QUOTE_UNSUPPORTED_RATIO:
            matches.append((page, score))

    # Citatet måste ha tillräckligt stöd på samma sida som anges. Om den
    # angivna sidan inte stämmer kan en enda entydig träff rätta den.
    given = result.get("sida") or 0
    selected = next(((p, score) for p, score in matches if p == given), None)
    if selected is None and len(matches) == 1:
        selected = matches[0]
    if selected is None:
        result.update({"sammanfattning": NOT_VERIFIED, "citat": "", "sida": 0,
                       "citat_kontroll": QUOTE_UNSUPPORTED, "citat_stod": 0.0})
        return result

    page, score = selected
    if page != given:
        result["sidnummer_korrigerat"] = True
    result["sida"] = page
    result["citat_kontroll"] = (
        QUOTE_VERIFIED if score >= QUOTE_VERIFIED_RATIO else QUOTE_APPROXIMATE
    )
    result["citat_stod"] = round(score, 2)
    return result
