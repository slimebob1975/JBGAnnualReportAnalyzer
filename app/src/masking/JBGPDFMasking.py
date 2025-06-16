import fitz  # PyMuPDF
import re
from transformers import pipeline
import sys
from pathlib import Path
from logging import Logger
import os

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
            if word.lower() in {"and", "do", "gr", "vice", "statens", "is", "revis", "aukt", "kass", "Led", "lem", "mar", "sum", "id",\
                "supp", "cer", "föret", "kassach", "gransk", "general", "sek", "arb"}:
                continue
            if word in {"Signerat", "jan", "kassa", "signe", "dista", "manuell", "Sek", "General"}:
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
        
        # Personnummer
        pnr_matches = set(re.findall(r"\b\d{6}[-+]\d{4}\b", full_text))
        
        # Epost
        full_text = self._fix_split_emails(full_text)
        email_matches = set(re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", full_text))
        
        # Twitter-konton
        twitter_matches = set(re.findall(r"@[A-Za-z0-9_]{1,15}", full_text))
        
        # Födelsedatum vid signering
        dob_matches = set(re.findall(r"\bDOB:\s*(?:19|20)\d{2}/\d{2}/\d{2}\b", full_text))
        
        # Extra namn som inte BERT-modellen identifierar
        extra_fornamn, extra_efternamn = self._get_extra_names()

        all_terms = sensitive_words.union(pnr_matches, email_matches, twitter_matches, dob_matches, extra_fornamn, extra_efternamn)
        return self._clean_entities(all_terms)

    @staticmethod
    def _fix_split_emails(text: str) -> str:
        # Sätt ihop mejladresser där hostnamn eller domän brutits
        return re.sub(r'(@[^\s@]*)[\r\n]+([^\s@]+\.[a-z]{2,10})', r'\1\2', text, flags=re.IGNORECASE)
    
    @staticmethod
    def _get_extra_names():
        names =   {"Jeanette", "Mathias"}
        surnames = {"Cervin"}
        return names, surnames
    
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

    def mask_pdf_black_boxes(self, input_pdf : Path, output_pdf : Path, sensitive_terms, logger: Logger=None):
        if logger:
            logger.debug(f"Calling mask_pdf_black_boxes with inputs: {input_pdf}, {output_pdf}, {sensitive_terms}")
        try:
            doc = fitz.open(input_pdf)
            i = 0
            for page in doc:
                i += 1
                for term in sensitive_terms:
                    quads = page.search_for(term, quads=True)
                    for quad in quads:
                        rect = self._make_masking_rectangle(quad)
                        page.add_redact_annot(rect, fill=(0, 0, 0))
                page.apply_redactions()
            if logger: logger.debug(f"Saving masked files...")
            if doc.needs_pass:
                raise Exception("Filen är krypterad eller behöver lösenord.")
            try:
                doc.save(output_pdf, garbage=4, deflate=True)
            except fitz.mupdf.FzErrorFormat as ex:
                logger.warning(f"Masking failed for {input_pdf} in call to save. Reason: {str(ex)}. File not masked.")
                if os.path.exists(output_pdf):
                    os.remove(output_pdf)
                    logger.warning(f"Erased corrupted PDF: {output_pdf}")
                return None
        except Exception as ex:
            if logger: logger.error(f"mask_pdf_black_boxes failed: {str(ex)}")
        return output_pdf
        
    def do_masking(self, pdf_path: Path, pdf_output_path: Path = None, logger: Logger = None)-> Path:
    
        pdf_path = str(pdf_path)
        if not pdf_output_path:
            pdf_output_path = pdf_path.with_name(pdf_path.stem + "_masked.pdf")
        else:
            pdf_output_path = str(pdf_output_path)
        
        if logger:
            logger.info(f"Performing masking on file: {pdf_path}")
        else:
            print(f"Performing masking on file: {pdf_path}")
    
        page_texts = self.extract_text(pdf_path=pdf_path)
        
        sensitive_terms = self.detect_sensitive_terms(page_texts=page_texts)
        if logger:
            logger.info(f"Identified sensitive terms: {sensitive_terms}. Masking...")
        else:
            print(f"Identified sensitive terms: {sensitive_terms}. Masking...")
        
        pdf_output_path = self.mask_pdf_black_boxes(pdf_path, pdf_output_path, sensitive_terms, logger)
        if pdf_output_path:
            if logger:
                logger.info(f"Masked file saved to path: {pdf_output_path}")
            else:
                print(f"Masked file saved to path: {pdf_output_path}")
            return pdf_output_path    
        else:
            if logger: 
                logger.info(f"Masking failed. Returning original file: {pdf_path}")
            else:
                print(f"Masking failed. Returning original file: {pdf_path}")
            return pdf_path

def main(pdf_path_str):
    masker = PDFMasker()
    pdf_path = Path(pdf_path_str)
    out_path = Path(pdf_path.with_name(pdf_path.stem + "_masked.pdf"))
    
    out_path = masker.do_masking(pdf_path, out_path)
        
if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Användning: python JBGPDFMasking.py <fil.pdf>")
    else:
        main(sys.argv[1])