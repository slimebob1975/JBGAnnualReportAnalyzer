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

## B. Klart att bygga

### B1. Jämförelse mellan år

**Varför den är viktigast av de byggbara.** Alla kontroller utom
enhetsjämförelsen arbetar *inom* en enskild årsredovisning. När den manuella
inmatningen upphör försvinner den sista externa referensen — om det inte finns
ett föregående år att jämföra mot.

Ett värde som rört sig tusen gånger, bytt tecken eller hoppat en tiopotens
mellan två år är nästan säkert fel, och varken modellen eller någon aritmetisk
identitet märker det.

**Hur historiken ska hållas — avgjort.** Inte som dold lokal status. Skälen:

* En lokal fil går förlorad vid ominstallation, byte av maskin eller när en
  kollega kör i stället.
* En myndighet behöver kunna svara på vad en jämförelse gjordes mot. "Värdet
  har rört sig tusen gånger sedan i fjol" är inte kontrollerbart om ingen vet
  vilken fjolårsfil som avsågs.
* Tyst jämförelse mot något användaren inte vet finns är en obehaglig
  egenskap hos ett granskningsverktyg.

**Föreslagen lösning.** Två vägar in, samma mekanism:

1. Varje avslutad körning sparar sin resultat-JSON i en historikkatalog i
   installationen, med kassa, år och körningsdatum i filnamnet. Katalogen är
   konfigurerbar så att den kan läggas på en säkerhetskopierad plats.
2. Användaren kan dessutom ladda upp en tidigare resultatfil att jämföra mot.
   Det är den vägen som överlever en ominstallation.

I formuläret en kryssruta "Jämför med historik", gråad när ingen historik
finns. När den är ikryssad ska det framgå **vad** jämförelsen görs mot — år,
filnamn och datum — både i gränssnittet och i Läsanvisningen. Resultatfilen bör
notera samma uppgifter, annars går en anmärkning inte att härleda i efterhand.

**Att tänka på.** Kassor byter namn och slås samman; jämförelsen måste gå via
kassanummer och namnupplösningen, inte via rubriken i kolumnen. Ett nyckeltal
som saknas det ena året är ingen avvikelse utan en lucka, och ska inte
rapporteras som en rörelse.

### B2. Flik 3 — sammanfattningar ur förvaltningsberättelsen

Efterfrågad av verksamheten och numera prioriterad: kassorna på rader och tre
textkolumner — årets viktigaste händelser, förväntad framtida utveckling och
arbetslöshetsnivå, samt hur kassan säkerställt kontroll av medelsförvaltningen.

**Avgjort: stödcitatet får en egen kolumn.** Varje sammanfattning följs av ett
ordagrant citat med sidhänvisning. Saknas stöd ska cellen läsa "Framgår inte av
årsredovisningen" i stället för att modellen skriver något rimligt.

**Varför kravet på citat inte är förhandlingsbart.** Det här är generering, inte
extraktion. Ingen aritmetik fångar ett fel, och en flytande, rimlig men
felaktig sammanfattning är betydligt svårare att upptäcka än en felaktig
siffra. En cell med ett citat går att kontrollera på tio sekunder; en cell utan
går inte att kontrollera alls.

**Den tredje kolumnen är tom i år.** Kravet på redovisning av
medelsförvaltningen väntas först nästa år. Tomma celler är därför rätt utfall,
men de måste vara tomma av rätt skäl.

**Maskeringen är inget hinder.** Kontrollerat mot Alfa-kassans maskerade fil:
den löpande texten i förvaltningsberättelsen är i stort sett orörd, eftersom
svärtningen träffar styrelse- och revisorstabellerna, inte prosan.

**Kostnad.** Ett extra modellanrop per dokument över redan uttagen text,
ungefär 3–4 USD per körning.

### B3. Kontrollera att antalet kassor stämmer med antalet dokument

Banderollen överst i bladet fångar dokument som pipelinen vägrar. Den fångar
inte en kassa vars namn inte går att lösa upp, eller två filer som hamnar i
samma kolumn. Räkna uppladdade dokument mot kolumner i utdata och säg till när
de inte går ihop, på samma plats och samma sätt som banderollen.

### B4. Register över återkommande anmärkningar

**Idén.** Spara anmärkningarna från varje körning i installationen, med antal
förekomster per kassa, nyckeltal och kontroll. Sortera fallande på antal.

**Vad det ger.** Skillnaden mellan brus och mönster blir synlig utan att någon
behöver minnas. En anmärkning som återkommer varje körning är något att
åtgärda; en som dykt upp en gång är sannolikt slumpvariation. Just den
skillnaden fick vi under hösten 2026 bara genom att en människa råkade känna
igen samma belopp fem körningar i rad.

**Två öppna ändar som registret hade hanterat.** `Summa tillgångar` för Vision
avvek med 99 670 tkr i en enda körning och aldrig mer. Den sexteckens term i
GS a-kassa som numera svärtas utan att någon vet om det var en person eller ett
vanligt ord. Båda ligger i dag som fotnoter i det här dokumentet, vilket är
fel plats.

**Hör ihop med B1.** Samma lagringsmekanism, samma fråga om vad som händer vid
ominstallation, och samma krav på att användaren ska veta vad som sparas.

---

## C. Senare

### C1. Köra dokumenten parallellt

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

## D. Beslut, inte kod

### D1. Dåliga skanningar

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

## E. Avklarat

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

### Öppna ändar, i väntan på registret i B4

`Summa tillgångar` för Vision avvek med 99 670 tkr från sina delposter i
körningen den 17 september 2026. Syntes inte före och inte efter.

Den sexteckens term i GS a-kassa som svärtas sedan 0058 utan att någon vet om
det var en person eller ett vanligt svenskt ord som NER tog fel på.

Båda är exempel på varför B4 behövs: i dag är det en människas minne som
avgör om något är ett mönster eller en engångsföreteelse.
