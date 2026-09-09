"""Generate nyckeltalsdefinitioner.json from IAFFS 2026:1, bilaga 1 and 2.

Scope, per the specification and the instruction that came with it:
  * bilaga 1: resultaträkning, balansräkning, noter 1-10
  * bilaga 2: all statistics
  * NOT the redovisningsprinciper page, and no items a fund adds beyond the
    minimum the föreskrift requires
  * current financial year only, never the comparison year

Names must be unique: they are an enum in the response schema, and the source
document reuses "SUMMA" eight times plus "Övriga fordringar", "Övriga skulder",
"Övriga externa kostnader", "Källskatt arbetslöshetsersättning", "Årets
avstående från återkrav" and "Antal beslut" twice each. Note items are
therefore prefixed with their note number, which also reads well as a
spreadsheet row.

"Delposter" gives a sum's components with a coefficient, so one generic
validation rule can check every subtotal in the specification instead of a
hand-written rule per identity.
"""

import json
from pathlib import Path

RR = "📈 Resultaträkning"
BR = "📊 Balansräkning"
NOT = "🗒 Noter"
STAT = "👥 Statistik (bilaga 2)"

IN_RR = "Återfinns i resultaträkningen."
IN_BR = "Återfinns i balansräkningen."


def m(name, group, desc, instr="", alt=None, delposter=None, unit=None):
    entry = {
        "Nyckeltal": name,
        "Alternativa benämningar": alt or [],
        "Beskrivning": desc,
        "Grupp": group,
    }
    if delposter:
        entry["Delposter"] = delposter
        entry["Formel"] = " ".join(
            f"{'+' if c > 0 else '-'} {k}" for k, c in delposter.items()
        ).lstrip("+ ")
    if unit:
        entry["Enhet"] = unit
    entry["Specifika instruktioner"] = instr
    return entry


metrics = []

# ---------------------------------------------------------------- bilaga 1: RR
metrics += [
    m("Medlemsavgifter", RR, "Intäkter från medlemsavgifter.", IN_RR + " Under INTÄKTER."),
    m("Övriga intäkter", RR, "Andra intäkter än medlemsavgifter.",
      IN_RR + " Under INTÄKTER. Specificeras i not 1."),
    m("Summa intäkter", RR, "Summan av rörelsens intäkter.",
      IN_RR, delposter={"Medlemsavgifter": 1, "Övriga intäkter": 1}),

    m("Personalkostnader", RR, "Kostnader för personal.",
      IN_RR + " Anges som ett positivt belopp. Specificeras i not 2."),
    m("Övriga externa kostnader", RR, "Externa administrationskostnader.",
      IN_RR + " Anges som ett positivt belopp. Specificeras i not 3. "
      "Förväxla inte med posten med samma namn i not 3, som är en delpost."),
    m("Avskrivningar", RR, "Avskrivningar på anläggningstillgångar.",
      IN_RR + " Anges som ett positivt belopp."),
    m("Summa administrationskostnader", RR, "Summan av administrationskostnaderna.",
      IN_RR + " Anges som ett positivt belopp.",
      alt=["Administrationskostnader"],
      delposter={"Personalkostnader": 1, "Övriga externa kostnader": 1, "Avskrivningar": 1}),

    m("Resultat före avgifter till staten", RR, "Intäkter minus administrationskostnader.",
      IN_RR, delposter={"Summa intäkter": 1, "Summa administrationskostnader": -1}),

    m("Finansieringsavgift", RR, "Avgift till staten.",
      IN_RR + " Under AVGIFTER TILL STATEN. Anges som ett positivt belopp."),
    m("Summa avgifter till staten", RR, "Summan av avgifterna till staten.",
      IN_RR + " Anges som ett positivt belopp.",
      delposter={"Finansieringsavgift": 1}),

    m("Resultat före finansiella poster", RR, "Resultat efter avgifter till staten.",
      IN_RR, delposter={"Resultat före avgifter till staten": 1,
                        "Summa avgifter till staten": -1}),

    m("Finansiella intäkter", RR, "Ränteintäkter och liknande.",
      IN_RR + " Specificeras i not 4."),
    m("Finansiella kostnader", RR, "Räntekostnader och liknande.",
      IN_RR + " Anges som ett positivt belopp."),
    m("Summa finansiella poster", RR, "Netto av finansiella intäkter och kostnader.",
      IN_RR +
      " Detta är en NETTOPOST och ska anges med korrekt matematiskt tecken: "
      "positiv när intäkterna överstiger kostnaderna, negativ när kostnaderna "
      "överstiger intäkterna. Skriver dokumentet beloppet utan tecken, eller "
      "med omvänt tecken mot vad delposterna ger, ange ändå det matematiskt "
      "riktiga värdet och förklara i kommentaren.",
      delposter={"Finansiella intäkter": 1, "Finansiella kostnader": -1}),

    m("Resultat före poster arbetslöshetsförsäkringen", RR,
      "Resultat innan försäkringsposterna.", IN_RR,
      delposter={"Resultat före finansiella poster": 1, "Summa finansiella poster": 1}),

    m("Statligt bidrag till arbetslöshetsersättning", RR,
      "Statsbidrag som finansierar utbetald ersättning.",
      IN_RR + " Under POSTER ARBETSLÖSHETSFÖRSÄKRINGEN. Specificeras i not 5."),
    m("Kostnad arbetslöshetsersättning", RR, "Utbetald arbetslöshetsersättning.",
      IN_RR + " Anges som ett positivt belopp. Specificeras i not 6."),
    m("Kostnad ej statsbidragsberättigad arbetslöshetsersättning", RR,
      "Ersättning som inte täcks av statsbidrag.",
      IN_RR + " Anges som ett positivt belopp."),
    m("Summa poster arbetslöshetsförsäkringen", RR,
      "Netto av försäkringsposterna.", IN_RR +
      " Detta är en NETTOPOST och ska anges med korrekt matematiskt tecken: "
      "positiv när intäkterna överstiger kostnaderna, negativ när kostnaderna "
      "överstiger intäkterna. Skriver dokumentet beloppet utan tecken, eller "
      "med omvänt tecken mot vad delposterna ger, ange ändå det matematiskt "
      "riktiga värdet och förklara i kommentaren.",
      delposter={"Statligt bidrag till arbetslöshetsersättning": 1,
                 "Kostnad arbetslöshetsersättning": -1,
                 "Kostnad ej statsbidragsberättigad arbetslöshetsersättning": -1}),

    m("Årets resultat", RR, "Årets resultat.",
      "Återfinns sist i resultaträkningen och även i balansräkningen under EGET KAPITAL. "
      "Ange samma belopp oavsett var det hämtas.",
      delposter={"Resultat före poster arbetslöshetsförsäkringen": 1,
                 "Summa poster arbetslöshetsförsäkringen": 1}),
]

# ---------------------------------------------------------------- bilaga 1: BR
metrics += [
    m("Immateriella anläggningstillgångar", BR, "Immateriella tillgångar.", IN_BR),
    m("Materiella anläggningstillgångar", BR, "Materiella tillgångar.", IN_BR),
    m("Andra långfristiga värdepappersinnehav", BR,
      "Delpost under finansiella anläggningstillgångar.",
      IN_BR + " Detta är en ANLÄGGNINGStillgång och får inte förväxlas med "
      "Övriga kortfristiga placeringar."),
    m("Finansiella anläggningstillgångar", BR,
      "Summan av de finansiella anläggningstillgångarna.",
      IN_BR + " Redovisar kassan bara raden 'Andra långfristiga "
      "värdepappersinnehav' är beloppen desamma.",
      delposter={"Andra långfristiga värdepappersinnehav": 1}),
    m("Summa anläggningstillgångar", BR, "Summan av anläggningstillgångarna.", IN_BR,
      delposter={"Immateriella anläggningstillgångar": 1,
                 "Materiella anläggningstillgångar": 1,
                 "Finansiella anläggningstillgångar": 1}),

    m("Fordringar statligt bidrag till arbetslöshetsersättning", BR,
      "Fordran på staten avseende utbetald ersättning.", IN_BR + " Under FORDRINGAR."),
    m("Fordringar felaktig arbetslöshetsersättning", BR,
      "Fordran avseende felaktigt utbetald ersättning.",
      IN_BR + " Under FORDRINGAR. Specificeras i not 7."),
    m("Fordringar ränta återkrav felaktig arbetslöshetsersättning", BR,
      "Upplupen ränta på återkrav.", IN_BR + " Under FORDRINGAR."),
    m("Fordringar medlemsavgifter", BR, "Fordran avseende medlemsavgifter.",
      IN_BR + " Under FORDRINGAR.",
      alt=["Fordringar medlemsavgift"]),
    m("Övriga fordringar", BR, "Andra fordringar.",
      IN_BR + " Under FORDRINGAR. Specificeras i not 8. Detta är balansräkningens "
      "totalpost, inte delposten med samma namn i not 8."),
    m("Förutbetalda kostnader och upplupna intäkter", BR, "Interimsfordringar.",
      IN_BR + " Under FORDRINGAR."),
    m("Andra kortfristiga placeringar", BR, "Kortfristiga placeringar.",
      IN_BR + " Under KORTFRISTIGA PLACERINGAR. Saknas posten, eller finns rubriken "
      "utan belopp, ska nyckeltalet utelämnas. Hämta aldrig beloppet från "
      "Andra långfristiga värdepappersinnehav eller från Kassa och bank.",
      alt=["Övriga kortfristiga placeringar"]),
    m("Kassa och bank", BR, "Likvida medel.", IN_BR + " Under OMSÄTTNINGSTILLGÅNGAR."),
    m("Summa omsättningstillgångar", BR, "Summan av omsättningstillgångarna.", IN_BR,
      alt=["Omsättningstillgångar"],
      delposter={"Fordringar statligt bidrag till arbetslöshetsersättning": 1,
                 "Fordringar felaktig arbetslöshetsersättning": 1,
                 "Fordringar ränta återkrav felaktig arbetslöshetsersättning": 1,
                 "Fordringar medlemsavgifter": 1,
                 "Övriga fordringar": 1,
                 "Förutbetalda kostnader och upplupna intäkter": 1,
                 "Andra kortfristiga placeringar": 1,
                 "Kassa och bank": 1}),
    m("Summa tillgångar", BR, "Balansomslutning, tillgångssidan.",
      IN_BR, alt=["Balansomslutning"],
      delposter={"Summa anläggningstillgångar": 1, "Summa omsättningstillgångar": 1}),

    m("Eget kapital vid räkenskapsårets början", BR, "Ingående eget kapital.",
      IN_BR + " Under EGET KAPITAL."),
    m("Summa eget kapital", BR, "Utgående eget kapital.", IN_BR, alt=["Eget kapital"],
      delposter={"Eget kapital vid räkenskapsårets början": 1, "Årets resultat": 1}),

    m("Avsättningar felaktig arbetslöshetsersättning", BR,
      "Avsättning för felaktigt utbetald ersättning.",
      IN_BR + " Under AVSÄTTNINGAR. Specificeras i not 9."),
    m("Summa avsättningar", BR, "Summan av avsättningarna.", IN_BR,
      alt=["Utgående avsättningar"],
      delposter={"Avsättningar felaktig arbetslöshetsersättning": 1}),

    m("Skulder arbetslöshetsersättning", BR, "Skuld avseende ersättning.",
      IN_BR + " Under SKULDER."),
    m("Skulder finansieringsavgift", BR, "Skuld avseende finansieringsavgift.",
      IN_BR + " Under SKULDER."),
    m("Leverantörsskulder", BR, "Skulder till leverantörer.", IN_BR + " Under SKULDER."),
    m("Övriga skulder", BR, "Andra skulder.",
      IN_BR + " Under SKULDER. Specificeras i not 10. Detta är balansräkningens "
      "totalpost, inte delposten med samma namn i not 10."),
    m("Upplupna kostnader och förutbetalda intäkter", BR, "Interimsskulder.",
      IN_BR + " Under SKULDER."),
    m("Summa skulder", BR, "Summan av skulderna, exklusive avsättningar.",
      IN_BR + " Ange skulderna EXKLUSIVE avsättningar. Redovisar dokumentet bara "
      "'Summa avsättningar och skulder', ange den summan minus summa avsättningar "
      "och skriv i kommentaren hur beloppet räknats fram.",
      alt=["Skulder"],
      delposter={"Skulder arbetslöshetsersättning": 1, "Skulder finansieringsavgift": 1,
                 "Leverantörsskulder": 1, "Övriga skulder": 1,
                 "Upplupna kostnader och förutbetalda intäkter": 1}),
    m("Summa eget kapital, avsättningar och skulder", BR,
      "Balansomslutning, skuldsidan.", IN_BR,
      delposter={"Summa eget kapital": 1, "Summa avsättningar": 1, "Summa skulder": 1}),
]

# ------------------------------------------------------------- bilaga 1: noter
# (föreskriftens notnummer, notens rubrik, delposter, summeringsrad, den post i
# RR/BR som noten specificerar). The note number is the föreskrift's own and
# is NOT used in the metric name: funds add notes of their own, so their note 7
# is rarely the föreskrift's note 7. Naming by subject keeps the metric stable
# whatever the report numbers it.
#
# "links_to" is the row the note explains; the note's total must equal it.
# Note 2 is excluded: medelantal anställda is a headcount, not an amount.
NOTE_SPEC = [
    (1, "Övriga intäkter", ["Annat statligt bidrag"], "Summa", "Övriga intäkter"),
    (2, "Personalkostnader",
     ["Medelantal anställda kvinnor", "Medelantal anställda män"], "Summa", None),
    (3, "Övriga externa kostnader",
     ["Befarade och konstaterade förluster medlemsavgifter", "Övriga externa kostnader"],
     "Summa", "Övriga externa kostnader"),
    (4, "Finansiella intäkter",
     ["Ränta på återkrav felaktig arbetslöshetsersättning", "Övriga ränteintäkter"],
     "Summa", "Finansiella intäkter"),
    (5, "Statligt bidrag till arbetslöshetsersättning",
     ["Statligt bidrag till arbetslöshetsersättning",
      "Periodiserat statligt bidrag till arbetslöshetsersättning",
      "Återbetald arbetslöshetsersättning",
      "Förändring värdereglering avsättning felaktig arbetslöshetsersättning"],
     "Summa", "Statligt bidrag till arbetslöshetsersättning"),
    (6, "Kostnad arbetslöshetsersättning",
     ["Arbetslöshetsersättning", "Periodiserad arbetslöshetsersättning",
      "Återbetald arbetslöshetsersättning",
      "Förändring värdereglering fordran felaktig arbetslöshetsersättning"],
     "Summa", "Kostnad arbetslöshetsersättning"),
    (7, "Fordringar felaktig arbetslöshetsersättning",
     ["Ingående fordringar", "Årets tillkommande fordringar", "Årets inbetalningar",
      "Årets avstående från återkrav", "Osäkra fordringar"],
     "Utgående fordringar", "Fordringar felaktig arbetslöshetsersättning"),
    (8, "Övriga fordringar",
     ["Källskatt arbetslöshetsersättning", "Övriga fordringar"],
     "Summa", "Övriga fordringar"),
    (9, "Avsättning felaktig arbetslöshetsersättning",
     ["Ingående avsättningar", "Årets tillkommande avsättningar", "Årets betalningar",
      "Årets avstående från återkrav", "Justering till följd av osäkra fordringar"],
     "Utgående avsättningar", "Avsättningar felaktig arbetslöshetsersättning"),
    (10, "Övriga skulder",
     ["Källskatt arbetslöshetsersättning", "Övriga skulder"], "Summa", "Övriga skulder"),
]


# Rows that reduce the balance in a roll-forward note. The reports disagree:
# tested against six funds, "as reported" reproduced the stated total for two,
# "always negative" for two, and "excluding osäkra fordringar" for three. No
# convention fits every fund, so the Swedish one is imposed and the sum check
# reports whoever deviates.
DEDUCTIONS = {
    "Årets inbetalningar",
    "Årets avstående från återkrav",
    "Osäkra fordringar",
    "Årets betalningar",
    "Justering till följd av osäkra fordringar",
}
SIGN_RULE = (
    " Posten minskar saldot och ska anges som ett NEGATIVT belopp, även om "
    "dokumentet skriver den utan minustecken. Osäkra fordringar redovisas "
    "enligt svensk praxis som ett avdrag i balansräkningen."
)


def note_metric(subject: str, item: str) -> str:
    return f"Not till {subject}: {item}"


for number, title, items, total, links_to in NOTE_SPEC:
    reference = (
        f"Noten som specificerar {title} i resultat- eller balansräkningen "
        f"(nummer {number} i föreskriften; kassans egen numrering kan skilja sig)."
    )
    for item in items:
        alt = []
        instr = f"Delpost i {reference}"
        if item in DEDUCTIONS:
            instr += SIGN_RULE
        if item == "Årets avstående från återkrav":
            alt = ["Eftergift", "Årets eftergift", "Eftergivna belopp"]
            instr += (
                " Vissa kassor kallar posten 'eftergift'. Begreppet är utfasat men "
                "ska tolkas som synonymt med avstående från återkrav."
            )
        metrics.append(
            m(note_metric(title, item), NOT, f"{item}, enligt noten till {title}.",
              instr, alt=alt)
        )

    # No component sum for a note.
    #
    # The föreskrift is a minimum and funds add rows of their own, so the
    # delposter we know about cannot be expected to reach the note's total.
    # Measured on six funds: the sum check failed on five of six for two
    # separate notes while the note-to-statement check passed on both. It
    # produced about twenty findings carrying no information.
    #
    # The note's total against the row it explains is the check that holds
    # regardless of how many rows a fund adds.
    entry = m(note_metric(title, total), NOT, f"{total}, enligt noten till {title}.",
              f"Summeringsraden i {reference} Rapportera raden som den står i "
              "dokumentet; räkna inte om den.")
    if links_to:
        # The note explains one row of the statements, so its total must equal it.
        entry["Motsvarar"] = links_to
        entry["Specifika instruktioner"] += (
            f" Summan ska stämma med posten '{links_to}' i resultat- eller "
            "balansräkningen."
        )
    metrics.append(entry)

# ---------------------------------------------------------------- bilaga 2
STAT_SPEC = [
    ("Totalt antal medlemmar 31 december", "antal", "Under MEDLEMMAR."),
    ("Antal medlemmar varav män", "antal", "Under MEDLEMMAR, raden 'varav män'."),
    ("Antal medlemmar varav kvinnor", "antal", "Under MEDLEMMAR, raden 'varav kvinnor'."),
    ("Antal nytillkomna medlemmar", "antal", "Under MEDLEMMAR."),
    ("Antal som begärt utträde", "antal", "Under MEDLEMMAR."),
    ("Antal som utträtt pga bristande betalning", "antal", "Under MEDLEMMAR."),
    ("Antal uteslutna medlemmar", "antal", "Under MEDLEMMAR."),
    ("Medlemsavgift per månad 31 december", "kronor",
     "Under MEDLEMSAVGIFT. Ange beloppet i kronor per månad, inte tusental."),
    ("Utbetald arbetslöshetsersättning", "belopp", "Under ARBETSLÖSHETSERSÄTTNING."),
    ("Antal ersättningsdagar", "antal", "Under ARBETSLÖSHETSERSÄTTNING."),
    ("Antal medlemmar som fått ersättning", "antal", "Under ARBETSLÖSHETSERSÄTTNING."),
    ("Antal beslut underrättelser", "antal", "Under UNDERRÄTTELSER, raden 'Antal beslut'."),
    ("Antal sanktion", "antal", "Under UNDERRÄTTELSER."),
    ("Antal beslut omprövning", "antal", "Under OMPRÖVNINGAR, ÖVERKLAGAN, DOMAR."),
    ("Antal beslut omprövning med ändring", "antal",
     "Under OMPRÖVNINGAR, ÖVERKLAGAN, DOMAR."),
    ("Antal överklagan", "antal", "Under OMPRÖVNINGAR, ÖVERKLAGAN, DOMAR."),
    ("Antal domar", "antal", "Under OMPRÖVNINGAR, ÖVERKLAGAN, DOMAR."),
    ("Antal beslut återkrav", "antal", "Under ÅTERKRAV, raden 'Antal beslut'."),
    ("Totalt belopp återkrav", "belopp", "Under ÅTERKRAV, raden 'Totalt belopp'."),
    ("Antal beslut återkrav inlämnade till Kronofogdemyndigheten", "antal",
     "Under ÅTERKRAV."),
    ("Totalt belopp återkrav inlämnade till Kronofogdemyndigheten", "belopp",
     "Under ÅTERKRAV."),
    ("Antal anmälningar till Polismyndigheten", "antal", "Under POLISANMÄLNINGAR."),
    ("Totalt belopp anmälningar till Polismyndigheten", "belopp",
     "Under POLISANMÄLNINGAR, raden 'Totalt belopp av anmälningar som är "
     "inlämnade till Polismyndigheten'."),
]
for name, unit, instr in STAT_SPEC:
    metrics.append(
        m(name, STAT, f"Uppgift ur bilaga 2: {name.lower()}.",
          instr + " Uppgiften är ett antal, inte ett belopp." if unit == "antal"
          else instr, unit=unit)
    )

# ------------------------------------------------------------------- checks
names = [e["Nyckeltal"] for e in metrics]
duplicates = {n for n in names if names.count(n) > 1}
assert not duplicates, f"names must be unique for the schema enum: {duplicates}"

known = set(names)
for entry in metrics:
    for component in entry.get("Delposter", {}):
        assert component in known, f"{entry['Nyckeltal']} refers to unknown {component!r}"

out = Path("app/prompt/json/nyckeltalsdefinitioner.json")
out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"wrote {len(metrics)} metrics")
from collections import Counter
for g, n in Counter(e["Grupp"] for e in metrics).most_common():
    print(f"   {g:34} {n}")
print(f"   sums with components: {sum(1 for e in metrics if e.get('Delposter'))}")
