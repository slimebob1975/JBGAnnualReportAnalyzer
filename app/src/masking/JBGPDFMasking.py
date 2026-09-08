import json
import logging
import os
import re
import shutil
import sys
import tempfile
from logging import Logger
from pathlib import Path

try:  # PyMuPDF >= 1.24.3 ships the package under its real name
    import pymupdf
except ImportError:  # pragma: no cover - older PyMuPDF only exposes "fitz"
    import fitz as pymupdf

NER_MODEL = "KBLab/bert-base-swedish-cased-ner"


class PDFMasker:
    def __init__(self, ner=None):
        # transformers (and torch) are imported lazily so that the rest of the
        # package, and its tests, can be used without the ~2 GB ML stack.
        if ner is not None:
            self.ner = ner
        else:
            try:
                from transformers import pipeline
            except ImportError as ex:
                # Masking is an optional extra. Say so plainly rather than
                # letting an ImportError surface as "Fel vid analys".
                raise RuntimeError(
                    "Maskning är påslagen men transformers är inte installerat. "
                    "Installera med: pip install '.[masking]' – eller stäng av "
                    "maskningen i formuläret."
                ) from ex

            try:
                import torch  # noqa: F401
            except ImportError as ex:
                # transformers imports fine without a backend and then fails
                # much later, inside pipeline(), with
                # "NameError: name 'torch' is not defined". Check up front so
                # the message names the missing package.
                raise RuntimeError(
                    "Maskning är påslagen men PyTorch är inte installerat. "
                    "transformers behöver en backend för att kunna köra "
                    "NER-modellen. Installera med: pip install '.[masking]' "
                    "eller pip install torch – eller stäng av maskningen i "
                    "formuläret."
                ) from ex

            self.ner = pipeline(
                "ner",
                model=NER_MODEL,
                tokenizer=NER_MODEL,
                aggregation_strategy="simple",
            )

    def sanitize_pdf(self, input_pdf: Path, logger: Logger = None) -> Path:
        temp_dir = tempfile.mkdtemp()
        sanitized_path = Path(temp_dir) / f"{input_pdf.stem}_sanitized.pdf"
        try:
            doc = pymupdf.open(input_pdf)
            doc.save(sanitized_path, garbage=4, deflate=True, clean=True)
            if logger:
                logger.info(f"Sanitized PDF saved to: {sanitized_path}")
            return sanitized_path
        except Exception as e:
            if logger:
                logger.warning(f"Sanitizing failed, proceeding with original file: {e}")
            return input_pdf

    def extract_text(self, pdf_path):
        doc = pymupdf.open(pdf_path)
        return [page.get_text() for page in doc]

    def _clean_entities(self, entities):
        cleaned = []
        for word in entities:
            if re.match(r"^[#@]", word):
                continue
            if word.lower() in {"and", "do", "gr", "vice", "statens", "is", "revis", "aukt", "kass", "Led", "lem", "mar", "sum", "id",
                                 "supp", "cer", "föret", "kassach", "gransk", "general", "sek", "arb"}:
                continue
            if word in {"Signerat", "jan", "kassa", "signe", "dista", "manuell", "Sek", "General"}:
                continue
            updated_word = self._normalize_hyphens(word.strip())
            updated_word = self._deduplicate_if_mirrored_with_space(updated_word)
            cleaned.append(updated_word)
        return cleaned

    def detect_sensitive_terms(self, page_texts, max_chunk_chars=512):
        sensitive_words = set()
        for text in page_texts:
            for i in range(0, len(text), max_chunk_chars):
                chunk = text[i:i + max_chunk_chars]
                try:
                    ner_results = self.ner(chunk)
                    names = {r['word'].strip() for r in ner_results if r['entity_group'] == 'PER'}
                    sensitive_words.update(names)
                except Exception as e:
                    print(f"NER-fel: {e}")
        full_text = "\n".join(page_texts)
        pnr_matches = set(re.findall(r"\b\d{6}[-+]\d{4}\b", full_text))
        full_text = self._fix_split_emails(full_text)
        email_matches = set(re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", full_text))
        twitter_matches = set(re.findall(r"@[A-Za-z0-9_]{1,15}", full_text))
        dob_matches = set(re.findall(r"\bDOB:\s*(?:19|20)\d{2}/\d{2}/\d{2}\b", full_text))
        extra_fornamn, extra_efternamn = self._get_extra_names()
        all_terms = sensitive_words.union(pnr_matches, email_matches, twitter_matches, dob_matches, extra_fornamn, extra_efternamn)
        return self._clean_entities(all_terms)

    @staticmethod
    def _fix_split_emails(text: str) -> str:
        return re.sub(r'(@[^\s@]*)[\r\n]+([^\s@]+\.[a-z]{2,10})', r'\1\2', text, flags=re.IGNORECASE)

    @staticmethod
    def _extra_names_path() -> Path:
        override = os.getenv("JBG_MASKING_EXTRA_NAMES")
        if override:
            return Path(override)
        return Path(__file__).resolve().parents[2] / "config" / "masking_extra_names.json"

    _extra_names_cache = None

    @classmethod
    def _get_extra_names(cls):
        """Names to always mask, on top of whatever the NER model finds.

        These used to be hardcoded in this file, which meant real people's
        names were committed to the repository. They now live in a
        gitignored config file (see masking_extra_names.example.json), or
        wherever JBG_MASKING_EXTRA_NAMES points.
        """
        if cls._extra_names_cache is not None:
            return cls._extra_names_cache

        path = cls._extra_names_path()
        if not path.is_file():
            logging.getLogger(__name__).info(
                f"Ingen fil med extra namn att maskera ({path}). "
                "Endast NER-modellens träffar maskeras."
            )
            cls._extra_names_cache = (set(), set())
            return cls._extra_names_cache

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as ex:
            logging.getLogger(__name__).warning(
                f"Kunde inte läsa {path}: {ex}. Endast NER-träffar maskeras."
            )
            cls._extra_names_cache = (set(), set())
            return cls._extra_names_cache

        first = {n.strip() for n in data.get("fornamn", []) if str(n).strip()}
        last = {n.strip() for n in data.get("efternamn", []) if str(n).strip()}
        logging.getLogger(__name__).info(
            f"Läste {len(first)} förnamn och {len(last)} efternamn ur {path.name}."
        )
        cls._extra_names_cache = (first, last)
        return cls._extra_names_cache

    @staticmethod
    def _deduplicate_if_mirrored_with_space(s: str) -> str:
        s = s.strip()
        if len(s) < 5 or len(s) % 2 == 0:
            return s
        mid = len(s) // 2
        if s[mid] != " ":
            return s
        left = s[:mid].strip().lower()
        right = s[mid + 1:].strip().lower()
        if left == right:
            return s[:mid].strip()
        return s

    @staticmethod
    def _normalize_hyphens(text: str) -> str:
        return re.sub(r"\s*-\s*", "-", text)

    # Padding around a word's own box, so descenders and antialiasing edges
    # are covered too.
    REDACTION_PADDING = 1.0

    @staticmethod
    def _tokenise(word: str) -> str:
        """A word reduced to what should be compared: no surrounding
        punctuation, case folded."""
        return re.sub(r"^\W+|\W+$", "", word or "").casefold()

    # A personal name does not appear hundreds of times in an annual report.
    # One corpus had "Comfact", the e-signing vendor, stamped on all 204 pages
    # and tagged as an entity; it alone blocked a whole document.
    MIN_TERM_LENGTH = 4
    MAX_TERM_OCCURRENCES = 25

    @classmethod
    def _plausible_terms(cls, pdf_path: Path, terms) -> tuple:
        """Drop terms that cannot be personal data.

        Named-entity recognition over a financial report produces a fair
        number of false positives: vendor names in page footers, abbreviations
        like "Ref" and "IAF", and stray three-letter tokens. Redacting those
        damages the document without protecting anybody, and because the
        verification checks whatever it is given, a single such term can block
        an otherwise clean file.

        Returns (kept, too_short, too_common).
        """
        with pymupdf.open(pdf_path) as doc:
            haystack = cls._normalise_haystack(
                "\n".join(page.get_text() for page in doc)
            )

        kept, too_short, too_common = [], [], []
        for term in terms:
            candidate = (term or "").strip()
            if len(candidate) < cls.MIN_TERM_LENGTH:
                too_short.append(candidate)
                continue
            needle = cls._normalise_for_search(candidate)
            occurrences = len(re.findall(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack))
            if occurrences > cls.MAX_TERM_OCCURRENCES:
                too_common.append((candidate, occurrences))
                continue
            kept.append(term)
        return kept, too_short, too_common

    @classmethod
    def _joined_words(cls, page) -> list:
        """The page's words, with line-break hyphenation undone.

        get_text("words") yields "Gun-" and "hild" as separate tokens, so a
        name split across a line was never located, while the verifier (which
        joins them) could read it perfectly. Each entry is
        (token, [rects]) so a joined word still redacts both halves.
        """
        words = page.get_text("words")
        entries, index = [], 0
        while index < len(words):
            raw = words[index][4] or ""
            rects = [pymupdf.Rect(words[index][:4])]
            token = cls._tokenise(raw)
            if raw.endswith("-") and index + 1 < len(words):
                nxt = words[index + 1]
                # Only across a line break: within a line the hyphen is real.
                if nxt[6] != words[index][6] or nxt[5] != words[index][5]:
                    token = cls._tokenise(raw.rstrip("-")) + cls._tokenise(nxt[4])
                    rects.append(pymupdf.Rect(nxt[:4]))
                    index += 1
            entries.append((token, rects))
            index += 1
        return entries

    @classmethod
    def _locate_term(cls, page, term: str, entries=None) -> list:
        """Rectangles covering every occurrence of a term on a page.

        Works on the word list rather than page.search_for(), because the
        search matches a literal string and therefore misses occurrences that
        differ only in whitespace: a name wrapped across a line, separated by
        a non-breaking space, or by two spaces instead of one. On a real
        corpus those accounted for several terms that survived redaction while
        remaining perfectly legible.

        A term wrapped across a line yields one rectangle per line, so the
        boxes stay tight instead of covering the whole paragraph.
        """
        wanted = [cls._tokenise(w) for w in (term or "").split()]
        wanted = [w for w in wanted if w]
        if not wanted:
            return []

        # The caller passes the page's word list when redacting many terms,
        # so it is built once per page instead of once per term.
        entries = cls._joined_words(page) if entries is None else entries
        tokens = [tok for tok, _ in entries]

        # Index by first token so long term lists stay cheap on big documents.
        starts = [i for i, tok in enumerate(tokens) if tok == wanted[0]]

        rects = []
        for i in starts:
            if tokens[i:i + len(wanted)] != wanted:
                continue
            for _, boxes in entries[i:i + len(wanted)]:
                for box in boxes:
                    rect = pymupdf.Rect(box)
                    rect.x0 -= cls.REDACTION_PADDING
                    rect.y0 -= cls.REDACTION_PADDING
                    rect.x1 += cls.REDACTION_PADDING
                    rect.y1 += cls.REDACTION_PADDING
                    rects.append(rect)
        return rects

    @classmethod
    def _clear_unmaskable_pages(cls, doc, terms, logger) -> list:
        """Empty pages that still show a sensitive term and cannot be redacted.

        Some generated pages - e-signature listings in particular - carry text
        that page.get_text() returns but page.get_text("words") does not, so
        there is no box to draw over it. One document had a signature page
        showing the same name eight times with zero locatable words.

        A page that cannot be masked must not be sent, so its content stream is
        emptied. These pages hold signatures, never the balance sheet; if a
        page with figures were ever cleared it would show up immediately as
        missing metrics, and the page number is logged.
        """
        cleared = []
        for page in doc:
            visible = cls._normalise_haystack(page.get_text())
            if not visible:
                continue
            still_there = [
                term for term in terms
                if len(term.strip()) >= cls.MIN_VERIFIABLE_TERM
                and re.search(
                    rf"(?<!\w){re.escape(cls._normalise_for_search(term.strip()))}(?!\w)",
                    visible,
                )
            ]
            if not still_there:
                continue
            # Only when nothing can be located: otherwise redaction handles it.
            entries = cls._joined_words(page)
            if any(cls._locate_term(page, t, entries=entries) for t in still_there):
                continue
            for xref in page.get_contents():
                doc.update_stream(xref, b" ")
            cleared.append(page.number)
        return cleared

    @staticmethod
    def _strip_annotations(page) -> int:
        """Remove widgets and annotations before redacting.

        Text inside a form field's appearance stream is returned by text
        extraction but is not touched by apply_redactions(), so the names on an
        e-signature panel survived every pass. Every document in one corpus
        that leaked did so on its signature pages.

        The masked file is a throwaway intermediate that exists only to be
        read, so dropping annotations costs nothing: they carry signatures and
        comments, never the balance sheet.
        """
        removed = 0
        for widget in list(page.widgets()):
            page.delete_widget(widget)
            removed += 1
        for annot in list(page.annots()):
            page.delete_annot(annot)
            removed += 1
        return removed

    # A term shorter than this matches too much to check reliably: "Ek" would
    # fire on "Eket", "Ekonomi" and so on.
    MIN_VERIFIABLE_TERM = 3

    # Terms that survive redaction. None is acceptable; the point of masking is
    # that personal data does not leave the network.
    FAIL_ON_LEAK = True

    @staticmethod
    def _normalise_for_search(text: str) -> str:
        """Fold case and collapse whitespace."""
        return re.sub(r"\s+", " ", (text or "")).casefold()

    @classmethod
    def _normalise_haystack(cls, text: str) -> str:
        """As above, but also join words hyphenated across a line break.

        A reader sees "Gun-\nhild" as "Gunhild", and so should this check.
        Without it the verification passed on a document where the name was
        plainly legible, which is worse than no verification at all.
        """
        joined = re.sub(r"-\s*\n\s*", "", text or "")
        return cls._normalise_for_search(joined)

    @classmethod
    def diagnose_survivors(cls, output_pdf: Path, terms, hits: dict) -> list:
        """Measure, per surviving term, exactly where the redaction went wrong.

        Reported as numbers only: the log is an audit trail and the names are
        the thing being protected. For each survivor:

          len       length of the term
          in        occurrences search_for locates in the ORIGINAL
          out       occurrences search_for locates in the MASKED output
          seen      occurrences the verifier sees in the masked output, after
                    folding case, collapsing whitespace and joining hyphens

        out > 0        the redaction did not remove text the search could see
        out = 0, seen > 0
                       the search and the verifier disagree about what the text
                       says: whitespace, hyphenation or ligature normalisation
        """
        with pymupdf.open(output_pdf) as out_doc:
            joined = cls._normalise_haystack(
                "\n".join(page.get_text() for page in out_doc)
            )
            cache = {pg.number: cls._joined_words(pg) for pg in out_doc}
            rows = []
            for term in terms:
                candidate = (term or "").strip()
                if len(candidate) < cls.MIN_VERIFIABLE_TERM:
                    continue
                needle = cls._normalise_for_search(candidate)
                pattern = rf"(?<!\w){re.escape(needle)}(?!\w)"
                seen = len(re.findall(pattern, joined))
                if not seen:
                    continue
                rows.append({
                    "len": len(candidate),
                    "in": hits.get(term, hits.get(candidate, 0)),
                    "out": sum(
                        len(cls._locate_term(pg, candidate, entries=cache[pg.number]))
                        for pg in out_doc
                    ),
                    "seen": seen,
                    "words": len(candidate.split()),
                })
        return rows

    @classmethod
    def find_surviving_terms(cls, pdf_path: Path, terms) -> list:
        """Terms still readable in the masked document.

        Redaction locates text with page.search_for(), which misses text split
        across spans, hyphenated across lines, or oddly kerned. Nothing used to
        check the result, so "Identified 61 sensitive terms" meant 61 were
        found, not 61 were removed. This turns that assumption into a check.
        """
        with pymupdf.open(pdf_path) as doc:
            haystack = cls._normalise_haystack(
                "\n".join(page.get_text() for page in doc)
            )

        survivors = []
        for term in terms:
            candidate = (term or "").strip()
            if len(candidate) < cls.MIN_VERIFIABLE_TERM:
                continue
            needle = cls._normalise_for_search(candidate)
            # Word boundaries, so "Anna" does not match "Annandag".
            if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
                survivors.append(candidate)
        return survivors

    def mask_pdf_black_boxes(self, input_pdf: Path, output_pdf: Path, sensitive_terms, logger: Logger = None):
        try:
            sensitive_terms, too_short, too_common = self._plausible_terms(
                input_pdf, sensitive_terms
            )
            if too_short or too_common:
                logger.info(
                    f"Uteslot {len(too_short)} termer kortare an "
                    f"{self.MIN_TERM_LENGTH} tecken och {len(too_common)} termer "
                    f"som forekommer fler an {self.MAX_TERM_OCCURRENCES} ganger "
                    "(sannolikt felträffar, t.ex. leverantorsnamn i sidfoten)."
                )
                logger.debug(f"For korta: {too_short}")
                logger.debug(f"For vanliga: {too_common}")

            doc = pymupdf.open(input_pdf)
            hits = {term: 0 for term in sensitive_terms}
            stripped = 0
            for page in doc:
                stripped += self._strip_annotations(page)
                entries = self._joined_words(page)
                for term in sensitive_terms:
                    rects = self._locate_term(page, term, entries=entries)
                    hits[term] += len(rects)
                    for rect in rects:
                        page.add_redact_annot(rect, fill=(0, 0, 0))
                page.apply_redactions()

            if stripped:
                logger.info(
                    f"Tog bort {stripped} formularfalt/anteckningar vars text inte "
                    "kan svartas (t.ex. signaturrutor)."
                )

            cleared = self._clear_unmaskable_pages(doc, sensitive_terms, logger)
            if cleared:
                logger.warning(
                    f"Tomde {len(cleared)} sida/sidor vars text inte gick att "
                    f"svarta exakt: {cleared}. Sidorna innehöll känsliga namn utan "
                    "anvandbara ordrutor, typiskt signaturuppstallningar."
                )

            never_located = [t for t, n in hits.items() if n == 0]
            if never_located:
                logger.info(
                    f"{len(never_located)} av {len(sensitive_terms)} termer kunde "
                    "inte lokaliseras av sokfunktionen och har darfor inte svartats."
                )
            doc.save(output_pdf, garbage=4, deflate=True, clean=True)
            doc.close()

            survivors = self.find_surviving_terms(output_pdf, sensitive_terms)
            if survivors:
                # Log the count, not the names: this file is the audit trail
                # and the names are the very thing being protected.
                logger.error(
                    f"{len(survivors)} av {len(sensitive_terms)} känsliga termer "
                    f"finns kvar som läsbar text i {output_pdf.name} efter maskering."
                )
                for row in self.diagnose_survivors(output_pdf, sensitive_terms, hits):
                    logger.error(
                        f"    term: {row['words']} ord, {row['len']} tecken | "
                        f"sokträffar in={row['in']} ut={row['out']} | "
                        f"synlig i utdata={row['seen']}"
                    )
                logger.debug(f"Kvarvarande termer: {survivors}")
                if self.FAIL_ON_LEAK:
                    output_pdf.unlink(missing_ok=True)
                    return None
            else:
                logger.info(
                    f"Maskering verifierad: inga av {len(sensitive_terms)} termer "
                    "återfinns i utdata."
                )

            logger.info(f"Masked file saved: {output_pdf}")
            return output_pdf
        except Exception as e:
            logger.error(f"Masking failed entirely: {e}")
            # Se till att inget _masked.pdf skapas om vi misslyckas
            if output_pdf.exists():
                try:
                    output_pdf.unlink()
                except Exception:
                    pass
            return None

    def _get_pymupdf_version(self):
        return pymupdf.__version__

    @staticmethod
    def _structure_warnings(doc) -> str:
        """Return MuPDF's complaints about the document, or an empty string.

        Replaces the old `Document.check_pdf()` call, which has never existed in
        PyMuPDF. `is_repaired` tells us MuPDF had to rebuild the xref table, and
        `mupdf_warnings()` drains the warning buffer accumulated while opening.
        """
        warnings = []
        if getattr(doc, "is_repaired", False):
            warnings.append("dokumentet fick repareras vid inläsning")
        try:
            buffered = pymupdf.TOOLS.mupdf_warnings(reset=True)
        except Exception:  # pragma: no cover - very old PyMuPDF
            buffered = ""
        if buffered:
            warnings.append(buffered.replace("\n", "; "))
        return "; ".join(warnings)

    def do_masking(self, pdf_path: Path, pdf_output_path: Path = None, logger: Logger = None) -> Path:
        # Never rely on the caller passing a logger: the /mask endpoint does not.
        logger = logger or logging.getLogger(__name__)

        pdf_path = Path(pdf_path)
        if not pdf_output_path:
            pdf_output_path = pdf_path.with_name(pdf_path.stem + "_masked.pdf")
        else:
            pdf_output_path = Path(pdf_output_path)
        logger.info(f"Starting masking on: {pdf_path}")

        sanitized_path = self.sanitize_pdf(pdf_path, logger)
        try:
            with pymupdf.open(sanitized_path) as doc:
                if doc.page_count == 0:
                    logger.warning("Sanitized PDF has no pages. Skipping masking.")
                    return None
                problems = self._structure_warnings(doc)
                if problems:
                    # Non-fatal: MuPDF has already recovered what it could, so we
                    # mask anyway rather than refusing the file outright.
                    logger.warning(f"PDF structure warnings for {pdf_path.name}: {problems}")
        except Exception as e:
            logger.warning(f"Failed to open sanitized PDF for structure check: {e}")
            return None

        try:
            page_texts = self.extract_text(sanitized_path)
            sensitive_terms = self.detect_sensitive_terms(page_texts)
            logger.info(f"Identified {len(sensitive_terms)} sensitive term(s)")
            logger.debug(f"Identified sensitive terms: {sensitive_terms}")
            result_path = self.mask_pdf_black_boxes(sanitized_path, pdf_output_path, sensitive_terms, logger)
            if result_path:
                return result_path
            logger.warning("Masking failed. No output file created.")
            return None
        finally:
            # Städa temporär fil och den temporärkatalog sanitize_pdf skapade
            if sanitized_path != pdf_path:
                shutil.rmtree(sanitized_path.parent, ignore_errors=True)


def main(pdf_path_str):

    import logging

    def get_logger():
        logger = logging.getLogger("PDFMasker")
        logger.setLevel(logging.DEBUG)  # Styr nivån: DEBUG, INFO, WARNING etc.
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        ch.setFormatter(formatter)
        logger.addHandler(ch)
        return logger


    masker = PDFMasker()
    logger = get_logger()
    pdf_path = Path(pdf_path_str)
    out_path = Path(pdf_path.with_name(pdf_path.stem + "_masked.pdf"))
    out_path = masker.do_masking(pdf_path, out_path, logger)
    if out_path is None:
        logger.error("Filen kunde inte maskas")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Användning: python JBGPDFMasking.py <fil.pdf>")
    else:
        main(sys.argv[1])
