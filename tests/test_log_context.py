"""Tester för dokumentmärkningen i loggen.

Varje felsökning i det här projektet har byggt på att läsa ett dokuments rader
i ordning. Körs flera dokument samtidigt försvinner den ordningen, och då är
namnet på raden det enda som gör loggen läsbar igen.
"""

import io
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.src import JBGLogContext as log_context  # noqa: E402

FORMAT = "[%(levelname)s]%(document)s %(message)s"


def _logger(name):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(FORMAT))
    log_context.install(handler)
    log = logging.getLogger(name)
    log.handlers = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False
    return log, stream


def test_rader_utanfor_ett_dokument_ser_ut_som_forut():
    log, stream = _logger("utanfor")
    log.info("Städning av gamla jobb var 300s.")
    assert stream.getvalue() == "[INFO] Städning av gamla jobb var 300s.\n"


def test_rader_inom_ett_dokument_bar_dess_namn():
    log, stream = _logger("inom")
    with log_context.document("GS a-kassa Årsredovisning 2025(173658) (0).pdf"):
        log.info("Kör OCR")
    # Märkningen är det korta namnet, inte filnamnet i sin helhet.
    assert "[INFO] [GS a-kassa 2025] Kör OCR" in stream.getvalue()


def test_namnet_tas_bort_efterat():
    log, stream = _logger("efterat")
    with log_context.document("x.pdf"):
        log.info("under")
    log.info("efter")
    assert stream.getvalue().strip().endswith("[INFO] efter")


def test_namnet_tas_bort_aven_vid_fel():
    log, stream = _logger("vidfel")
    try:
        with log_context.document("x.pdf"):
            raise ValueError("något gick fel")
    except ValueError:
        pass
    log.info("efter")
    assert "[INFO] efter" in stream.getvalue()
    assert "[x.pdf] efter" not in stream.getvalue()


def test_fler_arbetare_blandar_inte_ihop_namnen():
    """Hela poängen: varje arbetare sätter sitt eget utan att veta om de
    andra."""
    log, stream = _logger("arbetare")
    started = threading.Barrier(4)

    def work(name):
        with log_context.document(name):
            started.wait()  # tvinga raderna att flätas
            log.info("arbetar")

    with ThreadPoolExecutor(max_workers=4) as pool:
        for name in ("a.pdf", "b.pdf", "c.pdf", "d.pdf"):
            pool.submit(work, name)

    lines = [line for line in stream.getvalue().split("\n") if line]
    assert len(lines) == 4
    assert {line.split("]")[1].strip(" [") for line in lines} == {
        "a", "b", "c", "d"
    }


def test_biblioteksrader_far_ocksa_faltet():
    """Filtret sitter på hanteraren, inte på en logger. Utan det kraschar
    formateringen på en KeyError så fort openai eller httpx loggar något."""
    log, stream = _logger("eget")
    annan = logging.getLogger("httpx.prov")
    annan.handlers = log.handlers
    annan.setLevel(logging.INFO)
    annan.propagate = False

    annan.info("HTTP Request: POST ...")
    assert "[INFO] HTTP Request" in stream.getvalue()


def test_filtret_laggs_inte_pa_tva_ganger():
    handler = logging.StreamHandler(io.StringIO())
    log_context.install(handler)
    log_context.install(handler)
    assert sum(
        isinstance(f, log_context.DocumentFilter) for f in handler.filters
    ) == 1


def test_tomt_namn_ger_ingen_markering():
    log, stream = _logger("tomt")
    with log_context.document(""):
        log.info("ingen fil")
    assert stream.getvalue() == "[INFO] ingen fil\n"


# ------------------------------------------------ märkningen ska vara kort
@pytest.mark.parametrize("filnamn,vantat", [
    # Kassans namn står först.
    ("Alfa-kassans årsredovisning 2025 signed(173301) (0).pdf",
     "Alfa-kassans 2025 s\u2026"),
    ("GS a-kassa Årsredovisning 2025(173658) (0).pdf", "GS a-kassa 2025"),
    ("Akademikernas Årsredovisning 2025(173007) (0)_ocr_masked.pdf",
     "Akademikernas 2025"),
    # Kassans namn står sist. Utan att "Årsredovisning" tas bort skulle det
    # kapas bort, och raden sa inte vilken kassa den gällde.
    ("Årsredovisning 2024 Lärarnas a-kassa(156545) (0).pdf",
     "2024 Lärarnas a-kas\u2026"),
    ("Årsredovisning Livs a-kassa 2025(175097) (0).pdf", "Livs a-kassa 2025"),
    # Understreck är ett ordtecken, så "ÅR" däremellan har inga ordgränser.
    ("Säljarnas_ÅR_2025-signed-document(174093) (0).pdf", "Säljarnas 2025-sign\u2026"),
    # Filnamnet säger ingenting om kassan. Då är ärendenumret det enda som
    # skiljer dokumenten åt, och får stå kvar.
    ("Årsredovisning 2025(172140) (0).pdf", "2025 172140"),
])
def test_filnamnet_kortas_till_det_som_skiljer_dokumenten_at(filnamn, vantat):
    assert log_context.shorten(filnamn) == vantat


def test_markningen_ryms_pa_raden():
    langt = "Årsredovisning för Arbetslöshetskassan Alfa 2025 slutgiltig(1) (0).pdf"
    assert len(log_context.shorten(langt)) <= log_context.MAX_TAG_LENGTH


def test_bindestreck_behalls():
    """"Alfa-kassans" läser bättre än "Alfa kassans"."""
    assert "Alfa-kassans" in log_context.shorten("Alfa-kassans ÅR(1) (0).pdf")


def test_ett_kort_namn_lamnas_som_det_ar():
    assert log_context.shorten("kort.pdf") == "kort"


def test_tomt_namn_ger_tom_markning():
    assert log_context.shorten("") == ""
    assert log_context.shorten(None) == ""


def test_arendenumret_behalls_nar_filnamnet_inte_namner_nagon_kassa():
    """Fyra av tjugofyra filer i en körning hette "Årsredovisning
    2025(nnnnnn)" och fick alla taggen "2025". I en flätad logg gick de inte
    att skilja åt — precis det märkningen fanns till för."""
    filer = [
        "Årsredovisning 2025(172140) (0).pdf",
        "Årsredovisning 2025(172422) (0).pdf",
        "Årsredovisning 2025(173056) (0).pdf",
        "Årsredovisning 2025(175139) (0).pdf",
    ]
    taggar = [log_context.shorten(f) for f in filer]

    assert len(set(taggar)) == len(filer), "taggarna måste gå att skilja åt"
    assert taggar[0] == "2025 172140"


def test_arendenumret_tas_bort_nar_kassan_framgar():
    """Numret är skräp så länge filnamnet säger något annat."""
    assert log_context.shorten(
        "Akademikernas Årsredovisning 2025(173007) (0).pdf"
    ) == "Akademikernas 2025"
