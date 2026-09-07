"""Regression tests for the Step 1 bug-fix commit.

Run with:  python -m pytest tests -q
These deliberately avoid the heavy ML stack: PDFMasker takes an injectable
`ner` argument, so no transformers/torch download is needed.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.src.JBGAnnualReportAnalysis import (  # noqa: E402
    RETRYABLE_OPENAI_ERRORS,
    JBGAnnualReportAnalyzer,
)
from app.src.JBGAnnualReportExceptions import (  # noqa: E402
    EmptyOutputException,
    FileTypeException,
    JBGAnnualReportError,
)


# --------------------------------------------------------------------------
# Item 1: the retry loop used to be disabled by a non-exception in the tuple
# --------------------------------------------------------------------------
def test_retryable_errors_are_all_exception_classes():
    """openai.Timeout is httpx.Timeout, so an except-tuple containing it raises
    TypeError for *every* exception that reaches the clause."""
    for err in RETRYABLE_OPENAI_ERRORS:
        assert isinstance(err, type), f"{err!r} is not a class"
        assert issubclass(err, BaseException), f"{err!r} does not derive from BaseException"


def test_except_tuple_actually_catches():
    caught = False
    try:
        try:
            raise RETRYABLE_OPENAI_ERRORS[0].__new__(RETRYABLE_OPENAI_ERRORS[0])
        except RETRYABLE_OPENAI_ERRORS:
            caught = True
    except TypeError as exc:  # pragma: no cover - this is the old bug
        pytest.fail(f"except-tuple is invalid: {exc}")
    assert caught


# --------------------------------------------------------------------------
# Exceptions must derive from Exception, not BaseException
# --------------------------------------------------------------------------
@pytest.mark.parametrize("exc_cls", [FileTypeException, EmptyOutputException])
def test_exceptions_are_catchable_as_exception(exc_cls):
    with pytest.raises(JBGAnnualReportError):
        raise exc_cls("boom")
    assert issubclass(exc_cls, JBGAnnualReportError)
    assert exc_cls().message  # default message is populated


# --------------------------------------------------------------------------
# The _extract_key_number_term() typo and the dead _extract_zip method
# --------------------------------------------------------------------------
def test_no_typo_method_reference():
    assert hasattr(JBGAnnualReportAnalyzer, "_extract_key_number_terms")
    assert not hasattr(JBGAnnualReportAnalyzer, "_extract_key_number_term")


def test_dead_extract_zip_removed():
    assert not hasattr(JBGAnnualReportAnalyzer, "_extract_zip")


# --------------------------------------------------------------------------
# Conflict consolidation must not leak certainty/comment across entries
# --------------------------------------------------------------------------
def _analyzer_stub() -> JBGAnnualReportAnalyzer:
    return JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)


def test_conflicting_values_keep_their_own_metadata():
    analyzer = _analyzer_stub()
    data = {
        "Handels a-kassa": {
            "2023": {
                "Balansomslutning": [
                    {
                        "värde": 12244267,
                        "källa": "Sida 7, Sida 9",
                        "säkerhet": 0.95,
                        "kommentar": "Från balansräkningen",
                    },
                    {
                        "värde": 999,
                        "källa": "Sida 3",
                        "säkerhet": 0.4,
                        "kommentar": "Osäker gissning",
                    },
                ]
            }
        }
    }
    merged, num = analyzer._merge_conflicted_values_json_objects(data)
    entry = merged["Handels a-kassa"]["2023"]["Balansomslutning"]

    # The better-supported value wins, and it keeps *its own* metadata rather
    # than inheriting the last loop iteration's certainty and comment.
    assert entry["värde"] == 12244267
    assert entry["säkerhet"] == 0.95
    assert entry["kommentar"] == "Från balansräkningen"
    assert entry["källa"] == "Sida 7, 9"
    assert num >= 1


def test_consolidation_is_deterministic():
    analyzer = _analyzer_stub()

    def build():
        return {
            "K": {
                "2023": {
                    "M": [
                        {"värde": 1, "källa": "Sida 1", "säkerhet": 0.9, "kommentar": "a"},
                        {"värde": 2, "källa": "Sida 2", "säkerhet": 0.5, "kommentar": "b"},
                    ]
                }
            }
        }

    first, _ = analyzer._merge_conflicted_values_json_objects(build())
    for _ in range(5):
        again, _ = analyzer._merge_conflicted_values_json_objects(build())
        assert again == first


# --------------------------------------------------------------------------
# Item 2: masking must survive PyMuPDF having no check_pdf(), and no logger
# --------------------------------------------------------------------------
def test_masker_has_no_check_pdf_dependency():
    from app.src.masking import JBGPDFMasking

    assert not hasattr(JBGPDFMasking.PDFMasker, "_has_check_pdf")
    assert hasattr(JBGPDFMasking.PDFMasker, "_structure_warnings")


def test_do_masking_without_logger(tmp_path, monkeypatch):
    """The /mask endpoint passes no logger. That used to hit
    `logger.warning(...)` on None and silently return None."""
    pymupdf = pytest.importorskip("pymupdf")
    from app.src.masking.JBGPDFMasking import PDFMasker

    src = tmp_path / "in.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Undertecknad av Anna Exempel, ordforande.")
    doc.save(src)
    doc.close()

    # Extra names come from config now, so point at a temporary one.
    names_file = tmp_path / "names.json"
    names_file.write_text('{"fornamn": ["Anna"], "efternamn": ["Exempel"]}', encoding="utf-8")
    monkeypatch.setenv("JBG_MASKING_EXTRA_NAMES", str(names_file))

    masker = PDFMasker(ner=lambda text: [])  # no NER model needed
    out = masker.do_masking(src, tmp_path / "out.pdf")  # note: no logger

    assert out is not None, "masking returned None on a perfectly valid PDF"
    assert Path(out).is_file()

    with pymupdf.open(out) as masked:
        text = "".join(p.get_text() for p in masked)
    assert "Anna" not in text
    assert "Exempel" not in text
