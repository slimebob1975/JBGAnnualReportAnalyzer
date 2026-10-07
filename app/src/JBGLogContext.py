"""Vilket dokument en loggrad gäller.

Varje felsökning i det här projektet har byggt på att läsa ett dokuments rader
i ordning. Maskeringen som läckte namn, svärtningen som bara träffade några
förekomster av en term, Lärarnas årsredovisning som fick fel räkenskapsår -
inget av det syntes i koden, bara i loggen, och bara för att raderna kom i den
ordning de gjorde.

Körs flera dokument samtidigt försvinner den ordningen. Därför detta: varje
rad får dokumentets namn, och loggen går att följa igen även när den är
flätad. Byggt före trådningen och inte efter, eftersom det är verktyget man
behöver just när något går fel.

Namnet hålls i en ContextVar, som är trådlokal av sig själv. Varje arbetare
sätter sitt eget utan att veta om de andra.
"""

import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar

# Ärendenummer, kopienummer och filändelse säger ingenting om vilket dokument
# raden gäller, och står på varenda rad. "Alfa-kassans årsredovisning 2025
# signed(173301) (0).pdf" blir "Alfa-kassans årsred…".
NOISE = re.compile(
    r"\(\d{5,}\)|\(\d\)|\.pdf$|_ocr(_masked)?|_masked", re.IGNORECASE
)
# "Årsredovisning" står i nästan varje filnamn och skiljer inget från något.
# Tas det bort ryms kassans namn även när det står sist: "Årsredovisning 2024
# Lärarnas a-kassa" blir "2024 Lärarnas a-kas…" i stället för "Årsredovisning
# 2024…", som inte säger vilken kassa raden gäller.
GENERIC = re.compile(r"\b[åa]rsredovisning(en)?\b|\b[åa]r\b", re.IGNORECASE)
# Understreck är ett ordtecken i reguljära uttryck, så "ÅR" mellan två
# understreck har inga ordgränser. Separatorerna jämnas därför ut först.
# Bindestreck lämnas kvar: "Alfa-kassans" läser bättre än "Alfa kassans".
SEPARATORS = re.compile(r"[\s_]+")
MAX_TAG_LENGTH = 20

current_document: ContextVar[str] = ContextVar("current_document", default="")


class DocumentFilter(logging.Filter):
    """Lägger till fältet `document` på varje post.

    Sitter på hanterarna och inte på en enskild logger, så att även rader
    från openai, httpx och andra bibliotek får fältet. Utan det kraschar
    formateringen på en KeyError så fort någon annan loggar något.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        name = shorten(current_document.get())
        record.document = f" [{name}]" if name else ""
        return True


def shorten(name: str) -> str:
    """Så mycket av filnamnet som behövs för att veta vilket dokument det är.

    Hela namnet stod på varje rad, och fyrtio av dess femtiofyra tecken var
    desamma varje gång. Det som skiljer dokumenten åt står först.
    """
    trimmed = SEPARATORS.sub(" ", NOISE.sub(" ", name or ""))
    trimmed = SEPARATORS.sub(" ", GENERIC.sub(" ", trimmed)).strip(" _-")
    if len(trimmed) <= MAX_TAG_LENGTH:
        return trimmed
    return trimmed[:MAX_TAG_LENGTH - 1].rstrip() + "\u2026"


@contextmanager
def document(name: str):
    """Märk alla rader inom blocket med dokumentets namn."""
    token = current_document.set(name or "")
    try:
        yield
    finally:
        current_document.reset(token)


def install(*handlers) -> None:
    """Sätt filtret på hanterarna, eller på rotloggerns om inga anges."""
    targets = handlers or logging.getLogger().handlers
    for handler in targets:
        if not any(isinstance(f, DocumentFilter) for f in handler.filters):
            handler.addFilter(DocumentFilter())
