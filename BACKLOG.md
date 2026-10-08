# Backlog

Vad som återstår, varför det spelar roll och vad som krävs för att komma
vidare. Punkterna är ordnade efter vad som blockerar dem, inte efter storlek:
det som väntar på verksamheten går inte att koda sig ur, och det som är
tidskritiskt förlorar värde om det får ligga.

Siffror i det här dokumentet kommer från körningen den 1 oktober 2026
(24 årsredovisningar, 84 minuter, 6,44 USD, 2 479 uppgifter, 93,5 % explicit,
92 anmärkningar varav 2 allvarliga) om inget annat anges.

---

## A. Väntar på skarpa testet våren 2026

Verksamheten har stickprovskontrollerat 2025 års material och godkänt läget så
långt. Det riktiga provet görs under våren 2026 mot en helt ny uppsättning
årsredovisningar. Tre frågor hänger på det provet och kan inte avgöras före
det.

### A1. Mät träffsäkerheten mot det nya materialet

**Läget.** Alla kvalitetsmått verktyget har är inre konsistens: aritmetiska
identiteter, omläsning av samma dokument, jämförelse mellan kassor. Ingenting
mäter om siffrorna är *rätt*. Ett värde kan stämma aritmetiskt, ge samma
resultat vid två avläsningar, märkas som `explicit` och ändå vara hämtat från
fel rad.

Stickproven på 2025 års material är ett gott tecken men inte ett mått: ingen
vet hur många celler som kontrollerades, eller om de som kontrollerades var
representativa för dem som inte var det.

**Vad våren 2026 bör ge.** Kör det nya materialet och jämför mot det som matas
in för hand samma säsong, medan båda finns. Rapportera träffsäkerhet per
nyckeltalsgrupp, per säkerhetsnivå (`explicit` / `härledd` / `osäker`) och per
om cellen var anmärkt eller inte.

**Vad det svarar på.** Betyder `explicit` någonting? Är `explicit` och oanmärkt
rätt i 99,4 procent av fallen kan man granska de dryga nittio anmärkningarna
och lita på resten — och säga det med belägg. Har `explicit` och `osäker` samma
träffsäkerhet är färgkodningen dekoration och bör beskrivas som sådan i
Läsanvisningen.

**Att förbereda i förväg.** Jämförelsemodulen bör finnas klar när materialet
kommer, inte byggas medan det ligger på bordet. Formatet på de manuellt
inmatade siffrorna avgör hur den ska se ut, så fråga efter ett exempel i god
tid.

### A2. Vem äger anmärkningarna

Ersätter det här den manuella inläsningen måste någon granska de omkring
nittio anmärkningarna per körning: vem, mot vad, och vad innebär ett
godkännande.

Processen formas under det skarpa testet våren 2026. Det är rätt tillfälle:
det är då någon för första gången granskar en körning utan att ha läst
dokumenten själv först, och det är den situationen processen ska klara.

Värt att notera under provet: hur lång tid granskningen av en körning tar, hur
många anmärkningar som visar sig vara verkliga fel, och vilka som visar sig
vara brus. Det är de siffrorna som avgör om granskningen kan vara riktad i
stället för allomfattande.

### A3. Vad "klart" betyder

Med 108 nyckeltal per kassa innebär 95 procents träffsäkerhet per nyckeltal
drygt fem fel i varje kassa — alltså att varje kassa ändå måste granskas.
Måttet som betyder något är inte andelen rätt, utan om anmärkningarna och
`osäker`-märkningen fångar felen.

Kan inte besvaras före A1. Står här för att frågan inte ska glömmas bort när
siffrorna väl finns.

---

## B. Senare

### B1. Köra dokumenten parallellt

Kvar i backloggen, inte aktuell nu. Mätt på körningen den 1 oktober 2026,
84 minuter för 24 filer: väntan på språkmodellen 58 minuter (69 procent),
maskering på CPU 20 minuter (24 procent), OCR 5 minuter (6 procent).

Två sorters parallellism, inte en. Väntan på modellen är I/O och klarar sig med
trådar, men taket sätts av kontots hastighetsgränser — ungefär 19 000 tokens
per minut och arbetare — inte av maskinen. Maskeringen och OCR är CPU-arbete
och är den del som faktiskt beror på hårdvaran.

Nyttan ligger i utvecklingstempo snarare än i drift: den 1 oktober 2026 kördes
hela materialet fem gånger på en dag, och varje varv kostade drygt en timme av
väntan innan nästa fel kunde ses.

**Steg ett är mätt, inte byggt.** `scripts/measure_concurrency.py` skickar
egna anrop av samma storleksordning som en nyckeltalsextraktion, med ett, två,
fyra och åtta i taget, och rapporterar genomströmning, mediantid och
hastighetsgränser. Inga återförsök, eftersom poängen är att se gränserna och
inte dölja dem. Visar mätningen att kontot inte orkar mer än två samtidiga är
resten av punkten inte värd att bygga.

**Loggen är spårbar sedan 0075.** Varje rad som hör till ett dokument bär dess
namn, så att loggen går att följa även flätad. Byggt före trådningen och inte
efter, eftersom det är verktyget man behöver just när något går fel.

**Vad slingan rör, genomgånget.** Slingan är 174 rader och rör följande
tillstånd på analysobjektet. Listan är avstämd mot koden, inte minnet.

*Måste göras om innan trådning:*

| Tillstånd | Problem |
| --- | --- |
| `self._masker` | `_get_masker` kollar och sätter i två steg. Två trådar hinner båda se `None` och bygger var sin BERT-modell, omkring 2 GB styck. Behöver lås. |
| `self.last_ocr_diagnosis` | `_run_ocr` skriver, `_ensure_readable_pdf` läser efteråt för felmeddelandet. Två dokument samtidigt kan ge det enas diagnos till det andras avbrott. Ska vara returvärde, inte fält. |
| `report(file_index + 1, namn)` | Förutsätter att filerna blir klara i ordning. |
| `_save_partial_result(total_result, ...)` | Skriver en fil ur en delad lista. Samtidiga skrivningar flätas. Behöver serialisering. |
| `_warn_on_year_collision(total_result, ...)` | Läser `total_result` medan andra trådar hinner lägga till. |

*Går tekniskt bra men ger ostabil ordning:*

`self.skipped_files.append()` (fyra ställen), `self.stability_findings.extend()`
och `total_result.append()` är atomära under GIL:en, men ordningen blir
slumpmässig. Utdata bör vara likadan mellan två körningar av samma material, så
de bör samlas per dokument och slås ihop i filordning efteråt.

`declared_units[kassa]` och `management_summaries[kassa]` är lokala i
`do_analysis` och nycklade på kassa. Ordningen spelar ingen roll där.

*Redan trådsäkert:*

`self.usage` har ett `threading.Lock` i `record`. Räknaren för anrop och
tokens behöver inget mer.

*Oförändrat:* enhetsnormaliseringen och enhetskontrollen mellan kassor är
korpusnivå och ligger efter sammanslagningen, där de hör hemma.

**Steg två är gjort i 0078.** Per-dokument-arbetet ligger i
`_analyse_document` och returnerar ett `DocumentOutcome` i stället för att
skriva till `self`. Sammanslagningen sker i filordning, så två körningar av
samma material ger samma utdata oavsett i vilken ordning dokumenten blir
klara. Maskeraren byggs under lås och OCR-diagnosen ligger i en ContextVar.

**Steg tre är gjort i 0079.** Arbetspool över dokument, `JBG_MAX_WORKERS`,
förval sexton. Maskering och OCR körs under lås. Resultaten slås ihop i
filordning, förloppet räknas vid faktiskt avslut.

**Maskeringen är golvet.** 15,7 av 22,7 minuter i den första parallella
körningen. Om den går att korta är frågan om arbetet är minnes- eller
processorbundet, och de två ser likadana ut utifrån: är det det senare sprider
PyTorch redan en modells arbete över alla kärnor, och fyra modeller slåss då
om samma kärnor utan att något går fortare. `scripts/measure_masking.py`
maskerar samma dokument med en, två och fyra arbetare och rapporterar väggtid,
tid per dokument och högsta minnesanvändning, med val för en modell per
arbetare och för att dela PyTorchs trådar.

Att fånga minnesfel och trappa ned är inget alternativ: på Linux dödar
OOM-dödaren processen utan undantag att fånga, och på Windows avbryter en
misslyckad allokering inuti torch oftast i stället för att kasta `MemoryError`.
Antalet behöver vara ett val, inte en upptäckt.

**Maskeringen är processorbunden, och grafikkortet används sedan 0082.**
Sonden gav samma genomströmning vid en, två och fyra parallella maskerare —
0,037, 0,030 och 0,038 dokument per sekund — medan tiden per dokument steg
från 27 till 63 till 93 sekunder. En modell med tolv trådar mättar processorn,
och fler modeller slåss om samma kärnor. Fler arbetare var alltså aldrig
svaret, och `_MASKING_LOCK` kostar ingenting.

Kvar att mäta: hur mycket kortet faktiskt ger. Maskeringen var 15,7 av 22,7
minuter, så en körning kan i bästa fall hamna kring tio.

Kvar: nedtrappning vid 429. Mätningen visade inga hastighetsgränser upp till
sexton samtidiga, så det är en försäkring och inte ett behov — men när det
väl händer ska loggen säga det i klartext i stället för att se ut som att
modellen blivit långsam.

**Loggen är spårbar sedan 0075.** Varje rad som hör till ett dokument bär dess
namn, så att loggen går att följa även flätad. Byggt före trådningen och inte
efter, eftersom det är verktyget man behöver just när något går fel.

**Att göra om först.** `skipped_files`, `stability_findings`,
`validation_findings` och `usage` fylls på per fil på instansen. `_masker`
skapas lazy. NER-pipelinen är inte utlovat trådsäker. Förloppsrapporteringen
förutsätter att filer blir klara i ordning. Enhetsnormaliseringen och
enhetskontrollen mellan kassor är korpusnivå och måste ligga kvar efter
sammanslagningen.

**Gör loggen spårbar först.** Hela höstens felsökning byggde på att läsa ett
dokuments rader i ordning. Med flera arbetare krävs filnamn eller jobb-id på
varje rad för att det ska gå att följa — det bör byggas före trådningen, inte
efter.

---

## C. Beslut, inte kod

### C1. Dåliga skanningar

Pappersindustriarbetarnas årsredovisning är skevt inskannad och dess textlager
positionsförskjutet. Ungefär 30 procent av dess värden ändras mellan två
avläsningar av samma dokument. Det är ett fel i underlaget och går inte att
koda bort: ingen efterbehandling gör ett skevt original rakt.

**Beslut.** Läget är tills vidare oundvikligt, och förhoppningen är att
skanningskvaliteten förbättras med tiden.

**Men fortsätt rapportera.** Att verktyget redovisar vilka dokument som är
svårlästa, och hur svårlästa, är en del av dess värde för tillsynen. En kassa
vars årsredovisning inte går att läsa maskinellt är en upplysning i sig.
Instabilitetsanmärkningarna och de vägrade dokumenten ska därför synas i
utdata, inte tystas ned för att få snyggare siffror.

---

## D. Avklarat

Kort historik, så att det går att se vad som redan prövats och varför.

| Patch | Vad | Belägg för att det fungerade |
| --- | --- | --- |
| 0041 | OCR-stege med `force_ocr`, diagnostik vid misslyckad OCR, skalfel- och teckenkontroll i valideringen | Dokument 172422 gick från 897 till 21 523 tecken; reserven utlöstes på exakt det dokument den skrevs för |
| 0042 | Jobbets livslängd räknas från senaste livstecken; fel i ett dokument stoppar inte körningen; delresultat sparas löpande | En körning på 81 minuter mot en livslängd på 60 överlevde; tidigare kördes arbetskatalogen bort mitt i |
| 0043 | Stabilitetskontrollen jämför bara det omläsningen också söker efter; teckenkonventionen fastställd i definitionerna; anmärkningar med samma differens kopplas ihop | Instabilitet 105 → 74; not mot räkning 21 → 10 |
| 0044 | Nyckeltalsberäkningar ur JSON, delade flikar, kontroll av enhet mellan kassor | Enhetskontrollen hittade att fyra beloppsnyckeltal i bilaga 2 rapporterades i blandade enheter |
| 0045 | Tecken- och enhetsnormalisering i kod, rimliga intervall för nyckeltalen | 21 belopp omräknas automatiskt; teckennormaliseringen utlöses numera nästan aldrig, eftersom 0043 löste problemet uppströms |
| 0050 | Enhetsnormaliseringen itererar mot en delad referens; faktorn väljs på logaritmiskt avstånd; Excel förvalt utdataformat | Kolumnen `Utbetald arbetslöshetsersättning` hamnar helt i kronor, noll kvarvarande anmärkningar om enhet mot tre tidigare |
| 0061 | Statistiken i bilaga 2 får kontroller: en summa och sex inneslutningar via det nya fältet `Ingår i`; nyckeltalets namn står först i instabilitetsanmärkningar | Täckningen gick från 55 till 69 av 108 nyckeltal; de oskyddade statistikuppgifterna från 23 till 9 |
| 0070 | Register över återkommande anmärkningar; körningens siffror i Läsanvisningen | Alfa-kassans avvikelse på 70 691 kom tillbaka fem gånger innan någon kände igen den |
| 0069 | Jämförelse mellan år: anmärkning vid hundra gånger, flik som listar varje rörelse över det dubbla | Trösklarna mätta på 234 par: median 1,16 och p99 24, så en tröskel vid tio hade anmärkt på var elfte par |
| 0068 | Reserven med det vanligaste årtalet borttagen; varning när två dokument hamnar på samma kassa och år | Reserven blev fel båda gångerna den användes och slog ihop Lärarnas rapporter för 2024 och 2025 |
| 0067 | Ett dokument utan nyckeltal redovisas som ej analyserat; räkenskapsåret kan härledas ur det vanligast förekommande årtalet | Alfa-kassans årsredovisning för 2024 försvann spårlöst ur en körning sedan året inte gick att fastställa |
| 0066 | Varje färdig körning sparas som historik, så att nästa år har något att jämföra mot | Jobbkatalogen städas efter en timme; utan detta vore en körning av fjolårets material borta innan den kunde användas |
| 0065 | Citat jämförs på bokstäverna, utan diakriter och avstavning; ett citat som nästan stämmer märks i stället för att strykas | GS a-kassa fick två av tre riktiga sammanfattningar strukna för att modellen rättstavat det tesseract läst fel |
| 0064 | Förvaltningsberättelsen avgränsas till den längsta förekomsten av rubriken; inneslutningen för Antal domar borttagen; antal beskrivs inte som redovisningsposter | GS a-kassa fick tre tomma sammanfattningar ur ett avsnitt hämtat från innehållsförteckningen; Akademikernas 75 domar mot 43 överklaganden var inget fel |
| 0063 | Fliken Förvaltningsberättelse: tre sammanfattningar per kassa med ordagrant stödcitat och sida | Citat som inte går att återfinna i texten stryks tillsammans med sin sammanfattning |
| 0062 | Enheten läses ur dokumentets egen text och används för att hitta poster i fel enhet | Småföretagarnas finansieringsavgift, 117 308 746 där räkningen var i tusental, fångas på en ensam kassa — det fall jämförelsen mellan kassor inte kan se |
| 0057 | Svepet efter kvarvarande namn använder samma rimlighetsfilter som svärtningen | Unionens årsredovisning underkändes en hel dag för raden "TeamEngine E-Signing", som står på alla trettio sidorna och är en leverantörs banderoll, inte en person |
| 0046 | Person- och samordningsnummer i alla former; organisationsnummer undantas; oberoende svep av utdata | Svepet tyst i 24 av 24 dokument; maskerade termer 1 018 → 994, exakt ett färre i 22 dokument, vilket är kassans eget organisationsnummer som tidigare svärtades |

### Latent fel som hittades på vägen

Faktorn för enhetsomräkning valdes på linjärt avstånd från 1, vilket straffar
en faktor som skjuter över målet långt hårdare än en som hamnar under. För
2 924 mot medianen 418 miljoner gav ×1000 avståndet 0,99 och ×1 000 000
avståndet 5,99, så det uppenbart felaktiga tusentalet vann. Syntes aldrig
förrän iterationen gjorde att fallet över huvud taget prövades. Rättat i 0050;
avståndet mäts nu logaritmiskt.

### Öppna ändar, numera i anmärkningsregistret

`Summa tillgångar` för Vision avvek med 99 670 tkr från sina delposter i
körningen den 17 september 2026. Syntes inte före och inte efter.

Den sexteckens term i GS a-kassa som svärtas sedan 0058 utan att någon vet om
det var en person eller ett vanligt svenskt ord som NER tog fel på.

Båda var exempel på varför registret behövdes: det var en människas minne som
avgjorde om något var ett mönster eller en engångsföreteelse. Sedan 0070 räknar
registret i stället, och en anmärkning som kommer tillbaka flyter upp av sig
själv.
