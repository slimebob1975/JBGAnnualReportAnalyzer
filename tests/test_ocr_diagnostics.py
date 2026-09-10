"""Tests for the OCR retry ladder and the diagnosis that follows a failure.

The case these are written against: a fifteen-page report that came back
from OCR with 897 characters, every one of them from a digital signature
appended after scanning. The log said "för lite text" and nothing else, and
telling "unreadable scan" from "OCR was never asked to look" took two
throwaway scripts and a human.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pymupdf = pytest.importorskip("pymupdf")

from app.src import JBGPDFDiagnostics as diagnostics  # noqa: E402
from app.src.JBGAnnualReportAnalysis import JBGAnnualReportAnalyzer  # noqa: E402


def _analyzer() -> JBGAnnualReportAnalyzer:
    analyzer = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)
    analyzer.last_ocr_diagnosis = None
    return analyzer


def _pdf(tmp_path, pages, name="doc.pdf"):
    """Build a PDF from a list of page bodies. An empty body means a blank page."""
    doc = pymupdf.open()
    for body in pages:
        page = doc.new_page()
        if body:
            page.insert_text((72, 90), body, fontsize=11)
    out = tmp_path / name
    doc.save(out)
    doc.close()
    return out


def _long_body(prefix: str, lines: int = 30) -> str:
    return "\n".join(f"{prefix} rad {n}: belopp {1000 + n} tkr" for n in range(lines))


# ------------------------------------------------------------------ profiling
def test_profile_counts_pages_without_rendering(tmp_path):
    pdf = _pdf(tmp_path, ["", "", _long_body("Sidan har text")])
    result = diagnostics.profile(pdf)

    assert result.page_count == 3
    assert result.pages_with_text == 1
    assert "3 sidor" in result.one_line()
    # Nothing was rasterised, so no page carries an ink measurement.
    assert all(page.ink_fraction is None for page in result.pages)


def test_profile_survives_a_file_it_cannot_open(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    result = diagnostics.profile(broken)

    assert result.error
    assert "Kunde inte profilera" in result.one_line()


# ------------------------------------------------------------------ diagnosis
def test_blank_pages_are_reported_as_a_blank_render(tmp_path):
    """The distinction that matters: nothing on the page, versus something
    the tooling cannot reach."""
    pdf = _pdf(tmp_path, ["", "", ""])
    result = diagnostics.diagnose(pdf)

    assert result.verdict == diagnostics.VERDICT_BLANK_RENDER
    assert "renderar tomma" in result.explanation
    assert any("TOM" in line for line in result.report())


def test_a_page_with_content_is_not_called_blank(tmp_path):
    pdf = _pdf(tmp_path, [_long_body("Resultatrakning")] * 3)
    result = diagnostics.diagnose(pdf)

    assert result.verdict != diagnostics.VERDICT_BLANK_RENDER
    rendered = [page for page in result.pages if page.rendered]
    assert rendered and all(
        page.ink_fraction > diagnostics.BLANK_INK_FRACTION for page in rendered
    )


def test_renders_are_not_written_to_disk_unless_asked(tmp_path):
    """They are unmasked pages of a scanned report."""
    pdf = _pdf(tmp_path, ["", ""])
    result = diagnostics.diagnose(pdf)

    assert result.rendered_pages == []
    assert not list(tmp_path.glob("*.png"))


def test_renders_can_be_saved_for_a_human_to_look_at(tmp_path):
    pdf = _pdf(tmp_path, ["", ""])
    out = tmp_path / "renders"
    result = diagnostics.diagnose(pdf, save_to=out)

    assert result.rendered_pages
    assert all(path.is_file() for path in result.rendered_pages)


# ------------------------------------------------------------ the OCR ladder
def test_the_second_setting_is_tried_when_the_first_yields_nothing(tmp_path):
    """The 897-character case: skip_text finds nothing to do, force_ocr does."""
    scan = _pdf(tmp_path, [""] * 5, name="scan.pdf")
    good = _pdf(tmp_path, [_long_body(f"Sida {i}") for i in range(10)], name="good.pdf")

    attempted = []

    def fake_ocr(pdf_path, ocr_path, options):
        attempted.append(sorted(options))
        # The first setting produces a file with only a signature on it.
        body = [""] * 4 + ["Underskrift"] if len(attempted) == 1 else None
        if body is not None:
            source = _pdf(tmp_path, body, name=f"attempt{len(attempted)}.pdf")
        else:
            source = good
        ocr_path.write_bytes(source.read_bytes())
        return True

    analyzer = _analyzer()
    analyzer._ocr_once = fake_ocr
    result = analyzer._run_ocr(scan)

    assert len(attempted) == 2, "the ladder stopped after the first attempt"
    assert result is not None
    assert analyzer._text_length(result) >= JBGAnnualReportAnalyzer.MIN_USABLE_TEXT_CHARS


def test_a_sufficient_first_attempt_does_not_run_the_second(tmp_path):
    """force_ocr costs roughly twice as much; it must stay a fallback."""
    scan = _pdf(tmp_path, [""] * 5, name="scan.pdf")
    good = _pdf(tmp_path, [_long_body(f"Sida {i}") for i in range(10)], name="good.pdf")

    attempted = []

    def fake_ocr(pdf_path, ocr_path, options):
        attempted.append(options)
        ocr_path.write_bytes(good.read_bytes())
        return True

    analyzer = _analyzer()
    analyzer._ocr_once = fake_ocr
    assert analyzer._run_ocr(scan) is not None
    assert len(attempted) == 1


def test_every_attempt_failing_yields_a_diagnosis_not_just_none(tmp_path):
    scan = _pdf(tmp_path, [""] * 5, name="scan.pdf")

    def fake_ocr(pdf_path, ocr_path, options):
        ocr_path.write_bytes(scan.read_bytes())
        return True

    analyzer = _analyzer()
    analyzer._ocr_once = fake_ocr
    assert analyzer._run_ocr(scan) is None
    assert analyzer.last_ocr_diagnosis is not None
    assert analyzer.last_ocr_diagnosis.verdict == diagnostics.VERDICT_BLANK_RENDER


def test_the_skip_message_names_a_cause(tmp_path, monkeypatch):
    """"gav bara 897 tecken" is true and tells nobody what to do about it."""
    from app.src import JBGAnnualReportAnalysis as mod
    from app.src.JBGAnnualReportExceptions import FileTypeException

    scan = _pdf(tmp_path, [""] * 5, name="scan.pdf")
    monkeypatch.setattr(mod, "ocr_availability", lambda: (True, "ok"))

    def fake_ocr(pdf_path, ocr_path, options):
        ocr_path.write_bytes(scan.read_bytes())
        return True

    analyzer = _analyzer()
    analyzer._ocr_once = fake_ocr
    with pytest.raises(FileTypeException) as excinfo:
        analyzer._ensure_readable_pdf(scan)

    assert "samtliga inställningar" in excinfo.value.message
    assert "renderar tomma" in excinfo.value.message


def test_a_failed_invocation_moves_on_to_the_next_setting(tmp_path):
    """ocrmypdf raising on one setting must not end the ladder."""
    scan = _pdf(tmp_path, [""] * 5, name="scan.pdf")
    good = _pdf(tmp_path, [_long_body(f"Sida {i}") for i in range(10)], name="good.pdf")

    calls = []

    def fake_ocr(pdf_path, ocr_path, options):
        calls.append(options)
        if len(calls) == 1:
            return False
        ocr_path.write_bytes(good.read_bytes())
        return True

    analyzer = _analyzer()
    analyzer._ocr_once = fake_ocr
    assert analyzer._run_ocr(scan) is not None
    assert len(calls) == 2
