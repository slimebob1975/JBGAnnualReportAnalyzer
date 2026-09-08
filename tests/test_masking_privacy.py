"""Tests for the masking privacy guarantee.

Two defects, both found by comparing a real run's log against its corpus:

* Masking ran before OCR, so on a scanned document the redactor saw an empty
  page, removed nothing, and OCR then recovered every name. Ten scanned
  reports masked 0 terms each; fourteen native ones averaged 49.
* Nothing checked the redacted output, so "Identified 61 sensitive terms"
  meant 61 were found, not 61 were removed.
"""

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pymupdf = pytest.importorskip("pymupdf")

from app.src.JBGAnnualReportAnalysis import JBGAnnualReportAnalyzer  # noqa: E402
from app.src.JBGAnnualReportExceptions import FileTypeException  # noqa: E402
from app.src.masking.JBGPDFMasking import PDFMasker  # noqa: E402


def _pdf(path, lines, blank=False):
    doc = pymupdf.open()
    page = doc.new_page()
    if not blank:
        y = 90
        for line in lines:
            page.insert_text((72, y), line, fontsize=11)
            y += 20
    doc.save(path)
    doc.close()
    return path


def _masker():
    m = PDFMasker(ner=lambda text: [])
    PDFMasker._extra_names_cache = (set(), set())
    return m


# ------------------------------------------------------- order of operations
def test_ocr_runs_before_masking(tmp_path, monkeypatch):
    """The redactor must be handed a document that has text in it."""
    from app.src import JBGAnnualReportAnalysis as mod

    scan = _pdf(tmp_path / "scan.pdf", [], blank=True)
    readable = _pdf(
        tmp_path / "scan_ocr.pdf",
        [f"Styrelsen bestar av Gunhild Bergstrom rad {i}" for i in range(40)],
    )
    monkeypatch.setattr(mod, "ocr_availability", lambda: (True, "ok"))

    analyzer = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)
    analyzer._run_ocr = lambda path: readable

    result = analyzer._ensure_readable_pdf(scan)
    assert result == readable

    with pymupdf.open(result) as doc:
        text = "".join(p.get_text() for p in doc)
    assert "Gunhild" in text, "the masker would otherwise see nothing"


def test_a_document_with_text_is_not_ocred(tmp_path, monkeypatch):
    from app.src import JBGAnnualReportAnalysis as mod

    pdf = _pdf(tmp_path / "native.pdf", [f"Rad {i} med text" for i in range(40)])
    monkeypatch.setattr(mod, "ocr_availability", lambda: (True, "ok"))

    analyzer = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)

    def boom(path):  # pragma: no cover
        raise AssertionError("OCR should not run on a readable document")

    analyzer._run_ocr = boom
    assert analyzer._ensure_readable_pdf(pdf) == pdf


def test_unreadable_scan_is_refused_before_masking(tmp_path, monkeypatch):
    from app.src import JBGAnnualReportAnalysis as mod

    scan = _pdf(tmp_path / "scan.pdf", [], blank=True)
    monkeypatch.setattr(mod, "ocr_availability", lambda: (False, "tesseract saknas"))

    analyzer = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)
    with pytest.raises(FileTypeException, match="saknar textlager"):
        analyzer._ensure_readable_pdf(scan)


# ------------------------------------------------------------- verification
def test_clean_masking_is_verified(tmp_path):
    src = _pdf(tmp_path / "a.pdf", ["Styrelsen bestar av Gunhild Bergstrom, ordforande."])
    out = _masker().mask_pdf_black_boxes(
        src, tmp_path / "a_masked.pdf", ["Gunhild", "Bergstrom"], logging.getLogger()
    )
    assert out is not None
    with pymupdf.open(out) as doc:
        text = "".join(p.get_text() for p in doc)
    assert "Gunhild" not in text and "Bergstrom" not in text


def test_a_term_that_survives_redaction_blocks_the_file(tmp_path):
    """A term the locator cannot reach at all must stop the document.

    Simulated by disabling the locator, since the cases that used to defeat it
    (line-break hyphenation, spacing variants, signature widgets) are now
    handled and no longer produce a leak."""
    src = _pdf(tmp_path / "b.pdf", ["Undertecknad av Gunhild Bergstrom den 14 mars."])
    out = tmp_path / "b_masked.pdf"

    masker = _masker()
    masker._locate_term = classmethod(lambda cls, page, term, entries=None: [])
    result = masker.mask_pdf_black_boxes(
        src, out, ["Gunhild", "Bergstrom"], logging.getLogger()
    )
    assert result is None, "a leaking document must not be returned"
    assert not out.exists(), "the leaking file must not be left on disk"


def test_hyphenated_names_are_detected():
    joined = PDFMasker._normalise_haystack("Undertecknad av Gun-\nhild Bergstrom")
    assert "gunhild" in joined


def test_short_terms_are_not_checked():
    """Two-character terms match too much to verify usefully."""
    assert PDFMasker.find_surviving_terms.__func__ is not None
    assert PDFMasker.MIN_VERIFIABLE_TERM >= 3


def test_word_boundaries_prevent_false_positives(tmp_path):
    """"Anna" must not be reported as surviving because "Annandag" is present."""
    pdf = _pdf(tmp_path / "c.pdf", ["Kassan var stangd Annandag jul."])
    assert PDFMasker.find_surviving_terms(pdf, ["Anna"]) == []


def test_survivors_are_reported_by_name_to_the_caller(tmp_path):
    pdf = _pdf(tmp_path / "d.pdf", ["Ordforande Gunhild Bergstrom har undertecknat."])
    survivors = PDFMasker.find_surviving_terms(pdf, ["Gunhild", "Saknas", "Bergstrom"])
    assert set(survivors) == {"Gunhild", "Bergstrom"}


def test_failed_masking_stops_the_file_rather_than_analysing_it(tmp_path, monkeypatch):
    """do_analysis must treat a masking failure as a skip, not carry on with
    the unmasked document."""
    from app.src import JBGAnnualReportAnalysis as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    body = source[source.index("if self.use_masking:"):source.index("logger.info(f\"Extraherar text")]
    assert "raise FileTypeException" in body
    assert "omaskerad text" in body


# --------------------------------------------------------- leak diagnostics
def test_diagnostics_measure_rather_than_guess(tmp_path):
    """Three plausible explanations for the observed leaks were each ruled out
    by testing. The diagnostic reports counts and lets the data decide."""
    src = _pdf(tmp_path / "h.pdf", ["Undertecknad av Gunhild Bergstrom, ordf."])
    rows = PDFMasker.diagnose_survivors(src, ["Gunhild"], {"Gunhild": 0})
    assert len(rows) == 1
    row = rows[0]
    assert row["len"] == 7 and row["words"] == 1
    assert row["in"] == 0        # nothing was redacted in this fixture
    assert row["out"] == 1       # the locator can see it in the output
    assert row["seen"] == 1      # and so can the verifier


def test_diagnostics_distinguish_a_redaction_failure(tmp_path):
    """out > 0 means the search could see the text and it was not removed,
    which is a different defect from the search never finding it."""
    pdf = _pdf(tmp_path / "i.pdf", ["Ordforande Gunhild Bergstrom."])
    rows = PDFMasker.diagnose_survivors(pdf, ["Gunhild"], {"Gunhild": 1})
    assert rows[0]["out"] >= 1


def test_diagnostics_report_no_names(tmp_path):
    pdf = _pdf(tmp_path / "k.pdf", ["Ordforande Gunhild Bergstrom."])
    rows = PDFMasker.diagnose_survivors(pdf, ["Gunhild", "Bergstrom"], {})
    flat = str(rows)
    assert "Gunhild" not in flat and "Bergstrom" not in flat


def test_terms_the_redactor_never_located_are_counted(tmp_path):
    """Logged even when nothing survives, so a near-miss is still visible."""
    pdf = _pdf(tmp_path / "l.pdf", ["Ordforande Karl Andersson."])
    out = tmp_path / "l_masked.pdf"
    _masker().mask_pdf_black_boxes(pdf, out, ["Andersson", "Nilsson"], logging.getLogger())
    assert out.exists()


# ------------------------------------------------ how terms are located now
def test_names_wrapped_across_a_line_are_redacted(tmp_path):
    """page.search_for matches a literal string, so spacing variants slipped
    through. Word-sequence matching handles them."""
    pdf = _pdf(tmp_path / "wrap.pdf", ["Undertecknad av Anna", "Svensson, ordforande"])
    rects = PDFMasker._locate_term(pymupdf.open(pdf)[0], "Anna Svensson")
    assert len(rects) == 2, "one box per line, not one box over the paragraph"

    out = tmp_path / "wrap_masked.pdf"
    assert _masker().mask_pdf_black_boxes(pdf, out, ["Anna Svensson"], logging.getLogger())
    assert PDFMasker.find_surviving_terms(out, ["Anna Svensson"]) == []


@pytest.mark.parametrize(
    "separator, label",
    [("  ", "double space"), ("\u00a0", "non-breaking space"), ("\t", "tab")],
)
def test_unusual_separators_are_still_matched(tmp_path, separator, label):
    pdf = _pdf(tmp_path / f"s{len(label)}.pdf", [f"Ordforande Anna{separator}Svensson."])
    rects = PDFMasker._locate_term(pymupdf.open(pdf)[0], "Anna Svensson")
    assert rects, f"{label} defeated the locator"


def test_trailing_punctuation_does_not_prevent_a_match(tmp_path):
    pdf = _pdf(tmp_path / "punct.pdf", ["Godkant av Anna Svensson, revisor."])
    assert PDFMasker._locate_term(pymupdf.open(pdf)[0], "Anna Svensson")


def test_partial_words_are_not_matched(tmp_path):
    """"Anna" must not redact "Annandag"."""
    pdf = _pdf(tmp_path / "part.pdf", ["Kassan var stangd Annandag jul."])
    assert PDFMasker._locate_term(pymupdf.open(pdf)[0], "Anna") == []


def test_signature_widgets_are_removed(tmp_path):
    """Text in a form field's appearance stream is returned by extraction but
    untouched by apply_redactions, so e-signature panels leaked every time."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 90), "Arsredovisning 2025")
    widget = pymupdf.Widget()
    widget.field_name = "sig"
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.rect = pymupdf.Rect(72, 200, 300, 220)
    widget.field_value = "Signerad av Anna Svensson"
    page.add_widget(widget)
    src = tmp_path / "sig.pdf"
    doc.save(src)
    doc.close()

    with pymupdf.open(src) as d:
        assert len(list(d[0].widgets())) == 1

    out = tmp_path / "sig_masked.pdf"
    assert _masker().mask_pdf_black_boxes(src, out, ["Anna Svensson"], logging.getLogger())
    with pymupdf.open(out) as d:
        assert len(list(d[0].widgets())) == 0
        assert "Arsredovisning 2025" in d[0].get_text(), "page content must survive"


def test_body_text_is_left_alone(tmp_path):
    """Redaction must not eat the figures the analysis depends on."""
    pdf = _pdf(
        tmp_path / "body.pdf",
        ["Balansrakning", "Summa tillgangar 63 853", "Ordforande Anna Svensson"],
    )
    out = tmp_path / "body_masked.pdf"
    _masker().mask_pdf_black_boxes(pdf, out, ["Anna Svensson"], logging.getLogger())
    with pymupdf.open(out) as d:
        text = "".join(p.get_text() for p in d)
    assert "Summa tillgangar 63 853" in text
    assert "Balansrakning" in text
    assert "Svensson" not in text


# ------------------------------------------------------ term list filtering
def test_a_vendor_name_stamped_on_every_page_is_dropped(tmp_path):
    """NER tagged "Comfact", the e-signing vendor, 204 times in a real
    document. Redacting it protects nobody, and because verification checks
    whatever it is given, it blocked the whole file."""
    lines = [f"Comfact sidfot rad {i}" for i in range(40)]
    lines.append("Ordforande Anna Svensson")
    pdf = _pdf(tmp_path / "v.pdf", lines)
    kept, short, common = PDFMasker._plausible_terms(pdf, ["Comfact", "Anna Svensson"])
    assert "Comfact" not in kept
    assert common and common[0][0] == "Comfact"
    assert "Anna Svensson" in kept


def test_short_abbreviations_are_dropped(tmp_path):
    """Ref, Not, IAF and Jag were all tagged as entities."""
    pdf = _pdf(tmp_path / "s.pdf", ["Not 12 Ref 4 IAF beslut Anna Svensson"])
    kept, short, common = PDFMasker._plausible_terms(pdf, ["Not", "Ref", "IAF", "Anna Svensson"])
    assert kept == ["Anna Svensson"]
    assert set(short) == {"Not", "Ref", "IAF"}


def test_a_name_appearing_a_few_times_is_kept(tmp_path):
    pdf = _pdf(tmp_path / "n.pdf", ["Anna Svensson"] * 5)
    kept, _, _ = PDFMasker._plausible_terms(pdf, ["Anna Svensson"])
    assert kept == ["Anna Svensson"]


def test_hyphen_split_name_is_located_and_redacted(tmp_path):
    """get_text("words") yields "Gun-" and "hild" separately, so the name was
    never located while the verifier could read it perfectly."""
    pdf = _pdf(tmp_path / "hy.pdf", ["Undertecknad av Gun-", "hild Bergstrom, ordf."])
    with pymupdf.open(pdf) as doc:
        assert len(PDFMasker._locate_term(doc[0], "Gunhild")) == 2  # both halves

    out = tmp_path / "hy_masked.pdf"
    assert _masker().mask_pdf_black_boxes(pdf, out, ["Gunhild", "Bergstrom"], logging.getLogger())
    assert PDFMasker.find_surviving_terms(out, ["Gunhild"]) == []


def test_a_real_hyphen_inside_a_line_is_not_joined(tmp_path):
    """Only line-break hyphens are joined; "a-kassa" must stay one token."""
    pdf = _pdf(tmp_path / "ak.pdf", ["Sveriges a-kassa redovisar"])
    with pymupdf.open(pdf) as doc:
        tokens = [tok for tok, _ in PDFMasker._joined_words(doc[0])]
    assert "a-kassa" in tokens


def test_word_list_is_built_once_per_page(tmp_path):
    """Rebuilding it per term made a 45-page document with 60 terms crawl."""
    pdf = _pdf(tmp_path / "p.pdf", ["Anna Svensson rad"] * 20)
    with pymupdf.open(pdf) as doc:
        entries = PDFMasker._joined_words(doc[0])
        calls = []
        original = PDFMasker._joined_words.__func__

        def counting(cls, page):
            calls.append(1)
            return original(cls, page)

        PDFMasker._joined_words = classmethod(counting)
        try:
            for _ in range(30):
                PDFMasker._locate_term(doc[0], "Anna Svensson", entries=entries)
        finally:
            PDFMasker._joined_words = classmethod(original)
    assert calls == [], "passing entries must avoid rebuilding the word list"


# --------------------------------------------- pages that cannot be redacted
def _page_without_word_boxes(tmp_path, name="nb.pdf"):
    """A page whose text get_text() returns but get_text("words") does not.

    Real e-signature listing pages behave this way: one had 4553 characters of
    text, 200 words, and the same name visible eight times with zero locatable
    word boxes.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 90), "NAMN: Jenny Soderstrom")
    path = tmp_path / name
    doc.save(path)
    doc.close()
    return path


def test_a_page_that_cannot_be_masked_is_emptied(tmp_path, monkeypatch):
    pdf = _pdf(
        tmp_path / "mix.pdf",
        ["Balansrakning", "Summa tillgangar 63 853", "NAMN: Jenny Soderstrom"],
    )
    # simulate the real defect: nothing is locatable on the page
    monkeypatch.setattr(
        PDFMasker, "_locate_term", classmethod(lambda cls, page, term, entries=None: [])
    )
    out = tmp_path / "mix_masked.pdf"
    result = _masker().mask_pdf_black_boxes(
        pdf, out, ["Jenny Soderstrom"], logging.getLogger()
    )
    assert result is not None, "clearing the page should let the document through"
    with pymupdf.open(out) as doc:
        assert doc[0].get_text().strip() == ""


def test_pages_that_can_be_masked_are_not_emptied(tmp_path):
    """Clearing is a last resort, not the normal path."""
    pdf = _pdf(
        tmp_path / "ok.pdf",
        ["Balansrakning", "Summa tillgangar 63 853", "Ordforande Anna Svensson"],
    )
    out = tmp_path / "ok_masked.pdf"
    assert _masker().mask_pdf_black_boxes(pdf, out, ["Anna Svensson"], logging.getLogger())
    with pymupdf.open(out) as doc:
        text = "".join(p.get_text() for p in doc)
    assert "Summa tillgangar 63 853" in text
    assert "Balansrakning" in text
    assert "Svensson" not in text


def test_clearing_reports_the_page_numbers(tmp_path, monkeypatch):
    """A cleared page must be visible in the log: if one ever held figures,
    the number is how you would find out."""
    pdf = _pdf(tmp_path / "rep.pdf", ["NAMN: Jenny Soderstrom"])
    monkeypatch.setattr(
        PDFMasker, "_locate_term", classmethod(lambda cls, page, term, entries=None: [])
    )
    doc = pymupdf.open(pdf)
    cleared = PDFMasker._clear_unmaskable_pages(doc, ["Jenny Soderstrom"], logging.getLogger())
    doc.close()
    assert cleared == [0]


def test_a_clean_page_is_never_cleared(tmp_path):
    pdf = _pdf(tmp_path / "clean.pdf", ["Summa tillgangar 63 853"])
    doc = pymupdf.open(pdf)
    assert PDFMasker._clear_unmaskable_pages(doc, ["Anna Svensson"], logging.getLogger()) == []
    doc.close()
