import fitz  # PyMuPDF
import re
from transformers import pipeline
import sys
from pathlib import Path
from logging import Logger

class PDFMasker:
    def __init__(self):
        self.ner = pipeline("ner", model="KBLab/bert-base-swedish-cased-ner", tokenizer="KBLab/bert-base-swedish-cased-ner", grouped_entities=True)

    def extract_text(self, pdf_path):
        doc = fitz.open(pdf_path)
        return [page.get_text() for page in doc]

    def _clean_entities(self, entities):
        cleaned = []
        for word in entities:
            if re.match(r"^[#@]", word):
                continue
            if word.lower() in {"and", "do", "gr", "vice", "statens", "is", "revis", "aukt", "kass", "Led"}:
                continue
            
            # Fixa extra mellanslag runt bindestreck
            updated_word = self._normalize_hyphens(word.strip())
            
            # Ta bort dubbla fraser
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
        email_matches = set(re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", full_text))
        dob_matches = set(re.findall(r"\bDOB:\s*(?:19|20)\d{2}/\d{2}/\d{2}\b", full_text))

        all_terms = sensitive_words.union(pnr_matches, email_matches, dob_matches)
        return self._clean_entities(all_terms)

    @staticmethod
    def _deduplicate_if_mirrored_with_space(s: str) -> str:
        s = s.strip()
        if len(s) < 5 or len(s) % 2 == 0:
            return s  # måste vara udda antal tecken
        mid = len(s) // 2
        if s[mid] != " ":
            return s  # måste ha exakt ett mellanslag i mitten
        left = s[:mid].strip().lower()
        right = s[mid + 1:].strip().lower()
        if left == right:
            return s[:mid].strip()
        return s

    
    @staticmethod
    def _normalize_hyphens(text: str) -> str:
        # Ta bort mellanslag runt bindestreck
        return re.sub(r"\s*-\s*", "-", text)
    
    def _make_masking_rectangle(self, quad, fixed_height=10.0):
        mid_y = (quad.rect.y0 + quad.rect.y1) / 2
        return fitz.Rect(quad.rect.x0, mid_y - fixed_height / 2, quad.rect.x1, mid_y + fixed_height / 2)

    def mask_pdf_black_boxes(self, input_pdf, output_pdf, sensitive_terms):
        doc = fitz.open(input_pdf)
        for page in doc:
            for term in sensitive_terms:
                quads = page.search_for(term, quads=True)
                for quad in quads:
                    rect = self._make_masking_rectangle(quad)
                    page.add_redact_annot(rect, fill=(0, 0, 0))
            page.apply_redactions()
        doc.save(output_pdf)
        
    def do_masking(self, pdf_path: Path, pdf_output_path: Path = None, logger: Logger = print):
    
        if not pdf_output_path:
            pdf_output_path = pdf_path.with_name(pdf_path.stem + "_masked.pdf")
        
        logger.info(f"Performing masking on file: {pdf_path}")
    
        page_texts = self.extract_text(pdf_path=pdf_path)
        
        sensitive_terms = self.detect_sensitive_terms(page_texts=page_texts)
        logger.info(f"Identified sensitive terms: {sensitive_terms}. Masking...")
        
        self.mask_pdf_black_boxes(pdf_path, pdf_output_path, sensitive_terms)
        logger.info(f"Masked file saved to path: {pdf_output_path}")
        return pdf_output_path


def main(pdf_path_str):
    masker = PDFMasker()
    pdf_path = Path(pdf_path_str)
    out_path = pdf_path.with_name(pdf_path.stem + "_maskerad.pdf")

    print(f"Läser: {pdf_path.name}")
    page_texts = masker.extract_text(pdf_path)
    terms = masker.detect_sensitive_terms(page_texts)
    print(f"Identifierade känsliga termer: {terms}")
    
    masker.mask_pdf_black_boxes(str(pdf_path), str(out_path), terms)
        
    print(f"Maskerad PDF sparad till: {out_path}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Användning: python test_maskering.py <fil.pdf>")
    else:
        main(sys.argv[1])