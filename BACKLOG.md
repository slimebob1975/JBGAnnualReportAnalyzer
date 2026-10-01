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

### B3. Enhet för belopp — delvis besvarad

**Beskedet.** Enligt ÅRL och BFN:s vägledningar ska belopp anges i hela kronor
eller tusental kronor, och vilket ska framgå av årsredovisningen, ofta som
"Belopp anges i tkr om inte annat anges" i sidhuvudet eller under
redovisningsprinciperna. Tkr är det vanliga, kronor förekommer hos några av de
minsta kassorna, och i statistikbilagan kan det variera. Hela kronor krävs
alltid i förslaget till resultatdisposition.

**Vad som gjorts.** Normaliseringen gissar utifrån vad de övriga kassorna
redovisar, vilket är den kvalificerade gissning verksamheten efterfrågat.

**Vad som återstår.** Dokumentet anger oftast själv sin enhet. Att läsa den
uppgiften ur texten vore ett starkare besked än medianen bland kassorna, och
skulle dessutom fånga en kassa som avviker utan att någon annan gör det. Kräver
att enheten hämtas vid extraktionen.

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
| 0050 | Enhetsnormaliseringen itererar mot en delad referens; faktorn väljs på logaritmiskt avstånd; Excel förvalt utdataformat | Kolumnen `Utbetald arbetslöshetsersättning` hamnar helt i kronor, noll kvarvarande anmärkningar om enhet mot tre tidigare |
| 0046 | Person- och samordningsnummer i alla former; organisationsnummer undantas; oberoende svep av utdata | Svepet tyst i 24 av 24 dokument; maskerade termer 1 018 → 994, exakt ett färre i 22 dokument, vilket är kassans eget organisationsnummer som tidigare svärtades |

### Latent fel som hittades på vägen

Faktorn för enhetsomräkning valdes på linjärt avstånd från 1, vilket straffar
en faktor som skjuter över målet långt hårdare än en som hamnar under. För
2 924 mot medianen 418 miljoner gav ×1000 avståndet 0,99 och ×1 000 000
avståndet 5,99, så det uppenbart felaktiga tusentalet vann. Syntes aldrig
förrän iterationen gjorde att fallet över huvud taget prövades. Rättat i 0050;
avståndet mäts nu logaritmiskt.

### Öppen observation utan åtgärd

`Summa tillgångar` för Vision avvek med 99 670 tkr från sina delposter i
körningen den 17 september. Syntes inte i tidigare körningar och inte i den
18 september. Noterat i väntan på om det återkommer.
