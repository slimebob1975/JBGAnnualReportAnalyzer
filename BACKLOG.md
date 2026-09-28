# Backlog

Vad som återstår, varför det spelar roll och vad som krävs för att komma
vidare. Punkterna är ordnade efter vad som blockerar dem, inte efter storlek:
det som väntar på verksamheten går inte att koda sig ur, och det som är
tidskritiskt förlorar värde om det får ligga.

Siffror i det här dokumentet kommer från körningen den 18 september 2026
(24 årsredovisningar, 72:56, 6,34 USD, 2 470 uppgifter, 96,3 % explicit,
98 anmärkningar) om inget annat anges.

---

## A. Tidskritiskt

### A1. Fånga de manuellt inmatade siffrorna medan de finns kvar

**Läget.** Alla kvalitetsmått verktyget har är inre konsistens: aritmetiska
identiteter, omläsning av samma dokument, jämförelse mellan kassor. Ingenting
mäter om siffrorna är *rätt*. Ett värde kan stämma aritmetiskt, ge samma
resultat vid två avläsningar, märkas som `explicit` och ändå vara hämtat från
fel rad.

**Varför nu.** Årets siffror har matats in manuellt och stämts av mot
verktygets utdata. Det är det enda facit som någonsin funnits för det här
systemet, och det finns just nu. Nästa år ersätter verktyget den manuella
inläsningen, och då finns inget att jämföra mot. När årets fil ersätts är
möjligheten borta.

**Vad som behövs.** Kalkylbladet med de manuellt inmatade värdena. Därefter en
jämförelsemodul som rapporterar träffsäkerhet per nyckeltalsgrupp, per
säkerhetsnivå (`explicit` / `härledd` / `osäker`) och per om cellen var
anmärkt eller inte.

**Vad det ger.** Ett svar på den fråga ingen kan besvara i dag: betyder
`explicit` någonting? Om `explicit` och oanmärkt visar sig vara rätt i 99,4 %
av fallen kan man granska de 98 anmärkningarna och lita på resten — och säga
det med belägg. Om `explicit` och `osäker` har samma träffsäkerhet är
färgkodningen dekoration och bör beskrivas som sådan i Läsanvisningen.

**Ansvar.** Kollegan som gjorde avstämningen. Kodning därefter.

---

## B. Väntar på verksamheten

Ingen av punkterna här går att avgöra från koden. Alla tre återkommer i varje
körning, och tills de besvaras rapporterar verktyget samma sak om och om igen
utan att någon lär sig något av det.

### B1. Delpostlistorna för tre summor

**Iakttagelsen.** `Summa skulder` överstiger summan av sina delposter i 5
kassor, `Summa finansiella poster` i 4 och `Summa omsättningstillgångar` i 2.
Antalet kassor varierar mellan körningar (8, 6 respektive 2 i föregående
körning) men mönstret är stabilt, och den rapporterade summan är nästan alltid
*större* än delposterna.

**Frågan.** Har verkliga balansräkningar poster som föreskriftens uppräkning i
bilaga 2 inte tar upp — skatteskulder, skulder till andra a-kassor,
personalens källskatt? Eller missar modellen delposter?

**Vad som behövs.** Någon som öppnar två eller tre årsredovisningar och tittar.
Tjugo minuter.

**Följd.** Är uppräkningen ofullständig ska kontrollen tillåta att en summa
överstiger sina listade delposter, och ungefär en femtedel av alla anmärkningar
försvinner permanent. Är den fullständig har vi en systematisk lucka i
extraktionen som är värd att jaga.

### B2. Alfa-kassans `Summa intäkter`

**Iakttagelsen.** `Summa intäkter` överstiger `Medlemsavgifter` +
`Övriga intäkter` med exakt 70 691 tkr. Samma belopp, samma kassa, i fem
körningar i rad. Reproducerbarhet på den nivån betyder strukturell skillnad,
inte felläsning.

**Hypotesen.** Alfa administrerar ersättning till icke anslutna och får ett
särskilt statsbidrag för administration, som föreskriftens tvåtermsformel inte
täcker.

**Vad som behövs.** Bekräftelse mot Alfas årsredovisning. Stämmer det bör Alfa
undantas från den kontrollen, eller kontrollen kompletteras med posten.

### B3. Enhet för belopp i bilaga 2

**Iakttagelsen.** Bilaga 2 anger inte i vilken enhet belopp ska redovisas, och
kassorna svarar olika. `Utbetald arbetslöshetsersättning` löpte från 2 081 till
1 801 362 020 över kassorna innan normaliseringen. 21 belopp räknas nu om
automatiskt, men tre kvarstår omärkta och flaggade:

| Kassa | Rapporterat | Trolig innebörd |
| --- | --- | --- |
| Akademikernas | 2 924 | miljoner kronor → 2,9 mdkr |
| Alfa | 2 081 | miljoner kronor → 2,1 mdkr |
| Kommunalarbetarnas | 3 542 833 | tusental kronor → 3,5 mdkr |

**Frågan.** Vad står det faktiskt i de tre årsredovisningarna? Och, vidare:
avser föreskriften en bestämd enhet? Gör den det, och kassorna ändå svarar
olika, är det en tillsynsiakttagelse i sig och inte bara ett städproblem.

**Vad som behövs.** Besked om de tre. Se även C1, som löser fallen tekniskt om
besked dröjer.

---

## C. Klart att bygga

### C1. Iterera enhetsnormaliseringen mot en delad referens

**Felet.** `normalise_units` och `check_unit_consistency` beräknar sin
referensmedian på olika underlag: den ena före omräkningarna, den andra efter.
Kommunalarbetarnas mättes mot en median på cirka 186 miljoner och hamnade 52
gånger under — precis under tröskeln på 100 — och lämnades därför orörd. Efter
de övriga omräkningarna är medianen 418 miljoner, och samma värde rapporteras
nu av kontrollen som 118 gånger för lågt. Rättaren och kontrollanten tittar på
olika kolumner.

**Åtgärden.** Kör normaliseringen om tills inga fler omräkningar sker (med tak,
förslagsvis fem varv) och låt båda funktionerna använda samma referensfunktion.
Räknat mot medianen efter första varvet löser sig alla tre fallen i B3 inom
befintligt residualtest: Kommunal ×1000 → 8,5 gånger medianen, Akademikernas
×1 000 000 → 7,0, Alfa → 5,0.

**Omfattning.** Liten. `app/src/JBGNormalisation.py` och
`check_unit_consistency` i `app/src/JBGValidation.py`.

### C2. Namnge nyckeltalet i anmärkningar om instabilitet

De 62 instabilitetsanmärkningarna anger inte vilket nyckeltal som ändrades —
bara i loggen; i Excel färgas rätt cell. Det gör dem omöjliga att analysera i
grupp. En rad kod, och det skulle ha sparat tid flera gånger under arbetet.

### C3. Jämförelse mellan år

**Varför den är viktigast av de byggbara.** Alla kontroller utom
enhetsjämförelsen arbetar *inom* en enskild årsredovisning. När den manuella
inmatningen upphör försvinner den sista externa referensen — om det inte finns
ett föregående år att jämföra mot.

Ett värde som rört sig tusen gånger, bytt tecken eller hoppat en tiopotens
mellan 2024 och 2025 är nästan säkert fel, och varken modellen eller någon
aritmetisk identitet märker det. Kostar en körning av 2024 års rapporter och
därefter inga modellanrop alls.

Det är också den kontroll som svarar mot kollegans faktiska invändning, att det
i skarpt läge inte finns något att kontrollera mot.

### C4. Utöka `Delposter` till statistiken i bilaga 2

**Läget.** 55 av 108 nyckeltal omfattas av minst en aritmetisk kontroll. De 53
som inte gör det är 30 notposter och **samtliga 23 statistikuppgifter i
bilaga 2**. För dem finns bara modellens egen säkerhetsbedömning, och
ingenting skulle märka att `Antal medlemmar varav män` lästs från fel rad.

**Åtgärden.** Flera av uppgifterna har inbördes samband som föreskriften redan
ger: `varav män` + `varav kvinnor` = totalt antal medlemmar, och motsvarande
för återkrav och anmälningar. Kontrollerna byggs ur `Delposter` i
nyckeltalsdefinitionerna, så det är redigering av en JSON-fil och inte
kodändring.

**Följd.** Flyttar en del av de 53 oskyddade nyckeltalen in under kontroll till
i stort sett ingen kostnad.

### C5. Excel som förvalt utdataformat

**Läget.** Formulärets radioknappar står i ordningen JSON, CSV, Excel, med
JSON förvald (`app/templates/index.html`, raderna 62–64). Serversidan har
ingen egen förvalning: `format` deklareras som `Form(...)`, alltså obligatorisk,
så sidans markering är den enda förvalningen som finns.

**Varför det är fel.** Excel är det enda format som bär det verktyget faktiskt
producerar — färgkodning efter säkerhet, kommentarer med källa och anmärkning,
nyckeltalsberäkningar och de två flikarna. JSON och CSV är utvecklar- och
integrationsformat. Den som använder tjänsten för sitt arbete vill ha
Excel-filen varje gång och måste i dag aktivt välja bort förvalet.

**Åtgärden.** Sätt `checked` på Excel och lägg knapparna i ordningen Excel,
CSV, JSON, så att ordningen speglar hur ofta formaten faktiskt används. Ge
samtidigt `format` ett serverförval på `"xlsx"` i stället för `Form(...)`, så
att förvalet gäller även för anrop som inte kommer från formuläret och inte
bara är en egenskap hos sidan.

**Omfattning.** `app/templates/index.html` och `app/main.py`. Kontrollera om
någon av de elva formatberoende testerna utgår från JSON som förval.

### C6. Flik 3 — sammanfattningar ur förvaltningsberättelsen

Efterfrågad av verksamheten: en flik med kassorna på rader och tre
textkolumner — årets viktigaste händelser, förväntad framtida utveckling och
arbetslöshetsnivå, samt hur kassan säkerställt kontroll av medelsförvaltningen.

**Två villkor innan den byggs.** Det här är generering, inte extraktion: det
finns ingen aritmetik som fångar ett fel, och en flytande, rimlig men felaktig
sammanfattning är betydligt svårare att upptäcka än en felaktig siffra. Varje
sammanfattning bör därför bära ett ordagrant citat och en sidhänvisning, och
där stöd saknas ska cellen läsa "Framgår inte av årsredovisningen" i stället
för att modellen skriver något rimligt.

Den tredje kolumnen finns ännu inte i årsredovisningarna — kravet väntas nästa
år — så den kommer att vara tom för de flesta kassor. Det är rätt utfall, men
det måste vara tomt av rätt skäl.

**Kostnad.** Ett extra modellanrop per dokument över redan uttagen text,
ungefär 3–4 USD per körning.

---

## D. Beslut, inte kod

### D1. Pappersindustriarbetarnas

23 av 98 anmärkningar kommer från detta enda dokument — 23 procent av allt, från
4 procent av materialet. Det har varit så i varje körning: 19 av 62 instabila
avläsningar, fyra delsummor som inte stämmer, en balansräkning som inte
balanserar. Orsaken är känd och ligger utanför programmet: skanningen är skev
och textlagret positionsförskjutet, så kolumner blandas.

Ingen kodändring hjälper. Antingen matas dokumentet in för hand, eller så
begärs en ren kopia från kassan. Så länge det ligger kvar förvränger det varje
kvalitetsmått som redovisas.

### D2. Vem äger anmärkningarna

Ersätter det här den manuella inläsningen måste någon granska de omkring hundra
anmärkningarna per körning: vem, mot vad, och vad innebär ett godkännande.
I dag producerar verktyget en lista och processen runt den är odefinierad.

Det är inte ett kodproblem, men det är skillnaden mellan ett användbart
analysverktyg och något verksamheten kan luta sig mot. Bör avgöras före första
skarpa körningen, inte efter.

### D3. Vad "klart" betyder

Med 108 nyckeltal per kassa innebär 95 procents träffsäkerhet per nyckeltal
drygt fem fel i varje kassa — alltså att varje kassa ändå måste granskas.
Måttet som betyder något är inte andelen rätt, utan om anmärkningarna och
`osäker`-märkningen fångar felen. Kan man säga att de gör det, blir granskningen
riktad i stället för allomfattande. A1 är det som gör den utsagan möjlig.

---

## E. Avklarat

Kort historik, så att det går att se vad som redan prövats och varför.

| Patch | Vad | Belägg för att det fungerade |
| --- | --- | --- |
| 0041 | OCR-stege med `force_ocr`, diagnostik vid misslyckad OCR, skalfel- och teckenkontroll i valideringen | Dokument 172422 gick från 897 till 21 523 tecken; reserven utlöstes på exakt det dokument den skrevs för |
| 0042 | Jobbets livslängd räknas från senaste livstecken; fel i ett dokument stoppar inte körningen; delresultat sparas löpande | En körning på 81 minuter mot en livslängd på 60 överlevde; tidigare kördes arbetskatalogen bort mitt i |
| 0043 | Stabilitetskontrollen jämför bara det omläsningen också söker efter; teckenkonventionen fastställd i definitionerna; anmärkningar med samma differens kopplas ihop | Instabilitet 105 → 74; not mot räkning 21 → 10 |
| 0044 | Nyckeltalsberäkningar ur JSON, delade flikar, kontroll av enhet mellan kassor | Enhetskontrollen hittade att fyra beloppsnyckeltal i bilaga 2 rapporterades i blandade enheter |
| 0045 | Tecken- och enhetsnormalisering i kod, rimliga intervall för nyckeltalen | 21 belopp omräknas automatiskt; teckennormaliseringen utlöses numera nästan aldrig, eftersom 0043 löste problemet uppströms |
| 0046 | Person- och samordningsnummer i alla former; organisationsnummer undantas; oberoende svep av utdata | Svepet tyst i 24 av 24 dokument; maskerade termer 1 018 → 994, exakt ett färre i 22 dokument, vilket är kassans eget organisationsnummer som tidigare svärtades |

### Öppen observation utan åtgärd

`Summa tillgångar` för Vision avvek med 99 670 tkr från sina delposter i
körningen den 17 september. Syntes inte i tidigare körningar och inte i den
18 september. Noterat i väntan på om det återkommer.
