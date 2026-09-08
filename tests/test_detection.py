"""Tests for the local (non-LLM) page-offset and fiscal-year detection.

These replace 86% of the OpenAI calls the service used to make, so they are
worth pinning down. Any call to _make_openai_api_call here is a test failure.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pymupdf = pytest.importorskip("pymupdf")

from app.src.JBGAnnualReportAnalysis import JBGAnnualReportAnalyzer  # noqa: E402


def _analyzer() -> JBGAnnualReportAnalyzer:
    a = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)

    def _forbidden(*args, **kwargs):  # pragma: no cover
        raise AssertionError("the LLM fallback was used where it should not be")

    a._make_openai_api_call = _forbidden
    return a


def _long_body(prefix: str, lines: int = 30) -> str:
    """A page body that actually fits. insert_text clips a single long line at
    the page edge, which silently produced ~100 characters per page."""
    return "\n".join(f"{prefix} rad {n}: belopp {1000 + n} tkr" for n in range(lines))


def _report(tmp_path, pages, page_labels=None, name="ar.pdf"):
    """Build a PDF whose pages contain the given (body, footer) text."""
    doc = pymupdf.open()
    for body, footer in pages:
        page = doc.new_page()
        page.insert_text((72, 90), body, fontsize=11)
        if footer is not None:
            page.insert_text((300, page.rect.height - 40), str(footer), fontsize=9)
    if page_labels:
        doc.set_page_labels(page_labels)
    out = tmp_path / name
    doc.save(out)
    doc.close()
    return out


# ---------------------------------------------------------------- page offset
def test_offset_from_page_labels(tmp_path):
    """A cover and a contents page, then printed numbering starting at 1."""
    pdf = _report(
        tmp_path,
        [("Arsredovisning 2023", None), ("Innehall", None)] + [(f"Sida {i}", None) for i in range(1, 6)],
        page_labels=[
            {"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
            {"startpage": 2, "prefix": "", "style": "D", "firstpagenum": 1},
        ],
    )
    with pymupdf.open(pdf) as doc:
        assert JBGAnnualReportAnalyzer._offset_from_page_labels(doc) == 2
    assert _analyzer()._find_page_number_offset(pdf) == 2


def test_no_page_labels_returns_none(tmp_path):
    pdf = _report(tmp_path, [("Text", None)] * 3)
    with pymupdf.open(pdf) as doc:
        assert JBGAnnualReportAnalyzer._offset_from_page_labels(doc) is None


def test_offset_from_printed_footer_numbers(tmp_path):
    """No /PageLabels, but each page prints its own number in the footer,
    starting one behind the PDF page index."""
    pages = [("Framsida", None)] + [(f"Innehall pa sidan {i}", i) for i in range(1, 9)]
    pdf = _report(tmp_path, pages)
    with pymupdf.open(pdf) as doc:
        assert JBGAnnualReportAnalyzer._offset_from_printed_numbers(doc) == 1
    assert _analyzer()._find_page_number_offset(pdf) == 1


def test_offset_falls_back_to_default_without_evidence(tmp_path):
    """No labels, no printed numbers, and the LLM fallback is off by default:
    we must return the standard value rather than spending 15 API calls."""
    pdf = _report(tmp_path, [("Bara brodtext utan sidnummer", None)] * 6)
    analyzer = _analyzer()
    assert analyzer._find_page_number_offset(pdf) == JBGAnnualReportAnalyzer.PAGE_OFFSET


def test_margin_scan_ignores_tables_running_into_the_footer(tmp_path):
    """Several numbers in the margin means a table, not a page number."""
    pages = [(f"Rad {i}", "12 244 267 63 853") for i in range(8)]
    pdf = _report(tmp_path, pages)
    with pymupdf.open(pdf) as doc:
        assert JBGAnnualReportAnalyzer._offset_from_printed_numbers(doc) is None


# ---------------------------------------------------------------- fiscal year
@pytest.mark.parametrize(
    "phrase, expected",
    [
        ("Arsredovisning for rakenskapsaret 2023-01-01 - 2023-12-31", 2023),
        ("Balansrakning per 2024-12-31", 2024),
        ("Verksamhetsaret 2022 har praglats av", 2022),
    ],
)
def test_year_from_strong_patterns(tmp_path, phrase, expected):
    pdf = _report(tmp_path, [(phrase, None)] + [("Ovrig text", None)] * 3)
    assert _analyzer()._find_primary_year_from_pdf(pdf) == expected


def test_reporting_year_beats_comparison_column(tmp_path):
    """Multi-year overviews mention older years more often than the reporting
    year. The strong-pattern weighting must stop 2021 from winning."""
    body = (
        "Arsredovisning for rakenskapsaret 2024-01-01 - 2024-12-31\n"
        "Flerarsoversikt: 2021 2021 2021 2022 2022 2023 2024"
    )
    pdf = _report(tmp_path, [(body, None), ("2021 2021 2022 2023", None)])
    assert _analyzer()._find_primary_year_from_pdf(pdf) == 2024


def test_year_scan_reads_only_the_front_of_the_document(tmp_path):
    """Guard the page cap: a later year buried on page 30 must not win."""
    pages = [("Arsredovisning rakenskapsaret 2023-12-31", None)]
    pages += [("2019 2019 2019 2019 2019", None) for _ in range(30)]
    pdf = _report(tmp_path, pages)
    assert _analyzer()._find_primary_year_from_pdf(pdf) == 2023


def test_year_falls_back_when_no_year_present(tmp_path, monkeypatch):
    """With no year anywhere, the LLM fallback is allowed to run. Assert we
    reach it rather than silently returning a wrong year."""
    pdf = _report(tmp_path, [("Ingen arsangivelse alls", None)] * 3)
    analyzer = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)
    calls = []

    def fake_call(prompt, request, model=""):
        calls.append(model)
        return "-1"

    analyzer._make_openai_api_call = fake_call
    assert analyzer._find_primary_year_from_pdf(pdf) == JBGAnnualReportAnalyzer.FALLBACK_YEAR
    assert calls, "expected the LLM fallback to be consulted"


# ---------------------------------------------------------------- OCR gating
def test_ocr_skipped_when_text_layer_present(tmp_path):
    """The whole corpus in the sample log has a text layer, so OCR must not
    even be attempted."""
    pdf = _report(tmp_path, [(_long_body(f"Text pa sida {i}"), None) for i in range(5)])
    analyzer = _analyzer()

    def boom(*args, **kwargs):  # pragma: no cover
        raise AssertionError("OCR should not run on a document that has text")

    analyzer._run_ocr = boom
    text = analyzer._extract_text_from_pdf_from_pdf(analyzer._ensure_readable_pdf(pdf))
    assert "Text pa sida 0" in text
    assert len(text) >= JBGAnnualReportAnalyzer.MIN_USABLE_TEXT_CHARS


def test_scanned_document_is_refused_when_ocr_is_unavailable(tmp_path, monkeypatch):
    """Ten scanned reports in a 24-file run each burned model calls before
    anyone discovered there was no text to read."""
    from app.src import JBGAnnualReportAnalysis as mod
    from app.src.JBGAnnualReportExceptions import FileTypeException

    pdf = _report(tmp_path, [("", None)] * 10)
    monkeypatch.setattr(mod, "ocr_availability", lambda: (False, "tesseract saknas i PATH"))

    analyzer = _analyzer()
    with pytest.raises(FileTypeException) as excinfo:
        analyzer._ensure_readable_pdf(pdf)
    assert "saknar textlager" in excinfo.value.message
    assert "tesseract" in excinfo.value.message


def test_no_model_calls_are_made_on_an_unreadable_scan(tmp_path, monkeypatch):
    """_analyzer() raises if the model is called at all."""
    from app.src import JBGAnnualReportAnalysis as mod
    from app.src.JBGAnnualReportExceptions import FileTypeException

    pdf = _report(tmp_path, [("", None)] * 10)
    monkeypatch.setattr(mod, "ocr_availability", lambda: (False, "tesseract saknas"))
    with pytest.raises(FileTypeException):
        _analyzer()._ensure_readable_pdf(pdf)


def test_ocr_output_is_used_when_it_succeeds(tmp_path, monkeypatch):
    from app.src import JBGAnnualReportAnalysis as mod

    scan = _report(tmp_path, [("", None)] * 10, name="scan.pdf")
    # a stand-in for what OCR would have produced
    readable = _report(
        tmp_path,
        [(_long_body(f"Arsredovisning rakenskapsaret 2025-12-31 sida {i}"), None) for i in range(10)],
        name="scan_ocr.pdf",
    )
    monkeypatch.setattr(mod, "ocr_availability", lambda: (True, "ok"))

    analyzer = _analyzer()
    analyzer._run_ocr = lambda path: readable
    # OCR now happens in _ensure_readable_pdf, before masking, so extraction
    # runs against whatever that returns.
    text = analyzer._extract_text_from_pdf_from_pdf(analyzer._ensure_readable_pdf(scan))
    assert "Arsredovisning" in text
    assert len(text) >= JBGAnnualReportAnalyzer.MIN_USABLE_TEXT_CHARS


def test_a_text_layer_of_scanning_artefacts_is_refused(tmp_path, monkeypatch):
    """The scans in the corpus yielded 240-400 characters of noise, which used
    to be sent to the model as if it were a report."""
    from app.src import JBGAnnualReportAnalysis as mod
    from app.src.JBGAnnualReportExceptions import FileTypeException

    pdf = _report(tmp_path, [("x", None)] * 20)
    monkeypatch.setattr(mod, "ocr_availability", lambda: (True, "ok"))
    analyzer = _analyzer()
    analyzer._run_ocr = lambda path: pdf
    with pytest.raises(FileTypeException) as excinfo:
        analyzer._extract_text_from_pdf_from_pdf(analyzer._ensure_readable_pdf(pdf))
    assert "för lite" in excinfo.value.message


def test_year_is_derived_from_ocr_text_not_the_blank_original():
    """A latent bug: the year was read from the original scanned PDF, so it
    would have failed even with tesseract working perfectly."""
    analyzer = _analyzer()
    ocr_text = (
        "[Sida 1]\nArsredovisning for rakenskapsaret 2025-01-01 - 2025-12-31\n\n"
        "[Sida 2]\nBalansrakning\nSumma tillgangar 42 561"
    )
    assert analyzer._find_primary_year_from_text(ocr_text) == 2025


# ------------------------------------------------- follow-up: offset accuracy
def test_trivial_page_labels_are_ignored(tmp_path):
    """A single "arabic from page 0" rule is boilerplate, not evidence. On one
    real report it pushed the offset to 0 while the pages printed 1..n-1."""
    doc = pymupdf.open()
    for i in range(7):
        page = doc.new_page()
        if i:
            page.insert_text((300, page.rect.height - 40), str(i), fontsize=9)
    doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "D", "firstpagenum": 1}])
    pdf = tmp_path / "trivial.pdf"
    doc.save(pdf)
    doc.close()

    with pymupdf.open(pdf) as d:
        assert JBGAnnualReportAnalyzer._offset_from_page_labels(d) is None
    # the footer scan must now supply the real answer
    assert _analyzer()._find_page_number_offset(pdf) == 1


def test_genuine_page_labels_still_used(tmp_path):
    """A non-trivial rule set is real evidence and must still win."""
    pdf = _report(
        tmp_path,
        [("Omslag", None), ("Innehall", None)] + [(f"Brodtext {i}", None) for i in range(4)],
        page_labels=[
            {"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
            {"startpage": 2, "prefix": "", "style": "D", "firstpagenum": 1},
        ],
    )
    with pymupdf.open(pdf) as d:
        assert JBGAnnualReportAnalyzer._offset_from_page_labels(d) == 2


def test_trailing_line_scan_when_positions_are_unusable(tmp_path):
    """Simulates the 'Actualtext with no position' documents: the page number is
    in the text stream but a clipped rectangle will not find it."""
    doc = pymupdf.open()
    for i in range(8):
        page = doc.new_page()
        # number placed mid-page, so the footer clip misses it entirely
        page.insert_text((72, 300), f"Innehall\n{i}" if i else "Omslag", fontsize=10)
    pdf = tmp_path / "nopos.pdf"
    doc.save(pdf)
    doc.close()

    with pymupdf.open(pdf) as d:
        assert JBGAnnualReportAnalyzer._offset_from_printed_numbers(d) is None
        assert JBGAnnualReportAnalyzer._offset_from_trailing_lines(d) == 1


def test_masker_is_built_once_per_batch():
    analyzer = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)
    analyzer._masker = None
    built = []

    class FakeMasker:
        def __init__(self):
            built.append(1)

    import app.src.JBGAnnualReportAnalysis as mod

    original = mod.PDFMasker
    mod.PDFMasker = FakeMasker
    try:
        for _ in range(5):
            analyzer._get_masker()
    finally:
        mod.PDFMasker = original
    assert len(built) == 1, f"NER model built {len(built)} times for one batch"


# ------------------------------------------------- cleanup: chunk boundaries
def test_oversized_page_keeps_its_marker_on_every_sub_chunk():
    """Sub-chunks after the first used to start mid-page with no [Sida N]
    header, leaving the model nothing to put in the källa field."""
    analyzer = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)
    huge = "[Sida 7]\n" + " ".join(f"rad{i}" for i in range(4000))
    chunks = analyzer._chunk_text_by_pages(huge, max_tokens=400, model="gpt-4o")
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.lstrip().startswith("[Sida 7]"), chunk[:60]


def test_page_marker_helper():
    assert JBGAnnualReportAnalyzer._page_marker("[Sida 12]\ntext") == "[Sida 12]"
    assert JBGAnnualReportAnalyzer._page_marker("  [Sida iv]\ntext") == "[Sida iv]"
    assert JBGAnnualReportAnalyzer._page_marker("ingen markör här") == ""


def test_break_patterns_actually_match_real_text():
    """These strings go to str.find/str.rfind, so a regex among them is dead
    code. The list used to end with "\\n\\n[A-Z]" and r"\\. [A-ZÅÄÖ]", which
    never fired. Each pattern must be findable literally in ordinary text."""
    import inspect

    source = inspect.getsource(
        JBGAnnualReportAnalyzer._adjust_chunks_borders_for_safe_breaks
    )
    patterns = eval(  # noqa: S307 - reading our own literal
        source.split("break_patterns = ")[1].split("\n")[0]
    )
    # Pages are joined with a blank line, so a marker is preceded by a newline.
    sample = (
        "[Sida 8]\nFörvaltningsberättelse\n\n"
        "[Sida 9]\nBALANSRÄKNING\n\nSumma tillgångar 63 853\n"
        "Sida 10\nNot 12: Osäkra fordringar\nRubrik:\nText efter kolon.\n"
    )
    for pattern in patterns:
        assert sample.find(pattern) != -1, f"{pattern!r} matches nothing"

    # the two regex-shaped entries must be gone
    assert not any("A-Z" in p for p in patterns)


def test_removed_methods_are_actually_gone():
    """These were unreachable after the structured-output switch."""
    for name in [
        "_chunk_text",
        "_merge_json_objects",
        "_deep_merge_json_objects_simple",
        "_is_valid_numeric",
        "_document_contains_retreivable_text",
        "get_permitted_temperature",
    ]:
        assert not hasattr(JBGAnnualReportAnalyzer, name), name


# ----------------------------------------------------- OCR availability
def test_ocr_availability_explains_a_missing_tesseract(monkeypatch):
    """The failure a real run hit ten times, with no hint about the fix."""
    from app.src import JBGAnnualReportAnalysis as mod

    mod.ocr_availability.cache_clear()
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    available, reason = mod.ocr_availability()
    mod.ocr_availability.cache_clear()

    assert available is False
    assert "tesseract" in reason
    # actionable on every platform the team might use
    assert "PATH" in reason and "apt install" in reason and "brew" in reason


def test_ocr_availability_notices_a_missing_ghostscript(monkeypatch):
    from app.src import JBGAnnualReportAnalysis as mod

    mod.ocr_availability.cache_clear()
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/tesseract" if name == "tesseract" else None)
    available, reason = mod.ocr_availability()
    mod.ocr_availability.cache_clear()

    assert available is False
    assert "ghostscript" in reason


def test_ocr_availability_is_cached():
    from app.src import JBGAnnualReportAnalysis as mod

    mod.ocr_availability.cache_clear()
    first = mod.ocr_availability()
    assert mod.ocr_availability() is first
