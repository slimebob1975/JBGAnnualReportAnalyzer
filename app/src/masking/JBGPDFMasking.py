import fitz  # PyMuPDF
import re
from transformers import pipeline
import sys
from pathlib import Path
from logging import Logger
import tempfile

class PDFMasker:
    def __init__(self):
        self.ner = pipeline("ner", model="KBLab/bert-base-swedish-cased-ner", tokenizer="KBLab/bert-base-swedish-cased-ner", grouped_entities=True)

    def sanitize_pdf(self, input_pdf: Path, logger: Logger = None) -> Path:
        temp_dir = tempfile.mkdtemp()
        sanitized_path = Path(temp_dir) / f"{input_pdf.stem}_sanitized.pdf"
        try:
            doc = fitz.open(input_pdf)
            doc.save(sanitized_path, garbage=4, deflate=True, clean=True)
            if logger:
                logger.info(f"Sanitized PDF saved to: {sanitized_path}")
            return sanitized_path
        except Exception as e:
            if logger:
                logger.warning(f"Sanitizing failed, proceeding with original file: {e}")
            return input_pdf

    def extract_text(self, pdf_path):
        doc = fitz.open(pdf_path)
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
    def _get_extra_names():
        names = {"Jeanette", "Mathias"}
        surnames = {"Cervin"}
        return names, surnames

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
        return fitz.Rect(quad.rect.x0, mid_y - fixed_height / 2, quad.rect.x1, mid_y + fixed_height / 2)

    def mask_pdf_black_boxes(self, input_pdf: Path, output_pdf: Path, sensitive_terms, logger: Logger = None):
        try:
            doc = fitz.open(input_pdf)
            for page in doc:
                for term in sensitive_terms:
                    quads = page.search_for(term, quads=True)
                    for quad in quads:
                        rect = self._make_masking_rectangle(quad)
                        page.add_redact_annot(rect, fill=(0, 0, 0))
                page.apply_redactions()
            doc.save(output_pdf, garbage=4, deflate=True, clean=True)
            if logger: logger.info(f"Masked file saved: {output_pdf}")
            return output_pdf
        except Exception as e:
            if logger: logger.error(f"Masking failed entirely: {e}")
            # Se till att inget _masked.pdf skapas om vi misslyckas
            if output_pdf.exists():
                try:
                    output_pdf.unlink()
                except Exception:
                    pass
            return None

    def _has_check_pdf(self):
        return hasattr(fitz.Document, "check_pdf")
    
    def _get_pymupdf_version(self):
        return fitz.__version__

    def do_masking(self, pdf_path: Path, pdf_output_path: Path = None, logger: Logger = None) -> Path:
        pdf_path = Path(pdf_path)
        if not pdf_output_path:
            pdf_output_path = pdf_path.with_name(pdf_path.stem + "_masked.pdf")
        else:
            pdf_output_path = Path(pdf_output_path)
        if logger:
            logger.info(f"Starting masking on: {pdf_path}")

        sanitized_path = self.sanitize_pdf(pdf_path, logger)
        try:
            doc = fitz.open(sanitized_path)
            if self._has_check_pdf():
                if doc.check_pdf() != 0:
                    if logger:
                        logger.warning(f"Sanitized PDF still has structure problems. Skipping masking.")
                    return None
            else:
                version = self._get_pymupdf_version()
                logger.warning(f"PyMuPDF version {version} lacks check_pdf(). Skipping structure validation.")
        except Exception as e:
            if logger:
                logger.warning(f"Failed to open sanitized PDF for structure check: {e}")
            return None

        try:
            page_texts = self.extract_text(sanitized_path)
            sensitive_terms = self.detect_sensitive_terms(page_texts)
            if logger:
                logger.info(f"Identified sensitive terms: {sensitive_terms}")
            result_path = self.mask_pdf_black_boxes(sanitized_path, pdf_output_path, sensitive_terms, logger)
            if result_path:
                return result_path
            else:
                if logger:
                    logger.warning(f"Masking failed. No output file created.")
                return None
        finally:
            # Städa temporär fil
            if sanitized_path != pdf_path and sanitized_path.exists():
                try:
                    sanitized_path.unlink()
                except:
                    pass


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
        logger.error(f"Filen kunde inte maskas")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Användning: python JBGPDFMasking.py <fil.pdf>")
    else:
        main(sys.argv[1])
