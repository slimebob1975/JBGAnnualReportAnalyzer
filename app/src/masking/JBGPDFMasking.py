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

    def _make_masking_rectangle(self, quad, fixed_height=10.0):
        mid_y = (quad.rect.y0 + quad.rect.y1) / 2
        return pymupdf.Rect(quad.rect.x0, mid_y - fixed_height / 2, quad.rect.x1, mid_y + fixed_height / 2)

    def mask_pdf_black_boxes(self, input_pdf: Path, output_pdf: Path, sensitive_terms, logger: Logger = None):
        try:
            doc = pymupdf.open(input_pdf)
            for page in doc:
                for term in sensitive_terms:
                    quads = page.search_for(term, quads=True)
                    for quad in quads:
                        rect = self._make_masking_rectangle(quad)
                        page.add_redact_annot(rect, fill=(0, 0, 0))
                page.apply_redactions()
            doc.save(output_pdf, garbage=4, deflate=True, clean=True)
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
