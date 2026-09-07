import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

# NOTE: openai.Timeout is httpx.Timeout, a *configuration* object, not an
# exception class. Listing it in an except-tuple makes CPython raise
# "TypeError: catching classes that do not inherit from BaseException is not
# allowed" for every exception that reaches the clause, which disabled the
# retry loop entirely. Retry only on genuinely transient errors.
RETRYABLE_OPENAI_ERRORS = (
    RateLimitError,
    APITimeoutError,
    APIConnectionError,
    InternalServerError,
)
import logging

from app.src import JBGMetricSchema as schema
from app.src import JBGValidation as validation
from app.src.JBGAnnualReportExceptions import FileTypeException
from app.src.JBGFundNames import normalise_result_fund_names
from app.src.masking.JBGPDFMasking import PDFMasker

try:  # PyMuPDF >= 1.24.3 ships the package under its real name
    import pymupdf
except ImportError:  # pragma: no cover - older PyMuPDF only exposes "fitz"
    import fitz as pymupdf
import re
import time
from collections.abc import Mapping

import tiktoken

logger = logging.getLogger(__name__)
# Inherit the level configured by the application (JBG_LOG_LEVEL) rather than
# forcing DEBUG, which wrote full report text and full GPT responses to disk.

class _ApproximateEncoder:
    """Stand-in for a tiktoken encoder when the real one cannot be fetched.

    Encodes to fixed-width character groups, so len(encode(text)) approximates
    the token count and decode(encode(text)) round-trips exactly. That means
    every caller, including the overlap-based splitter that slices and decodes
    token lists, keeps working without special cases.
    """

    CHARS_PER_TOKEN = 4

    def encode(self, text: str) -> list[str]:
        step = self.CHARS_PER_TOKEN
        return [text[i:i + step] for i in range(0, len(text), step)]

    def decode(self, tokens) -> str:
        return "".join(tokens)


class JBGAnnualReportAnalyzer:
    METRIC_KEY_NUMBER_KEY = "Nyckeltal"
    METRIC_KEY_NUMBER_ALTERNATE_KEY = "Alternativa benämningar"
    FIX_BROKEN_LINES_WITH_KEY_NUMBERS = True
    FIELD_VALUE = "värde"
    FIELD_SOURCE = "källa"
    FIELD_CERTAINTY = "säkerhet"
    FIELD_COMMENT = "kommentar"
    SOURCE_PREFIX = "Sida"
    STANDARD_ENCODING = "utf-8"
    PAGE_OFFSET = 0
    OFFSET_LIMIT = 99
    MIN_CHECK_OFFSETS = 5
    MIN_OFFSET_AGREEMENT_RATE = 0.8
    MIN_YEAR_AGREEMENT_RATE = 0.8
    MIN_CHECK_YEARS = 5
    FALLBACK_YEAR = -1
    # Modern context windows make small chunks counter-productive: every extra
    # chunk is another call that sees only part of the report and then reports
    # the rest as missing. On the sample corpus this puts every document in a
    # single chunk.
    MAX_TOKENS = 30000
    MAX_TOKEN_OVERLAP = 1000
    USE_PAGE_AWARE_CHUNKING = True
    USE_STRUCTURED_OUTPUT = True
    VALIDATION_KEY = "_rimlighetskontroller"
    # One extra, narrowly-scoped call per file when the first pass missed
    # something. Skipped entirely when nothing is missing.
    USE_SECOND_PASS_FOR_MISSING = True
    SECOND_PASS_MAX_MISSING_RATIO = 0.5
    SECOND_PASS_TAG = "[Riktad omsökning]"
    MAX_CONCURRENT_CHUNKS = 4
    OPENAI_SDK_MAX_RETRIES = 3
    OPENAI_TIMEOUT_SECONDS = 300.0
    MAX_TOKEN_OVERLAP_REDUCTION = 200
    USE_TOKEN_OVERLAP = True
    DEFAULT_MODEL = "gpt-4o"
    DEFAULT_OPENAI_TEMPERATURE = 0.3
    GPT_5_TEMPERATURE = 1.0
    MODEL_GPT_5_MARKER = "gpt-5"
    DEFAULT_OPENAI_TOP_P = 1
    DEFAULT_SHORT_SLEEP_TIME = 1
    DEFAULT_LONG_SLEEP_TIME = 5
    TEXT_GAIN_FOR_OCR_CONVERSION = 1.5
    # Local (non-LLM) detection of page offset and fiscal year
    MAX_PAGES_FOR_OFFSET_SCAN = 25
    MAX_PAGES_FOR_YEAR_SCAN = 8
    MAX_PAGES_FOR_LLM_VOTE = 15
    MARGIN_FRACTION = 0.10
    MAX_FOOTER_TEXT_CHARS = 24
    TRAILING_LINES_TO_CHECK = 3
    STRONG_YEAR_WEIGHT = 5
    MIN_YEAR_AGREEMENT_RATE_LOCAL = 0.4
    # Local detection handles most documents, but some carry no extractable
    # page numbers at all. The fallback is capped and breaks early.
    USE_LLM_FALLBACK_FOR_PAGE_OFFSET = True
    USE_LLM_FALLBACK_FOR_YEAR = True
    # OCR is only worth its cost when the text layer is actually missing
    MIN_TEXT_PAGE_RATIO = 0.8
    OCR_LANGUAGE = "swe"

    def __init__(
        self,
        upload_dir: str | Path | list[str | Path],
        instruction_path: str | Path,
        metrics_path: str | Path,
        use_masking: bool = False,
        api_key: str = None,
        fund_list_path: str | Path = None,
    ):
        # Accept list of paths or a folder
        if isinstance(upload_dir, (list, tuple)):
            self.upload_files = [Path(f) for f in upload_dir]
        else:
            upload_path = Path(upload_dir)
            if not upload_path.exists():
                raise FileNotFoundError(f"Path does not exist: {upload_path}")

            # Search recursively for PDFs
            self.upload_files = list(upload_path.rglob("*.pdf"))

        self.instruction_path = Path(instruction_path)
        self.metrics_path = Path(metrics_path)
        self.use_masking = use_masking
        self.fund_list_path = (
            Path(fund_list_path)
            if fund_list_path
            else Path(__file__).resolve().parent / "json" / "kassor.json"
        )
        self.validation_findings = []
        self._masker = None
        self._schema_cache = None
        # max_retries lets the SDK handle rate limits with proper jitter, which
        # is why the fixed sleeps between chunks could be removed.
        self.openai_client = OpenAI(
            api_key=api_key or None,
            max_retries=self.OPENAI_SDK_MAX_RETRIES,
            timeout=self.OPENAI_TIMEOUT_SECONDS,
        )

    # ------------------------------------------------------------------
    # Page numbering offset
    #
    # Strategy, cheapest first:
    #   1. /PageLabels from the PDF itself (free, exact, no I/O)
    #   2. printed page numbers found in the page margins (free)
    #   3. the original LLM loop, kept only as a last resort
    # ------------------------------------------------------------------
    def _get_masker(self) -> PDFMasker:
        """Build the NER pipeline once per analyzer, not once per PDF.

        Constructing PDFMasker loads a BERT model. The log showed this being
        redone for every file in the batch, at roughly a second each after the
        first (and eight seconds on a cold Hugging Face cache).
        """
        if getattr(self, "_masker", None) is None:
            self._masker = PDFMasker()
        return self._masker

    def _find_page_number_offset(self, pdf_path: Path, model: str = "") -> int:
        try:
            with pymupdf.open(pdf_path) as doc:
                offset = self._offset_from_page_labels(doc)
                if offset is not None:
                    logger.info(f"Sidnummeroffset {offset} hämtat ur PDF:ens /PageLabels.")
                    return offset

                offset = self._offset_from_printed_numbers(doc)
                if offset is not None:
                    logger.info(f"Sidnummeroffset {offset} härlett ur tryckta sidnummer.")
                    return offset

                offset = self._offset_from_trailing_lines(doc)
                if offset is not None:
                    logger.info(f"Sidnummeroffset {offset} härlett ur sidornas sista rader.")
                    return offset

            if not self.USE_LLM_FALLBACK_FOR_PAGE_OFFSET:
                logger.info("Inget sidnummeroffset kunde härledas. Använder standardvärde.")
                return self.PAGE_OFFSET

            logger.info("Inget sidnummeroffset kunde härledas lokalt. Frågar modellen.")
            return self._offset_from_llm(pdf_path, model=model)
        except Exception as e:
            logger.warning(
                f"Could not extract pdf page number offset from {pdf_path.name}: {e}. Using standard value."
            )
            return self.PAGE_OFFSET

    @staticmethod
    def _offset_from_page_labels(doc) -> int | None:
        """Derive the offset from the document's own /PageLabels tree.

        Returns None when the PDF carries no page labels, which is common.
        """
        labels = doc.get_page_labels()
        if not labels:
            return None

        # A single "arabic, start at 1, from page 0" rule is what many PDF
        # producers emit unconditionally. It describes the PDF index, not the
        # printed numbering, so treat it as no information at all and let the
        # footer scan have a go. Trusting it put the offset at 0 on a document
        # whose pages are visibly numbered one behind.
        if len(labels) == 1:
            rule = labels[0]
            if (
                rule.get("startpage", 0) == 0
                and rule.get("firstpagenum", 1) in (0, 1)
                and rule.get("style", "D") in ("D", "")
                and not rule.get("prefix")
            ):
                logger.debug("PDF:ens /PageLabels är en trivialregel. Ignorerar den.")
                return None

        offsets = {}
        for index in range(doc.page_count):
            label = (doc[index].get_label() or "").strip()
            if not label.isdigit():
                continue  # roman numerals, prefixed labels, unnumbered front matter
            offsets[(index + 1) - int(label)] = offsets.get((index + 1) - int(label), 0) + 1

        if not offsets:
            return None
        best = max(offsets, key=offsets.get)
        if abs(best) > JBGAnnualReportAnalyzer.OFFSET_LIMIT:
            return None
        return best

    @staticmethod
    def _offset_from_printed_numbers(doc) -> int | None:
        """Look for a bare page number printed in the bottom margin.

        Footer only, deliberately. Scanning the top margin as well produced
        false positives on numbered note headings ("5 Finansiella intäkter"),
        which look exactly like a printed page number to a regex.
        """
        offsets = {}
        checked = 0

        for index in range(min(doc.page_count, JBGAnnualReportAnalyzer.MAX_PAGES_FOR_OFFSET_SCAN)):
            page = doc[index]
            rect = page.rect
            margin = rect.height * JBGAnnualReportAnalyzer.MARGIN_FRACTION
            footer = pymupdf.Rect(rect.x0, rect.y1 - margin, rect.x1, rect.y1)

            text = " ".join(page.get_text("text", clip=footer).split())
            # A footer holding a page number is short. Anything longer is a
            # table spilling into the margin, or a footnote.
            if not text or len(text) > JBGAnnualReportAnalyzer.MAX_FOOTER_TEXT_CHARS:
                continue

            candidates = re.findall(r"(?<![\d\-/.])(\d{1,3})(?![\d\-/.])", text)
            if len(candidates) != 1:
                continue
            printed = int(candidates[0])
            if printed == 0:
                continue

            offset = (index + 1) - printed
            if abs(offset) > JBGAnnualReportAnalyzer.OFFSET_LIMIT:
                continue
            offsets[offset] = offsets.get(offset, 0) + 1
            checked += 1

        if checked < JBGAnnualReportAnalyzer.MIN_CHECK_OFFSETS:
            return None
        best = max(offsets, key=offsets.get)
        agreement = offsets[best] / checked
        if agreement < JBGAnnualReportAnalyzer.MIN_OFFSET_AGREEMENT_RATE:
            logger.debug(f"Tryckta sidnummer gav ingen enighet: {offsets}")
            return None
        return best

    @staticmethod
    def _offset_from_trailing_lines(doc) -> int | None:
        """Position-independent variant of the footer scan.

        Some PDFs report unusable glyph positions ("Actualtext with no
        position"), so clipping a rectangle finds nothing. Reading the raw text
        stream and looking at the last few lines of each page works regardless.
        """
        offsets = {}
        checked = 0

        for index in range(min(doc.page_count, JBGAnnualReportAnalyzer.MAX_PAGES_FOR_OFFSET_SCAN)):
            lines = [ln.strip() for ln in doc[index].get_text().splitlines() if ln.strip()]
            if not lines:
                continue
            tail = lines[-JBGAnnualReportAnalyzer.TRAILING_LINES_TO_CHECK:]
            bare = [ln for ln in tail if re.fullmatch(r"\d{1,3}", ln)]
            if len(bare) != 1:
                continue
            printed = int(bare[0])
            if printed == 0:
                continue
            offset = (index + 1) - printed
            if abs(offset) > JBGAnnualReportAnalyzer.OFFSET_LIMIT:
                continue
            offsets[offset] = offsets.get(offset, 0) + 1
            checked += 1

        if checked < JBGAnnualReportAnalyzer.MIN_CHECK_OFFSETS:
            return None
        best = max(offsets, key=offsets.get)
        if offsets[best] / checked < JBGAnnualReportAnalyzer.MIN_OFFSET_AGREEMENT_RATE:
            logger.debug(f"Avslutande rader gav ingen enighet: {offsets}")
            return None
        return best

    def _offset_from_llm(self, pdf_path: Path, model: str = "") -> int:
        """Original per-page LLM vote. Now a fallback, and capped."""
        with pymupdf.open(pdf_path) as doc:
            page_offset = self.PAGE_OFFSET
            offsets = {}
            prompt = self._prompt_instructions_pdf_page_offset()
            max_pages = min(doc.page_count, self.MAX_PAGES_FOR_LLM_VOTE)

            for i in range(max_pages):
                # No sleep between votes: rate limits are handled by the SDK's
                # retry logic, and five 1-second sleeps per document was
                # measurable in the logs for no benefit.
                response = self._make_openai_api_call(
                    prompt, f"[Sida {i + 1}]:\n" + doc[i].get_text(), model=model
                )
                try:
                    new_offset = int(response.strip())
                except (TypeError, ValueError):
                    logger.warning(
                        f"Could not extract page_offset from response: {response} on page {i + 1}"
                    )
                    continue
                if abs(new_offset) > self.OFFSET_LIMIT:
                    continue
                offsets[new_offset] = offsets.get(new_offset, 0) + 1
                page_offset = max(offsets, key=offsets.get)
                rate = max(offsets.values()) / sum(offsets.values())
                if rate >= self.MIN_OFFSET_AGREEMENT_RATE and (i + 1) >= self.MIN_CHECK_OFFSETS:
                    logger.info(
                        f"Breaking offset calculation loop at {i + 1}th iteration with {round(rate, 2)} rate"
                    )
                    break

            logger.info(f"Final page numbering offset is {page_offset}")
            return page_offset

    # ------------------------------------------------------------------
    # Fiscal year
    #
    # Same idea: read it out of the document before paying for an opinion.
    # ------------------------------------------------------------------
    def _find_primary_year_from_pdf(self, pdf_path: Path, model: str = "") -> int:
        try:
            with pymupdf.open(pdf_path) as doc:
                year = self._year_from_text_patterns(doc)
                if year:
                    logger.info(f"Räkenskapsår {year} härlett ur dokumentets text.")
                    return year

            if not self.USE_LLM_FALLBACK_FOR_YEAR:
                logger.warning("Kunde inte härleda räkenskapsår lokalt. Sätter år okänt.")
                return self.FALLBACK_YEAR

            logger.info("Kunde inte härleda räkenskapsår lokalt. Frågar modellen.")
            return self._year_from_llm(pdf_path, model=model)
        except Exception as e:
            logger.warning(f"Kunde inte tolka år från {pdf_path.name}: {e}. Återgår till standardår.")
            return self.FALLBACK_YEAR

    @staticmethod
    def _year_from_text_patterns(doc) -> int | None:
        """Find the fiscal year using the phrasing Swedish annual reports use.

        Two passes. An explicit statement of the reporting period ("för
        räkenskapsåret 2024-01-01 - 2024-12-31") decides the question on its
        own; bare four-digit years are only counted when no such statement
        exists. Without that split, a flerårsöversikt listing 2019 five times
        per page outvoted the cover page.

        Matches on earlier pages weigh more, because the reporting year is
        stated on the cover while comparison years appear deeper in the report.
        """
        # Accent-tolerant: OCR and some PDF encodings turn å/ä/ö into a/o or
        # drop them entirely, and the whole point of this scan is to work on
        # documents whose text layer is imperfect.
        strong = re.compile(
            r"r[äa]kenskaps[åa]ret[^\d]{0,20}(20\d{2})"
            r"|verksamhets[åa]ret[^\d]{0,20}(20\d{2})"
            r"|[åa]rsredovisning[^\d]{0,30}(20\d{2})"
            r"|(20\d{2})-12-31"
            r"|per\s+den\s+31\s+december\s+(20\d{2})",
            re.IGNORECASE,
        )
        weak = re.compile(r"\b(20\d{2})\b")

        strong_scores, weak_scores = {}, {}
        pages_to_read = min(doc.page_count, JBGAnnualReportAnalyzer.MAX_PAGES_FOR_YEAR_SCAN)

        for index in range(pages_to_read):
            text = doc[index].get_text()
            page_weight = pages_to_read - index  # front pages count for more
            for match in strong.finditer(text):
                year = int(next(g for g in match.groups() if g))
                strong_scores[year] = strong_scores.get(year, 0) + page_weight
            for match in weak.finditer(text):
                year = int(match.group(1))
                weak_scores[year] = weak_scores.get(year, 0) + 1

        logger.debug(f"Årpoäng – explicita: {strong_scores}, svaga: {weak_scores}")

        for scores, label in ((strong_scores, "explicit"), (weak_scores, "svag")):
            if not scores:
                continue
            best = max(scores, key=scores.get)
            dominance = scores[best] / sum(scores.values())
            if dominance >= JBGAnnualReportAnalyzer.MIN_YEAR_AGREEMENT_RATE_LOCAL:
                logger.debug(f"Valde {best} via {label} matchning (dominans {round(dominance, 2)})")
                return best
            logger.debug(f"{label} matchning gav ingen dominans: {scores}")

        return None

    def _year_from_llm(self, pdf_path: Path, model: str = "") -> int:
        """Original per-page LLM vote. Now a fallback, and capped."""
        with pymupdf.open(pdf_path) as doc:
            year_counts = {}
            most_likely_year = -1
            prompt = self._prompt_instructions_pdf_actual_year()
            max_pages = min(doc.page_count, self.MAX_PAGES_FOR_LLM_VOTE)

            for i in range(max_pages):
                response = self._make_openai_api_call(
                    prompt, f"[Sida {i + 1}]:\n{doc[i].get_text()}", model=model
                )
                try:
                    extracted_year = int(response.strip())
                except (TypeError, ValueError):
                    logger.warning(f"Kunde inte tolka år från GPT-svar: {response} på sida {i + 1}")
                    continue
                if extracted_year < 2000:
                    continue  # -1 / -2 sentinels

                year_counts[extracted_year] = year_counts.get(extracted_year, 0) + 1
                most_likely_year = max(year_counts, key=year_counts.get)
                dominance = year_counts[most_likely_year] / sum(year_counts.values())
                if dominance >= self.MIN_YEAR_AGREEMENT_RATE and (i + 1) >= self.MIN_CHECK_YEARS:
                    logger.info(
                        f"Bryter årtolkningsloop vid sida {i + 1} med {round(dominance, 2)} dominans."
                    )
                    break

            return most_likely_year if most_likely_year > 0 else self.FALLBACK_YEAR

    def _extract_text_from_pdf_from_pdf(self, pdf_path: Path, model: str = "") -> str:
        try:
            # The offset is derived once and reused for the OCR copy: ocrmypdf
            # preserves page order, and rebuilding it cost a second full scan.
            offset = max(self._find_page_number_offset(pdf_path, model=model), 0)

            with pymupdf.open(pdf_path) as original_doc:
                original_text = self._extract_text_from_pdf(original_doc, offset).strip()
                pages_with_text = sum(1 for page in original_doc if page.get_text().strip())
                page_count = original_doc.page_count

            original_len = len(original_text)
            text_page_ratio = (pages_with_text / page_count) if page_count else 0.0
            logger.info(
                f"Original text length: {original_len} "
                f"({pages_with_text}/{page_count} sidor med textlager)"
            )

            if text_page_ratio >= self.MIN_TEXT_PAGE_RATIO:
                logger.info(
                    f"Textlager finns på {round(text_page_ratio * 100)}% av sidorna. Hoppar över OCR."
                )
                return original_text

            ocr_text = self._ocr_text(pdf_path, offset)
            if ocr_text is None:
                return original_text

            logger.info(f"OCR text length: {len(ocr_text)}")
            if len(ocr_text) > original_len * self.TEXT_GAIN_FOR_OCR_CONVERSION:
                logger.info(f"Using OCR-enhanced version of {pdf_path.name}")
                return ocr_text

            logger.info("OCR did not significantly improve content. Using original.")
            return original_text
        except Exception as e:
            logger.warning(f"Text extraction failed for {pdf_path.name}: {e}")
            return ""

    def _ocr_text(self, pdf_path: Path, offset: int) -> str | None:
        """OCR the pages that lack a text layer and return the combined text.

        Returns None when OCR is unavailable or fails, so the caller can fall
        back to whatever the original text layer offered.
        """
        ocr_path = pdf_path.with_name(f"{pdf_path.stem}_ocr.pdf")
        try:
            # Imported lazily: ocrmypdf needs tesseract and ghostscript to be
            # present, which we do not want to require just to import this module.
            import ocrmypdf

            # NOTE: the first parameter is positional. ocrmypdf 17 renamed it from
            # `input_file` to `input_file_or_options`, so the previous keyword call
            # raised TypeError on every single document and was silently swallowed.
            ocrmypdf.ocr(
                str(pdf_path),
                str(ocr_path),
                language=self.OCR_LANGUAGE,
                deskew=True,
                # Only rasterise and OCR pages that have no text of their own.
                skip_text=True,
                progress_bar=False,
            )
        except ImportError:
            logger.warning("ocrmypdf är inte installerat. Hoppar över OCR.")
            return None
        except Exception as ocr_err:
            logger.warning(f"OCR failed for {pdf_path.name}: {ocr_err}")
            return None

        try:
            with pymupdf.open(ocr_path) as ocr_doc:
                return self._extract_text_from_pdf(ocr_doc, offset).strip()
        except Exception as e:
            logger.warning(f"Kunde inte läsa OCR-resultatet för {pdf_path.name}: {e}")
            return None


    def _extract_text_from_pdf(self, doc, offset: int) -> str:

        def page_label(page_number, page_number_offset):
            page_label = page_number - page_number_offset
            if page_label > 0:
                return page_label
            else:
                n = page_label + page_number_offset
                return to_roman_numeral(n)

        def to_roman_numeral(n: int) -> str:
            val_map = [
                (1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'),
                (100, 'C'), (90, 'XC'), (50, 'L'), (40, 'XL'),
                (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')
            ]
            result = ""
            for (value, numeral) in val_map:
                while n >= value:
                    result += numeral
                    n -= value
            return result

        return "\n\n".join([
            f"[Sida {page_label(i+1, offset)}]\n{page.get_text()}"
            for i, page in enumerate(doc)
        ])

    def _merge_broken_key_number_lines(self, text: str, key_number_terms: list[str]=None) -> str:

        if not key_number_terms:
            key_number_terms = self._extract_key_number_terms()

        lines = text.split("\n")
        terms = {term.lower() for term in key_number_terms}
        merged = []
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            lower = line.lower()

            if any(lower.startswith(term) for term in terms):
                parts = [line]
                j = i + 1
                while j < len(lines):
                    next_line = lines[j].strip()
                    if not next_line:
                        j += 1
                        continue
                    if re.match(r"^[\d\s]+$", next_line):
                        parts.append(next_line)
                        j += 1
                    else:
                        break
                merged.append(" | ".join(parts))
                i = j
            else:
                merged.append(line)
                i += 1

        return "\n".join(merged)

    def _extract_key_number_terms(self) -> list[str]:
        metrics = self._load_metrics(dump=False)
        key_number_terms = [metric.get(self.METRIC_KEY_NUMBER_KEY) for metric in metrics]
        for metric in metrics:
            key_number_terms = key_number_terms + [alt_metric for alt_metric in metric.get(self.METRIC_KEY_NUMBER_ALTERNATE_KEY)]

        return key_number_terms

    @staticmethod
    def _get_encoder_for_model(model_name: str):
        """
        Returnerar en tiktoken-encoder för angiven modell.
        1) Försöker modell-specifik encoder via encoding_for_model
        2) Faller tillbaka enligt känd praxis:
        - GPT-5*  -> "o200k_base"
        - GPT-4o* -> "o200k_base"
        - GPT-4*  -> "cl100k_base"
        - GPT-3.5 -> "cl100k_base"
        - Annat   -> "o200k_base" (säkert val för nyare modeller)
        3) Om tiktoken inte kan hämta någon encoder alls: en approximativ
           encoder, så att analysen inte faller på att containern saknar
           utgående nätverk.
        """

        _GPT5_RE = re.compile(r"^gpt-5", re.IGNORECASE)      # gpt-5, gpt-5-mini, gpt-5-pro, etc.
        _GPT4O_RE = re.compile(r"^(gpt-4o|o\d)", re.IGNORECASE)  # gpt-4o, gpt-4o-mini, ev. o-…-modeller
        _GPT4_STD_RE = re.compile(r"^gpt-4(?!o)", re.IGNORECASE) # gpt-4, gpt-4-0613 osv.
        _GPT35_RE = re.compile(r"^gpt-3\.5", re.IGNORECASE)

        # 1) Försök med tiktokens inbyggda mappning
        try:
            return tiktoken.encoding_for_model(model_name)
        except Exception:
            pass

        # 2) Heuristiska fallbacks
        if _GPT5_RE.match(model_name) or _GPT4O_RE.match(model_name):
            wanted = "o200k_base"
        elif _GPT4_STD_RE.match(model_name) or _GPT35_RE.match(model_name):
            wanted = "cl100k_base"
        else:
            wanted = "o200k_base"  # anta nyare tokenizer

        try:
            return tiktoken.get_encoding(wanted)
        except Exception as ex:
            # tiktoken downloads its encoding files on first use. Behind a
            # restricted network, or in an air-gapped container, that fails and
            # used to take the whole analysis down with it.
            logger.warning(
                f"Kunde inte hämta tokenisering '{wanted}' ({ex}). "
                "Använder approximativ tokenräkning. Förbättra genom att "
                "cachea tiktoken vid byggtid och sätta TIKTOKEN_CACHE_DIR."
            )
            return _ApproximateEncoder()

    def _count_tokens(self, text: str, model: str = "gpt-4o") -> int:
        enc = JBGAnnualReportAnalyzer._get_encoder_for_model(model)
        return len(enc.encode(text))


    def _chunk_text_with_overlap(
        self,
        text: str,
        max_tokens: int,
        max_overlap_tokens: int | float,
        model: str = "gpt-4o"
    ) -> list[str]:

        # Ladda rätt tokenisering beroende på modell
        enc = JBGAnnualReportAnalyzer._get_encoder_for_model(model)

        # Tokenisera hela texten
        tokens = enc.encode(text)
        total_tokens = len(tokens)

        # Om float: konvertera till int (andel av max_tokens)
        if isinstance(max_overlap_tokens, float):
            if not (0 < max_overlap_tokens < 1):
                raise ValueError("Float-värde för max_overlap_tokens måste vara > 0 och < 1")
            overlap = int(max_tokens * max_overlap_tokens)
        elif isinstance(max_overlap_tokens, int):
            overlap = max_overlap_tokens
        else:
            raise TypeError("max_overlap_tokens måste vara int eller float")

        if overlap >= max_tokens:
            raise ValueError("Överlapp får inte vara större än eller lika med max_tokens")

        chunks = []
        start = 0

        while start < total_tokens:
            end = min(start + max_tokens, total_tokens)
            chunk_tokens = tokens[start:end]
            chunk_text = enc.decode(chunk_tokens)
            chunks.append(chunk_text.strip())

            if end == total_tokens:
                break
            else:
                start = end - overlap
                if start < 0:
                    start = 0

        return self._adjust_chunks_borders_for_safe_breaks(chunks)

    def _adjust_chunks_borders_for_safe_breaks(self, chunks: list[str]) -> list[str]:
        # Plain substrings only: these go to str.find/str.rfind. The list used
        # to end with "\n\n[A-Z]" and r"\. [A-ZÅÄÖ]", which look like regexes
        # and were matched literally, so they never fired.
        break_patterns = ["\n\n", "\n[Sida ", "\nSida ", "\nNot ", ":\n"]

        def find_last_good_break_index(text: str) -> int:
            for pattern in break_patterns:
                idx = text.rfind(pattern)
                if idx != -1 and len(text) - idx <= self.MAX_TOKEN_OVERLAP_REDUCTION:
                    return idx + len(pattern)
            return len(text)

        def find_first_good_break_index(text: str) -> int:
            for pattern in break_patterns:
                idx = text.find(pattern)
                if idx != -1 and idx <= self.MAX_TOKEN_OVERLAP_REDUCTION:
                    return idx + len(pattern)
            return 0  # börja från start om inget bra hittas

        for i in range(1, len(chunks)):
            # Trimma slutet på föregående chunk
            end = find_last_good_break_index(chunks[i - 1])
            if end < len(chunks[i - 1]):
                chunks[i - 1] = chunks[i - 1][:end]

            # Trimma början på aktuell chunk
            start = find_first_good_break_index(chunks[i])
            if start > 0:
                chunks[i] = chunks[i][start:]

        return chunks

    def _load_instruction(self) -> str:
        return self.instruction_path.read_text(encoding=self.STANDARD_ENCODING)

    def _load_metrics(self, dump : bool = True) -> str:
        metrics = json.loads(self.metrics_path.read_text(encoding=self.STANDARD_ENCODING))
        if dump:
            return json.dumps(metrics, ensure_ascii=False, indent=2)
        else:
            return metrics

    def _build_request_text(self, extracted_text: str) -> str:

        request_text = f"""
            Analysera följande årsredovisningsutdrag:
            ----------------
            {extracted_text}
            ----------------
            Returnera endast en giltig JSON-struktur enligt instruktionerna – ingen annan text.
        """
        return request_text

    def _build_system_prompt(self, the_year: int = None, only_metrics: list[str] = None):
        """Build the system prompt.

        only_metrics restricts both the definitions and the instructions to a
        subset, which is what the targeted second pass uses: sending all 18
        definitions again would re-invite the model to re-answer what it
        already got right.
        """
        instruction = self._load_instruction()
        if only_metrics:
            definitions = [
                entry
                for entry in self._load_metrics(dump=False)
                if entry.get(schema.METRIC_KEY) in set(only_metrics)
            ]
            metrics_json = json.dumps(definitions, ensure_ascii=False, indent=2)
        else:
            metrics_json = self._load_metrics()

        if self.USE_STRUCTURED_OUTPUT:
            instruction = (
                instruction
                + "\n\n"
                + (
                    schema.describe_for_second_pass(only_metrics)
                    if only_metrics
                    else schema.describe_for_prompt(schema.load_metric_names(self.metrics_path))
                )
            )

        if the_year:
            system_prompt = f"""
                {instruction}
                -------------
                Följande nyckeltal ska extraheras för {the_year}:
                -------------
                {metrics_json}
            """
        else:
            system_prompt = f"""
                {instruction}
                -------------
                Följande nyckeltal ska extraheras:
                -------------
                {metrics_json}
            """
        return system_prompt

    @staticmethod
    def _is_reasoning_model(gpt_model: str) -> bool:
        """gpt-5.x and the o-series reject temperature/top_p overrides."""
        name = (gpt_model or "").lower()
        return name.startswith(JBGAnnualReportAnalyzer.MODEL_GPT_5_MARKER) or bool(
            re.match(r"^o\d", name)
        )


    def _make_openai_api_call(
        self,
        system_prompt,
        request_text: str,
        model: str = "",
        response_schema: dict | None = None,
    ) -> str:
        model_used = model if model else self.DEFAULT_MODEL
        max_retries = 5
        initial_delay = 1.5
        backoff_factor = 2.0
        attempt = 0

        while attempt < max_retries:
            try:
                logger.debug(f"Open AI call attempt: {attempt}")
                kwargs = {
                    "model": model_used,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": request_text},
                    ],
                }
                # Reasoning models reject a temperature other than 1 and ignore
                # top_p, so only send them where they mean something.
                if not self._is_reasoning_model(model_used):
                    kwargs["temperature"] = self.DEFAULT_OPENAI_TEMPERATURE
                    kwargs["top_p"] = self.DEFAULT_OPENAI_TOP_P
                if response_schema is not None:
                    kwargs["response_format"] = {
                        "type": "json_schema",
                        "json_schema": response_schema,
                    }

                response = self.openai_client.chat.completions.create(**kwargs)

                # Token usage is informational only. The previous hard-coded
                # MODEL_TOKEN_LIMITS table had no entry for gpt-5.x, so it fell
                # back to 8192 and warned about every normal-sized call.
                usage = getattr(response, "usage", None)
                if usage:
                    logger.debug(
                        f"Tokens för '{model_used}': prompt={usage.prompt_tokens}, "
                        f"svar={usage.completion_tokens}, totalt={usage.total_tokens}"
                    )

                choice = response.choices[0]
                if getattr(choice.message, "refusal", None):
                    # A structured-output refusal is final; retrying wastes calls.
                    logger.error(f"Modellen avvisade förfrågan: {choice.message.refusal}")
                    raise RuntimeError(f"Modellen avvisade förfrågan: {choice.message.refusal}")

                if choice.finish_reason == "length":
                    raise RuntimeError(
                        "GPT-svaret nådde tokengränsen och är trunkerat. "
                        "Minska MAX_TOKENS eller höj modellens svarsgräns."
                    )
                if choice.finish_reason != "stop":
                    raise RuntimeError(
                        f"GPT-svar avslutades med '{choice.finish_reason}' – kan vara trunkerat eller felaktigt."
                    )

                logger.debug(f"GPT-response:\n{response}")
                return choice.message.content.strip()

            except RETRYABLE_OPENAI_ERRORS as ex:
                attempt += 1
                if attempt >= max_retries:
                    logger.error(f"OpenAI API-fel efter {max_retries} försök: {ex}")
                    raise RuntimeError(
                        f"Maximalt antal försök för API-anropet överskreds: {ex}"
                    ) from ex
                delay = initial_delay * (backoff_factor ** (attempt - 1))
                logger.warning(
                    f"OpenAI API-fel (försök {attempt}/{max_retries}): {ex}. "
                    f"Försöker igen om {delay:.1f}s."
                )
                time.sleep(delay)
            except RuntimeError as ex:
                if "avvisade" in str(ex):
                    raise
                # Raised above for a truncated / non-"stop" completion. Worth one
                # more attempt, since it is often caused by a transient hiccup.
                attempt += 1
                if attempt >= max_retries:
                    logger.error(f"Trunkerat GPT-svar efter {max_retries} försök: {ex}")
                    raise
                delay = initial_delay * (backoff_factor ** (attempt - 1))
                logger.warning(f"{ex} Försöker igen om {delay:.1f}s.")
                time.sleep(delay)
            except APIError as ex:
                # 4xx errors (bad model name, invalid key, malformed request) will
                # never succeed on retry. Fail immediately with a clear message.
                logger.error(f"Icke återförsökbart fel i OpenAI-anrop: {ex}")
                raise RuntimeError(f"OpenAI-anropet misslyckades: {ex}") from ex

        raise RuntimeError("Maximalt antal försök för API-anropet överskreds.")

    def _clean_presumed_prefixed_json(self, presumed_prefixed_json):
        if presumed_prefixed_json.startswith("```json"):
            presumed_prefixed_json = presumed_prefixed_json.removeprefix("```json").strip()
        if presumed_prefixed_json.endswith("```"):
            presumed_prefixed_json = presumed_prefixed_json.removesuffix("```").strip()
        return presumed_prefixed_json


    def _deep_merge_json_objects(self, json_list: list[dict]) -> dict:
        """
        Merge JSON object, but with less risk of overwriting existing values with null values

        Args:
            json_list (List[dict]): _description_

        Returns:
            dict: _description_
        """
        def deep_merge(a: dict, b: dict) -> dict:
            result = dict(a)
            for k, v in b.items():
                if (
                    k in result
                    and isinstance(result[k], Mapping)
                    and isinstance(v, Mapping)
                ):
                    result[k] = deep_merge(result[k], v)
                else:
                    # Huvudpatchen: skriv inte över med None
                    if k not in result or (result[k] is None and v is not None):
                        result[k] = v
                    elif result[k] is None and v is None:
                        result[k] = None  # Båda är None, skriv None
                    # Annars: behåll existerande värde
            return result

        result = {}
        for obj in json_list:
            result = deep_merge(result, obj)

        return result


    def _merge_json_fund_data(self, data):

        logger.debug(f"JSON data to be merged: {data}")

        # Check if what we get is really is a non-empty dictionary
        if not (isinstance(data, dict) and bool(data)):
            return None, None

        # Choose the longest name assuming it's the most descriptive
        preferred_name = max(data.keys(), key=len)

        # Build a new JSON structure with only one name
        merged = {preferred_name: {}}

        conflicts = []

        for _fund_name, year_data in data.items():
            for year, indicators in year_data.items():
                if year not in merged[preferred_name]:
                    merged[preferred_name][year] = {}

                for key, value in indicators.items():
                    if key not in merged[preferred_name][year]:
                        if value is None:
                            continue
                        else:
                            merged[preferred_name][year][key] = value
                    else:
                        # Conflict detected
                        existing = merged[preferred_name][year][key]
                        if existing != value:

                            # Store as list if conflict
                            if not isinstance(existing, list):
                                merged[preferred_name][year][key] = [existing]
                            merged[preferred_name][year][key].append(value)
                            conflicts.append((year, key, existing, value))

        logger.debug(f"JSON data after merge: {merged}")
        return merged, conflicts

    def _merge_conflicted_values_json_objects(self, json_obj: dict) -> tuple[dict, int]:

        num_consolidated = 0
        for fund, year_data in json_obj.items():
            for year, metrics in year_data.items():
                consolidated = {}
                for key, value in metrics.items():
                    if key in consolidated:
                        continue

                    # Hantera listor med dictar
                    if isinstance(value, list) and all(isinstance(v, dict) for v in value):
                        # Group the conflicting entries by value. Keep each group's
                        # own certainty/comment instead of letting the last entry of
                        # the loop overwrite them all, and keep the group backed by
                        # the most sources (ties broken by highest certainty).
                        grouped = {}
                        for v in value:
                            val = v.get(self.FIELD_VALUE, "")
                            src = v.get(self.FIELD_SOURCE, "") or ""
                            entry = grouped.setdefault(
                                val,
                                {
                                    "sources": set(),
                                    self.FIELD_CERTAINTY: v.get(self.FIELD_CERTAINTY, ""),
                                    self.FIELD_COMMENT: v.get(self.FIELD_COMMENT, ""),
                                },
                            )
                            entry["sources"].update(
                                s.strip()
                                for s in src.replace(self.SOURCE_PREFIX, "").split(",")
                                if s.strip()
                            )

                        def _rank(item):
                            val, meta = item
                            # certainty_rank handles both the named levels and
                            # the floats older result files contain.
                            certainty = schema.certainty_rank(
                                meta.get(self.FIELD_CERTAINTY)
                            )
                            # Certainty first, then corroborating sources. A model
                            # that says "explicit, found in Not 12" should beat
                            # one that says "härledd" from two vaguer places.
                            return (val is not None, certainty, len(meta["sources"]))

                        best_val, best_meta = max(grouped.items(), key=_rank)
                        srcs = best_meta["sources"]
                        consolidated[key] = {
                            self.FIELD_VALUE: best_val,
                            self.FIELD_SOURCE: f"{self.SOURCE_PREFIX} {', '.join(sorted(srcs))}",
                            self.FIELD_CERTAINTY: best_meta[self.FIELD_CERTAINTY],
                            self.FIELD_COMMENT: best_meta[self.FIELD_COMMENT],
                        }
                        if len(grouped) > 1:
                            logger.warning(
                                f"Motstridiga värden för '{key}' ({year}): "
                                f"{sorted(grouped.keys(), key=str)}. Valde {best_val!r}."
                            )
                        num_consolidated += sum(
                            max(len(m["sources"]), 1) for m in grouped.values()
                        ) - 1
                    else:
                        similar_entries = [
                            (alt_key, alt_value)
                            for alt_key, alt_value in metrics.items()
                            if alt_key != key and alt_key.startswith(key)
                        ]
                        main_value = value.get(self.FIELD_VALUE) if isinstance(value, dict) else None
                        sources = set()
                        if main_value:
                            main_cert = value.get(self.FIELD_CERTAINTY)
                            main_comm = value.get(self.FIELD_COMMENT)
                        else:
                            main_cert, main_comm = None, None
                        all_entries = [(key, value)] + similar_entries
                        for _alt_key, alt_val in all_entries:
                            if isinstance(alt_val, dict) and alt_val.get(self.FIELD_VALUE) == main_value:
                                source = alt_val.get(self.FIELD_SOURCE, "")
                                sources.update(s.strip() for s in source.replace(self.SOURCE_PREFIX, "").split(","))
                        if sources and main_value is not None:
                            consolidated[key] = {
                                self.FIELD_VALUE: main_value,
                                self.FIELD_SOURCE: f"{self.SOURCE_PREFIX} {', '.join(sorted(sources))}",
                                self.FIELD_CERTAINTY: main_cert,
                                self.FIELD_COMMENT: main_comm
                            }
                            num_consolidated += len(sources) - 1
                        else:
                            consolidated[key] = value
                json_obj[fund][year] = consolidated

        return json_obj, num_consolidated


    def _prompt_instructions_pdf_page_offset(self):

        system_prompt = """
        Du får ett textutdrag från en PDF-sida. Försök analysera skillnaden mellan den faktiska sidpositionen
        i dokumentet (PDF-sidnummer) och det tryckta sidnumret som står i dokumentets innehåll.
        Svaret ska vara:
        - En **ensam siffra**: skillnaden mellan PDF-sidnummer och tryckt nummer
        - Om det inte finns något tryckt nummer i utdraget, skriv siffran 0

        **Exempel:**
        Om du läser text från PDF-sida 3, och det står "2" som tryckt sidnummer, ska svaret vara: 1

        Svara alltid enbart med en siffra.
        """
        return system_prompt

    def _prompt_instructions_pdf_actual_year(self):

        system_prompt = """
        Du får ett textutdrag från en PDF-sida. Försök analysera vilket årtal texten handlar om.
        Svaret ska vara:
        - En **ensam siffra**
        - Om det inte finns något årtal i texten, svara då med "-1", vilket jag kommer tolka som okänt.
        - Om flera årtal förekommer i texten, svara med det årtal som förekommer flest gånger. Är det finns
        flera årtal som förekommer lika många gånger, svara då med "-2", vilket jag kommer tolka som obestämbart.

        Några vägledande exempel:
        - Texten innehåller årtalen: 2021, 2022, 2022, 2023 → svar: 2022
        - Texten innehåller endast 2023 → svar: 2023
        - Texten innehåller 2020, 2021 → svar: -2
        - Texten innehåller inga årtal → svar: -1

        Svara alltså alltid bara med en siffra: -1, -2 eller med ett årtal.
        """
        return system_prompt

    def _response_schema(self, only_metrics: list[str] = None) -> dict | None:
        """The strict schema for an extraction call, built once per metric set.

        Restricting the enum to the missing metrics makes it structurally
        impossible for the second pass to answer about anything else.
        """
        if not self.USE_STRUCTURED_OUTPUT:
            return None
        if self._schema_cache is None:
            self._schema_cache = {}

        key = tuple(sorted(only_metrics)) if only_metrics else None
        if key not in self._schema_cache:
            names = list(only_metrics) if only_metrics else schema.load_metric_names(
                self.metrics_path
            )
            self._schema_cache[key] = schema.build_schema(names)
            logger.debug(f"Byggde JSON-schema för {len(names)} nyckeltal.")
        return self._schema_cache[key]

    def _chunk_text_for_model(self, full_text: str, model: str = "") -> list[str]:
        """Split the extracted text for the model.

        Page-aware splitting keeps whole pages together and repeats the
        [Sida N] header at the top of every chunk. The previous token-window
        split could cut a balance sheet in half and strip the page marker off
        the front of a chunk, which is what the "källa" field depends on.
        """
        if self.USE_PAGE_AWARE_CHUNKING:
            return self._chunk_text_by_pages(full_text, max_tokens=self.MAX_TOKENS, model=model)

        # Kept as an escape hatch if page markers are ever unavailable.
        return self._chunk_text_with_overlap(
            text=full_text,
            max_tokens=self.MAX_TOKENS,
            max_overlap_tokens=self.MAX_TOKEN_OVERLAP,
            model=model,
        )

    @staticmethod
    def _page_marker(page_text: str) -> str:
        """The leading "[Sida N]" header of a page block, or an empty string."""
        match = re.match(r"\[Sida [^\]]+\]", page_text.lstrip())
        return match.group(0) if match else ""

    def _chunk_text_by_pages(self, full_text: str, max_tokens: int, model: str = "") -> list[str]:
        enc = self._get_encoder_for_model(model or self.DEFAULT_MODEL)

        # Split on the page markers written by _extract_text_from_pdf, keeping
        # each marker attached to the page it introduces.
        parts = re.split(r"(?=\[Sida [^\]]+\]\n)", full_text)
        pages = [part for part in (p.strip() for p in parts) if part]
        if not pages:
            pages = [full_text]

        chunks: list[str] = []
        current: list[str] = []
        current_tokens = 0

        for page in pages:
            page_tokens = len(enc.encode(page))

            # A single page bigger than the budget has to be split on its own.
            if page_tokens > max_tokens:
                if current:
                    chunks.append("\n\n".join(current))
                    current, current_tokens = [], 0
                # Clamp the overlap: MAX_TOKEN_OVERLAP is sized for the normal
                # budget and would exceed a smaller one, which the splitter
                # rejects outright.
                overlap = min(self.MAX_TOKEN_OVERLAP, max(1, max_tokens // 5))
                logger.info(
                    f"En sida överskrider chunkgränsen ({page_tokens} > {max_tokens} tokens). "
                    "Delar sidan med överlapp."
                )
                sub_chunks = self._chunk_text_with_overlap(
                    text=page,
                    max_tokens=max_tokens,
                    max_overlap_tokens=overlap,
                    model=model,
                )
                # Every sub-chunk after the first would otherwise start mid-page
                # with no [Sida N] header, leaving the model nothing to cite.
                marker = self._page_marker(page)
                chunks.extend(
                    sub if index == 0 or not marker or sub.startswith(marker)
                    else f"{marker}\n{sub}"
                    for index, sub in enumerate(sub_chunks)
                )
                continue

            if current and current_tokens + page_tokens > max_tokens:
                chunks.append("\n\n".join(current))
                current, current_tokens = [], 0

            current.append(page)
            current_tokens += page_tokens

        if current:
            chunks.append("\n\n".join(current))
        return chunks

    def _analyse_chunk(self, index: int, total: int, chunk: str, the_year: int, model: str):
        """Send one chunk and return the parsed nested result, or None."""
        prompt = self._build_system_prompt(the_year=the_year)
        request = self._build_request_text(chunk)
        logger.info(f"Skickar chunk {index + 1}/{total} till GPT...")
        try:
            response = self._make_openai_api_call(
                prompt, request, model, response_schema=self._response_schema()
            )
        except Exception as e:
            logger.error(f"Fel vid GPT-anrop chunk {index + 1}: {e}")
            return None

        logger.debug(f"GPT-rådata chunk {index + 1}:\n{response}")

        try:
            if self.USE_STRUCTURED_OUTPUT:
                # The schema guarantees shape, so parse directly. No fence
                # stripping, no "does it start with a brace" guess.
                payload = json.loads(response)
                result = schema.flat_to_nested(payload, fallback_year=the_year)
            else:
                cleaned = self._clean_presumed_prefixed_json(response).strip()
                if not cleaned.startswith("{") or not cleaned.endswith("}"):
                    logger.warning(
                        f"GPT-svar för chunk {index + 1} är inte giltig JSON – hoppar över."
                    )
                    return None
                result = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.warning(f"Misslyckades att parsa JSON för chunk {index + 1}: {e}")
            return None

        found = self._count_non_null_metrics(result)
        if found == 0:
            logger.info(f"Chunk {index + 1} gav inga nyckeltal. Hoppar över.")
            return None
        logger.info(f"Chunk {index + 1} gav {found} nyckeltal.")
        return result

    def _analyse_chunks(self, chunks: list[str], the_year: int, model: str) -> list[dict]:
        """Run the chunks, concurrently when there is more than one.

        Replaces the fixed time.sleep between calls. Rate limits are handled by
        the retry logic in _make_openai_api_call, which is what it is for.
        """
        if len(chunks) <= 1:
            results = [self._analyse_chunk(0, len(chunks), c, the_year, model) for c in chunks]
            return [r for r in results if r]

        workers = min(self.MAX_CONCURRENT_CHUNKS, len(chunks))
        logger.info(f"Analyserar {len(chunks)} chunkar med {workers} parallella anrop.")
        indexed: list[tuple[int, dict]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._analyse_chunk, i, len(chunks), chunk, the_year, model): i
                for i, chunk in enumerate(chunks)
            }
            for future in as_completed(futures):
                result = future.result()
                if result:
                    indexed.append((futures[future], result))

        # Merge in chunk order so the result does not depend on which call
        # happened to finish first.
        return [result for _, result in sorted(indexed, key=lambda pair: pair[0])]

    def _missing_metrics(self, result: dict) -> list[str]:
        """Metric names with no value anywhere in a single file's result."""
        found = set()
        for years in (result or {}).values():
            if not isinstance(years, dict):
                continue
            for metrics in years.values():
                if not isinstance(metrics, dict):
                    continue
                for name, entry in metrics.items():
                    if isinstance(entry, dict) and entry.get(self.FIELD_VALUE) is not None:
                        found.add(name)
        return [n for n in schema.load_metric_names(self.metrics_path) if n not in found]

    def _graft_metrics(self, result: dict, metrics: dict) -> int:
        """Add metrics to a file's result without overwriting what is there.

        The values are attached to the fund and year the first pass already
        established, rather than to whatever name the second pass reported, so
        a differently-worded fund name cannot split the result in two.
        """
        if not metrics or not result:
            return 0
        fund = next(iter(result))
        years = result[fund]
        if not isinstance(years, dict) or not years:
            return 0
        year = next(iter(years))
        target = years[year]

        added = 0
        for name, entry in metrics.items():
            if not isinstance(entry, dict) or entry.get(self.FIELD_VALUE) is None:
                continue
            existing = target.get(name)
            if isinstance(existing, dict) and existing.get(self.FIELD_VALUE) is not None:
                continue  # first pass already found it; leave it alone
            enriched = dict(entry)
            comment = enriched.get(self.FIELD_COMMENT) or ""
            enriched[self.FIELD_COMMENT] = f"{self.SECOND_PASS_TAG} {comment}".strip()
            target[name] = enriched
            added += 1
        return added

    def _second_pass_for_missing(
        self, result: dict, chunks: list[str], the_year: int, model: str
    ) -> dict:
        """Ask again for the metrics the first pass did not find.

        Cheap by construction: it only fires when something is absent, asks
        about nothing else, and stops as soon as the list is empty.
        """
        missing = self._missing_metrics(result)
        if not missing:
            return result

        total = len(schema.load_metric_names(self.metrics_path))
        if len(missing) > total * self.SECOND_PASS_MAX_MISSING_RATIO:
            # Most of the metrics absent means the first pass went wrong rather
            # than overlooked a line; re-reading the same text will not help.
            logger.warning(
                f"{len(missing)} av {total} nyckeltal saknas. Det tyder på ett "
                "fel i första genomgången snarare än förbisedda poster. "
                "Hoppar över riktad omsökning."
            )
            return result

        logger.info(
            f"{len(missing)} nyckeltal saknas efter första genomgången: "
            f"{', '.join(missing)}. Gör en riktad omsökning."
        )

        for index, chunk in enumerate(chunks):
            if not missing:
                break
            prompt = self._build_system_prompt(the_year=the_year, only_metrics=missing)
            try:
                response = self._make_openai_api_call(
                    prompt,
                    self._build_request_text(chunk),
                    model,
                    response_schema=self._response_schema(missing),
                )
            except Exception as ex:
                logger.warning(f"Riktad omsökning misslyckades för chunk {index + 1}: {ex}")
                continue

            try:
                payload = json.loads(response)
            except json.JSONDecodeError as ex:
                logger.warning(f"Kunde inte tolka svaret från riktad omsökning: {ex}")
                continue

            nested = schema.flat_to_nested(payload, fallback_year=the_year)
            metrics = {}
            for years in nested.values():
                for per_year in years.values():
                    metrics.update(per_year)

            added = self._graft_metrics(result, metrics)
            if added:
                logger.info(f"Riktad omsökning hittade {added} ytterligare nyckeltal.")
            missing = self._missing_metrics(result)

        if missing:
            logger.info(
                f"Efter omsökning saknas fortfarande {len(missing)} nyckeltal: "
                f"{', '.join(missing)}. De finns sannolikt inte i dokumentet."
            )
        return result

    def do_analysis(
        self,
        output_path: Path,
        model: str = "gpt-4o",
        progress_callback=None,
    ) -> Path:
        """Analyse every PDF found under upload_dir.

        progress_callback(done, total, filename) is called as each file
        finishes, so a caller can report progress on a job that runs for
        minutes. Failures in the callback must never abort the analysis.
        """

        if not self.upload_files:
            logger.error("No PDF files found for analysis.")
            raise ValueError("No valid PDF files found.")

        total_result = []


        total_files = len(self.upload_files)

        def report(done: int, name: str):
            if progress_callback is None:
                return
            try:
                progress_callback(done, total_files, name)
            except Exception as ex:  # pragma: no cover - never fail the run
                logger.warning(f"Kunde inte rapportera framsteg: {ex}")

        # We loop over all the pdf files
        for file_index, _pdf_path in enumerate(self.upload_files):
            logger.info(f"Processar fil: {_pdf_path}")
            # Reported at the start, so the progress message names the file
            # currently being worked on rather than the one that just finished.
            report(file_index, _pdf_path.name)

            # Use masking if required
            if self.use_masking:
                masker = self._get_masker()
                pdf_output_path = Path(_pdf_path.with_name(_pdf_path.stem + "_masked.pdf"))
                pdf_path = masker.do_masking(_pdf_path, pdf_output_path, logger=logger)

                if pdf_path is None:
                    logger.error(f"Maskering misslyckades för fil: {_pdf_path.name}. Hoppar över denna fil i analysen.")
                    continue
            else:
                pdf_path = _pdf_path

            # Get the current year for the analysis
            try:
                the_year = self._find_primary_year_from_pdf(pdf_path, model=model)
                logger.info(f"Extraherade aktuellt år från: {pdf_path.name} som: {the_year}")
                if the_year < 0:
                    raise RuntimeError(f"Could not extract main year from {pdf_path} to be used in system prompt.")
            except RuntimeError as ex:
                logger.warning(f"{str(ex)}. Setting year unknown.")
                the_year = None

            # Get the full text of the pdf
            logger.info(f"Extraherar text från: {pdf_path.name}")
            try:
                full_text = self._extract_text_from_pdf_from_pdf(pdf_path, model=model)
                #logger.debug(f"The full text for {pdf_path} is: {full_text}")
            except FileTypeException:
                logger.warning(f"Skipping file {pdf_path} since I could not extract any text from it (perhaps it was scanned?)")
                continue

            # Try to fix broken lines that can contain key numbers and values
            if self.FIX_BROKEN_LINES_WITH_KEY_NUMBERS:
                try:
                    full_text = self._merge_broken_key_number_lines(full_text, self._extract_key_number_terms())
                    logger.debug(f"The full text for {pdf_path} where broken lines with key numbers are merged is: {full_text}")
                except Exception:
                    logger.warning(f"Could not merge broken lines with key numbers and data in for full text of file: {pdf_path}")

            # Divide the text into chunks. Page-aware by default, so the
            # [Sida N] markers that populate the "källa" field survive.
            chunks = self._chunk_text_for_model(full_text, model=model)
            logger.info(f"{len(chunks)} chunk(s) genererade för {pdf_path.name}")

            partial_results = self._analyse_chunks(chunks, the_year=the_year, model=model)

            # Put together and clean up the result
            appended_result = self._deep_merge_json_objects(partial_results)
            logger.debug("In do_analysis: partial_results:")
            for result in partial_results:
                logger.debug(f"{result}")
            logger.debug(f"In do_analysis: appended_result: {appended_result}")
            if appended_result:
                appended_result, conflicts = self._merge_json_fund_data(appended_result)
                if conflicts:
                    logger.warning(
                        f"Last merge of JSON data resulted in {len(conflicts)} conflict(s) "
                        f"for {', '.join(sorted({c[1] for c in conflicts}))}"
                    )
                    logger.debug(f"Conflict detail: {conflicts}")
                    appended_result, num_merged_values = self._merge_conflicted_values_json_objects(appended_result)
                    if num_merged_values > 0:
                        logger.info(f"Merged {num_merged_values} duplicate values in appended JSON structure")
                    else:
                        logger.warning("No conclicts were merged.")

                if self.USE_SECOND_PASS_FOR_MISSING:
                    appended_result = self._second_pass_for_missing(
                        appended_result, chunks, the_year=the_year, model=model
                    )

                total_result.append(appended_result)

        report(total_files, "")

        # Write result to JSON output
        if total_result:
            final_result = self._deep_merge_json_objects(total_result)

            # Canonicalise the fund names before anything downstream keys off
            # them, so two spellings of one fund cannot become two columns.
            if self.fund_list_path and Path(self.fund_list_path).is_file():
                final_result, unresolved = normalise_result_fund_names(
                    final_result, self.fund_list_path
                )
                if unresolved:
                    logger.warning(
                        f"{len(unresolved)} kassanamn kunde inte normaliseras: "
                        f"{', '.join(sorted(unresolved))}"
                    )

            # Arithmetic sanity checks. These do not change the data; they tell
            # the reader which figures to verify against the source document.
            self.validation_findings = validation.validate(final_result)
            validation.log_findings(self.validation_findings)
            validation.log_certainty_histogram(final_result)

            # The findings go into the JSON as well, not just the log and the
            # Excel colour coding, so they survive whichever format is chosen.
            # Keys starting with an underscore are metadata, not funds; the
            # exporters skip them.
            if self.validation_findings:
                final_result[self.VALIDATION_KEY] = [
                    finding.as_dict() for finding in self.validation_findings
                ]

            output_path.write_text(json.dumps(final_result, ensure_ascii=False, indent=2), encoding=self.STANDARD_ENCODING)
            logger.info(f"Analysresultat sparat till: {output_path}")
            return output_path
        else:
            logger.warning("Inga resultat sparades.")
            return None

    def _count_non_null_metrics(self, json_obj: dict) -> int:
        count = 0
        for _fund, years in json_obj.items():
            for _year, metrics in years.items():
                for _metric_name, metric_data in metrics.items():
                    if isinstance(metric_data, dict) and metric_data.get("värde") is not None:
                        count += 1
        return count
