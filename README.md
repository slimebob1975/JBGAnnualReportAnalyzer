# JBGAnnualReportAnalyzer

En FastAPI-baserad webbtjänst som extraherar nyckeltal ur svenska
arbetslöshetskassors årsredovisningar med hjälp av en språkmodell.

## Funktioner

* Ladda upp en PDF eller en ZIP-fil med flera årsredovisningar.
* Maskera personnamn och andra känsliga uppgifter innan texten skickas vidare.
* Extrahera de poster som anges i IAF:s föreskrift IAFFS 2026:1, bilaga 1
  (resultaträkning, balansräkning och noterna 1–10) samt bilaga 2, med
  sidhänvisning, säkerhetsbedömning och motivering för varje värde.
  Redovisningsprinciperna ingår inte, och poster som en kassa lägger till
  utöver föreskriften rapporteras inte. Endast innevarande räkenskapsår.
* Rimlighetskontroller som flaggar värden som inte går ihop aritmetiskt.
* Resultat som JSON, CSV eller Excel, där Excel färgkodas efter modellens
  angivna säkerhet.
* Körs som bakgrundsjobb med löpande statusuppdatering i webbgränssnittet.

## Krav

* Python 3.10 eller senare.
* En OpenAI API-nyckel, som anges i formuläret.
* För maskning: `transformers` **och** `torch` (ca 2 GB). Båda behövs:
  transformers importeras utan backend och fallerar först när modellen ska
  köras. `/health` visar vilket paket som saknas.
* För OCR av inskannade dokument: `ocrmypdf` plus systempaketen
  `tesseract-ocr`, `tesseract-ocr-swe` och `ghostscript`. Dessa installeras
  **inte** av pip. Saknas de hoppas inskannade rapporter över, och `/health`
  talar om varför.

  * **Windows:** installeraren för tesseract finns på
    <https://github.com/UB-Mannheim/tesseract/wiki> – kryssa i språkpaketet
    *Swedish* under installationen och lägg installationskatalogen i `PATH`.
    Ghostscript finns på <https://ghostscript.com/releases/gsdnld.html>.
  * **Debian/Ubuntu:** `apt install tesseract-ocr tesseract-ocr-swe ghostscript`
  * **macOS:** `brew install tesseract tesseract-lang ghostscript`

  Kontrollera efter installationen att båda finns i `PATH`:

  ```
  tesseract --list-langs      # ska innehålla "swe"
  gs --version
  ```

  I Docker-avbilden ingår allt detta redan. På Windows sköts det av
  `scripts\\Ensure-OcrTools.ps1`, som startskriptet anropar. Det installerar
  det som saknas via winget, hämtar språkpaketet för svenska och lägger
  katalogerna i `PATH` för sessionen. Lyckas det inte skrivs en förklaring
  ut och tjänsten startar ändå, men inskannade rapporter hoppas över.

  Skriptet går också att köra fristende, vilket är enklaste sättet att
  felsöka en installation:

  ```powershell
  .\\scripts\\Ensure-OcrTools.ps1
  ```

## Installation

```bash
git clone https://github.com/slimebob1975/JBGAnnualReportAnalyzer.git
cd JBGAnnualReportAnalyzer

# Allt, inklusive maskning och OCR:
pip install -e ".[masking,ocr,dev]"

# Eller en slimmad installation utan torch (maskningen måste då stängas av
# i formuläret):
pip install -r requirements.txt
```

`requirements.txt` innehåller exakta versioner för reproducerbara
Docker-byggen. För utveckling är `pip install -e ".[...]"` att föredra,
eftersom `pyproject.toml` anger versionsintervall i stället.

### Så fungerar maskningen

Ordningen är **OCR först, sedan maskning, sedan textutdrag**. En inskannad
sida innehåller ingen text att hitta, så en maskning som körs före OCR
svärtar inget alls – och namnen dyker upp igen när OCR körs.

Efter maskningen kontrolleras resultatet: den maskerade filen läses tillbaka
och varje känslig term söks igen, även över radbrytande bindestreck. Hittas
någon term kvar skrivs ett fel i loggen, den maskerade filen tas bort och
dokumentet analyseras inte. Omaskerad text ska inte lämna maskinen.

### Namn som alltid ska maskeras

NER-modellen hittar det mesta, men vissa namn återkommer och bör alltid
maskeras. De läggs i en fil som inte versionshanteras:

```bash
cp app/config/masking_extra_names.example.json app/config/masking_extra_names.json
# fyll i "fornamn" och "efternamn"
```

Filen är med i `.gitignore` och `.dockerignore` eftersom den innehåller
personuppgifter. Saknas den maskeras enbart det som NER-modellen hittar.

## Körning

### Lokalt

```bash
uvicorn app.main:app --reload
```

Öppna sedan <http://127.0.0.1:8000>.

### Med Docker

```bash
docker build -t jbg-analyzer .
docker run -d -p 8000:8000 jbg-analyzer
```

Bygget laddar ner NER-modellen och tiktokens kodningsfiler och lägger dem i
avbilden, så att tjänsten inte behöver internetåtkomst mot Hugging Face vid
körning. Det tar tid och utrymme. För ett snabbt bygge:

```bash
docker build --build-arg SKIP_PREFETCH=1 -t jbg-analyzer .
```

Kontrollera att containern mår bra:

```bash
curl http://localhost:8000/health
```

Svaret anger om maskning och OCR faktiskt är installerade, om jobbkatalogen är
skrivbar och hur många jobb som pågår.

## Konfiguration

Alla inställningar har rimliga standardvärden. Ingen av dem behöver sättas för
att tjänsten ska fungera.

| Variabel | Standard | Beskrivning |
| --- | --- | --- |
| `JBG_LOG_LEVEL` | `INFO` | Loggnivå. `DEBUG` skriver ut fullständig dokumenttext och modellsvar, vilket innebär personuppgifter på disk. |
| `JBG_LOG_RETENTION_DAYS` | `14` | Loggfiler äldre än så tas bort vid start. De fem senaste sparas alltid. |
| `JBG_JOB_DIR` | systemets temp-katalog | Var jobbens arbetskataloger skapas. |
| `JBG_JOB_TTL_SECONDS` | `3600` | Hur länge ett jobbs filer ligger kvar efter senaste livstecken. |
| `JBG_SWEEP_INTERVAL_SECONDS` | `300` | Hur ofta utgångna jobb städas bort. |
| `JBG_MAX_CONCURRENT_JOBS` | `2` | Antal analyser som körs samtidigt. |
| `JBG_MAX_UPLOAD_MB` | `200` | Största tillåtna uppladdning. |
| `JBG_MODEL_PRICES` | `app/config/model_prices.json` | Sökväg till prislistan för kostnadsuppskattning. |
| `JBG_MASKING_EXTRA_NAMES` | `app/config/masking_extra_names.json` | Sökväg till filen med extra namn att maskera. |
| `JBG_NER_MODEL` | `KBLab/bert-base-swedish-cased-ner` | Modell för namnigenkänning. Läses vid bygget. |
| `TIKTOKEN_CACHE_DIR` | – | Bör pekas mot en förhämtad katalog i miljöer utan utgående nätverk. |

## Användning

1. Öppna webbgränssnittet och välj fil, modell, svarsformat och om maskning ska
   användas.
2. Analysen startar som ett bakgrundsjobb. Sidan visar vilken fil som bearbetas
   och hur lång tid som gått.
3. När jobbet är klart laddas resultatfilen ner automatiskt.

En analys av sju årsredovisningar tar i storleksordningen fyra minuter, varav
merparten är anrop till språkmodellen.

### Att läsa resultatet

Excel-filen färgkodar varje värde efter hur modellen hittade det: grönt för
`explicit` (står ordagrant i dokumentet), gult för `härledd` (beräknat eller
tolkad rubrik) och rött för `osäker` (bör kontrolleras). Lila markering
betyder att värdet ingår i en rimlighetskontroll som inte gick ihop. Håll pekaren över en cell för att se källa, säkerhet och motivering.
Fliken **Läsanvisning** innehåller färgförklaring och en lista över samtliga
anmärkningar.

Notposter heter `Not N: <post>` så att de inte förväxlas med balans- eller
resultaträkningens rader med liknande namn. Flera kassor kallar
`Not 7: Årets avstående från återkrav` för *eftergift*; det tolkas som samma
sak.

Varje delsumma i föreskriften kontrolleras aritmetiskt mot sina delposter.
Kontrollerna byggs ur `Delposter` i nyckeltalsdefinitionerna, så ett nytt
nyckeltal med delposter ger en ny kontroll utan kodändring.

Två avvikelser skiljs ut från vanliga differenser, eftersom de kräver helt
olika åtgärd. **Omvänt tecken** betyder att noten och den post den
specificerar är samma belopp med olika tecken; det är en fråga om
teckenkonvention och inte om en felläst siffra. **Skalfel** betyder att de två
sidorna är samma belopp i kronor respektive tusental kronor, vilket händer när
ett värde hämtats ur förvaltningsberättelsen i stället för ur räkningen. Båda
namnger sig själva i anmärkningen.

Ett nyckeltal som saknas i utdata har inte hittats i dokumentet. Modellen är
instruerad att utelämna det den inte hittar hellre än att gissa.

En fil som inte går att läsa – en inskannad rapport utan OCR, till exempel –
hoppas över med en tydlig förklaring i loggen, i webbgränssnittets
slutmeddelande och under nyckeln `_ejanalyserade` i JSON-filen. Övriga filer
analyseras som vanligt.

Tomma rader är därför ett normalt och förväntat resultat. Vissa nyckeltal redovisas helt enkelt inte av alla kassor: i ett testmaterial med sju
årsredovisningar saknade fem stycken 'Kortfristiga placeringar' även efter
riktad omsökning. Det är ett riktigare svar än en gissning, och tjänsten innehåller medvetet inga särregler för enskilda nyckeltal.

Hittar den första genomgången inte alla nyckeltal görs en riktad omsökning som
enbart frågar efter de saknade. Värden från omsökningen märks med
`[Riktad omsökning]` i kommentarsfältet. Saknas ett nyckeltal även efter det
finns posten med största sannolikhet inte i dokumentet.

### När OCR inte ger någon text

OCR körs i två steg. Först den billiga inställningen, som lämnar sidor som
redan har text ifred. Ger den mindre än 2 000 tecken görs ett andra försök med
`force_ocr`, som rastrerar varje sida oavsett vad den innehåller. Det andra
steget kostar ungefär dubbelt och är därför en reserv, inte förval.

Ett dokument vars sidinnehåll är ritat i stället för inbäddat som bild ser
tomt ut för det första steget: ocrmypdf hittar ingenting att tolka och
returnerar en fil lika tom som den fick in. Ett sådant dokument gav 897 tecken
på femton sidor, samtliga från en digital signatur som fogats till efter
skanningen.

Misslyckas båda försöken undersöks filen sida för sida innan den hoppas över.
Loggen anger då hur många sidor som har textlager, hur många som har bilder, i
vilken upplösning, och om sidorna över huvud taget renderar något. Slutsatsen
skrivs ut som en av ett fåtal namngivna orsaker – tom rendering, inbäddade
bilder, låg upplösning – och följer med i skipp-meddelandet i stället för bara
ett teckenantal.

Undersökningen renderar sidor i minnet. `SAVE_DIAGNOSTIC_RENDERS = True` i
`JBGAnnualReportAnalysis.py` sparar dem som PNG i jobbkatalogen, vilket är
bekvämt vid felsökning men lägger omaskerade sidor ur en inskannad rapport på
disk. Därför är det avstängt.

### Stabilitetskontroll av inskannade rapporter

Dokument som gått genom OCR läses av två gånger och resultaten jämförs. Ett
värde som skiljer sig mellan de två avläsningarna märks som **Instabil
avläsning** och behöver kontrolleras mot källdokumentet. Den första
avläsningen behålls: att de skiljer sig säger att siffran är osäker, inte att
det andra försöket är bättre.

Kontrollen görs bara på OCR-tolkade dokument, där skevt inskannade sidor ger
osäker teckentolkning. Den dubblar extraktionskostnaden för de dokumenten och
redovisas separat som `stabilitetskontroll` i tokenrapporten. Stängs av med
`VERIFY_OCR_EXTRACTION = False`, eller utökas till alla dokument med
`VERIFY_ALL_EXTRACTIONS = True`, i `JBGAnnualReportAnalysis.py`.

Anmärkningarna finns i alla tre formaten:

* **Excel** – lila celler, texten i cellkommentaren och en samlad lista på
  fliken Läsanvisning.
* **CSV** – kolumnen `Validering` anger vilken kontroll som slog till för raden.
* **JSON** – nyckeln `_rimlighetskontroller` innehåller samtliga anmärkningar
  med kassa, år, kontroll och berörda nyckeltal. Nycklar som börjar med
  understreck är metadata, inte kassor.

Loggen visar också fördelningen av angiven säkerhet efter varje körning. Om
nästan alla värden får 1,0 skiljer skalan inte mellan säkra och osäkra värden,
och färgkodningen säger då lite.

### Långa körningar

Livslängden räknas från jobbets senaste livstecken, inte från när det
skapades. Ett jobb rapporterar framsteg efter varje dokument och håller sig
därmed självt vid liv hur länge analysen än tar. Räknat från skapandet tog
städningen bort arbetskatalogen mitt under en körning: en analys av 24 filer
tar omkring nittio minuter, livslängden var en timme, och det tjugotredje
dokumentet föll på att dess pdf inte längre fanns. Ett jobb som verkligen har
hängt sig städas fortfarande bort, men först efter en hel livslängd utan
livstecken.

Ett fel i ett dokument stoppar inte längre körningen. Filen redovisas under
`_ejanalyserade` med orsaken, och de övriga analyseras som vanligt.

Efter varje dokument skrivs det som hunnit bli klart till en fil med suffixet
`_delvis.json` bredvid resultatfilen. Avbryts körningen finns arbetet kvar
där.

### Kostnad per körning

Efter varje körning loggas antal anrop och tokens, uppdelat både per modell och
per ändamål – nyckeltalsextraktion, riktad omsökning, årtolkning och
sidnummeroffset. Samma uppgifter finns under nyckeln `_modellanvandning` i
JSON-filen.

Priser per miljon tokens ligger i `app/config/model_prices.json` och täcker
modellerna i formuläret. Varje post har tre satser:

```json
"gpt-5.2": { "in": 1.75, "cachad": 0.175, "ut": 14.00 }
```

Cachade prompt-tokens debiteras separat, ofta en tiondel av `in`. Det är ingen
detalj: i en verklig körning var 88 procent av alla prompt-tokens cachade.

Ett modellnamn med datumsuffix matchar sin nyckel, så `gpt-5.2-2025-12-11`
prissätts som `gpt-5.2`. Saknas priset för någon använd modell rapporteras bara
tokens – aldrig en halv kostnad. Kontrollera priserna mot aktuell prislista;
de gäller standardnivån och korta kontext.

### Modell per roll

Formuläret har en modellväljare per steg: första genomgången, riktad omsökning
och stabilitetskontroll. Valen sparas i `app/config/model_roles.json` när
analysen startas och är förvalda nästa gång. Priset per miljon tokens står i
varje alternativ.

Filen skrivs av tjänsten och är därför gitignorerad; `model_roles.example.json`
ligger bredvid och kopieras vid första körningen. Den kan också redigeras
direkt, vilket är enda sättet att sätta rollerna `aar` och `sidnummer` som inte
finns i formuläret:

```json
{
  "extraktion": "gpt-5.6-solar",
  "omsokning":  "gpt-5.4-mini",
  "stabilitet": "gpt-5.2"
}
```

Tomt värde betyder att formulärets val används. Tokenrapporten redovisar
förbrukningen per modell och per roll, så en kombination går att mäta i stället
för att gissas.

Två uppslag värda att pröva: en dyrare modell för extraktionen och en billigare
för omsökningen, som är en smalare uppgift; och en **annan** modell för
stabilitetskontrollen, eftersom två läsningar med samma modell mäter modellens
egen spridning medan två modeller som är överens är ett starkare belägg för att
värdet stämmer.

## API

Webbgränssnittet använder samma API. Formulären fungerar även utan JavaScript,
då synkront via `/upload` och `/mask`.

| Metod | Väg | Beskrivning |
| --- | --- | --- |
| `POST` | `/api/analyze` | Startar en analys. Returnerar `job_id` direkt. |
| `POST` | `/api/mask` | Maskerar en enskild PDF. Returnerar `job_id`. |
| `GET` | `/api/jobs/{job_id}` | Jobbets status, framsteg och meddelande. |
| `GET` | `/api/jobs/{job_id}/download` | Resultatfilen. Endast det egna jobbets filer. |
| `GET` | `/health` | Status för containerns healthcheck. |

## Utveckling

```bash
pip install -e ".[masking,ocr,dev]"
ruff check app tests
pytest -q
```

På Windows går samma kontroller att köra med:

```powershell
.\run_checks.ps1        # lint och tester
.\run_checks.ps1 -Fix   # rätta det ruff klarar själv först
```

GitHub Actions kör lint och tester vid varje push. Bygget av Docker-avbilden
är en driftfråga och körs bara manuellt från fliken Actions, eftersom tjänsten
ännu inte driftsätts någonstans. Ta bort `if:`-raden i
`.github/workflows/ci.yml` när det ändras.

Testerna injicerar en attrapp i stället för NER-modellen och anropar aldrig
OpenAI, så hela sviten kör på några sekunder utan nätverk eller API-nyckel.

## Projektstruktur

```
JBGAnnualReportAnalyzer/
├── app/
│   ├── main.py                     # FastAPI-endpoints
│   ├── config/                     # extra namn att maskera (ej i git)
│   ├── prompt/                     # instruktioner och nyckeltalsdefinitioner
│   ├── src/
│   │   ├── JBGAnnualReportAnalysis.py   # textextraktion och modellanrop
│   │   ├── JBGMetricSchema.py           # JSON-schema för svaret
│   │   ├── JBGFundNames.py              # normalisering av kassanamn
│   │   ├── JBGValidation.py             # rimlighetskontroller
│   │   ├── JBGJobs.py                   # bakgrundsjobb och städning
│   │   ├── JBGJSONConverter.py          # export till CSV och Excel
│   │   └── masking/JBGPDFMasking.py     # maskning av PDF
│   ├── static/
│   └── templates/
├── scripts/
│   ├── prefetch_models.py           # hämtar modeller vid bygget
│   └── Ensure-OcrTools.ps1          # installerar tesseract och ghostscript
├── run_checks.ps1                  # lint och tester lokalt (Windows)
├── tests/
├── pyproject.toml
├── requirements.txt
└── Dockerfile
```

## Licens

Detta projekt är licensierat under GPL-3.0. Se [LICENSE](LICENSE).
