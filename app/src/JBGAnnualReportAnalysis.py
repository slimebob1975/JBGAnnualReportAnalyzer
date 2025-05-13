from pathlib import Path
import zipfile
import json
from typing import List, Union
from openai import OpenAI
from app.src.JBGFileTypeException import FileTypeException 
import logging
import fitz
import tiktoken
import time
import ocrmypdf

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class JBGAnnualReportAnalyzer:
    FIELD_VALUE = "värde"
    FIELD_SOURCE = "källa"
    SOURCE_PREFIX = "sid."
    STANDARD_ENCODING = "utf-8"
    PAGE_OFFSET = 0
    OFFSET_LIMIT = 99
    MIN_CHECK_OFFSETS = 5
    MIN_OFFSET_AGREEMENT_RATE = 0.8
    MAX_TOKENS = 4000
    DEFAULT_MODEL = "gpt-4o"
    DEFAULT_SHORT_SLEEP_TIME = 1
    DEFAULT_LONG_SLEEP_TIME = 5
    TEXT_GAIN_FOR_OCR_CONVERSION = 1.5
    
    def __init__(
        self,
        upload_dir: Union[str, Path, List[Union[str, Path]]],
        instruction_path: Union[str, Path],
        metrics_path: Union[str, Path]
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
        self.openai_client = OpenAI()

    def _extract_zip(self, zip_path: Path) -> List[Path]:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(self.upload_dir)
        return [f for f in self.upload_dir.glob("*.pdf")]

    def _find_page_number_offset(self, pdf_path: Path) -> int:
        try:
            doc = fitz.open(pdf_path)
            i = 0
            page_offset = -1
            offsets = {}
            first_openai_call = True
            for page in doc:
                i += 1
                if first_openai_call:
                    prompt = self._prompt_instructions_pdf_page_offset()
                    first_openai_call = False
                else:
                    time.sleep(self.DEFAULT_SHORT_SLEEP_TIME)
                response = self._make_openai_api_call(prompt, f"[Sida {i}]:\n" + page.get_text())
                logger.debug(f"GPT-rådata:\n{response}")
                try:
                    new_offset = int(response.strip())
                except Exception as ex:
                    logger.warning(f"Could not extract page_offset from response: {response} on page {i}")
                    continue
                else:
                    if abs(new_offset) > self.OFFSET_LIMIT:
                        continue
                    offsets[new_offset] = offsets.get(new_offset, 0) + 1
                    logger.debug(f"Calculated offsets: {offsets}")
                    page_offset = max(offsets, key=offsets.get)
                    page_offset_rate = float(max(offsets.values())) / float(sum(offsets.values())) 
                    logger.debug(f"Current offset: {page_offset} with agreement rate: {page_offset_rate}")
                    if page_offset_rate >= self.MIN_OFFSET_AGREEMENT_RATE and i >= self.MIN_CHECK_OFFSETS:
                        logger.info(f"Breaking offset calculation loop at {i}th iteration with {round(page_offset_rate,2)} rate")
                        logger.info(f"Final page numbering offset is {page_offset}")
                        break
            return page_offset
        except Exception as e:
            logger.warning(f"Could not extract pdf page number offset from {pdf_path.name}: {e}. Using standard value.")
            return self.PAGE_OFFSET

    def _extract_text_from_pdf_from_pdf(self, pdf_path: Path) -> str:

        try:
            original_doc = fitz.open(pdf_path)
            original_text = self._extract_text_from_pdf(original_doc, max(self._find_page_number_offset(pdf_path), 0)).strip()
            original_len = len(original_text)

            logger.info(f"Original text length: {original_len}")

            # Run OCR unconditionally
            ocr_path = pdf_path.with_name(f"{pdf_path.stem}_ocr.pdf")
            try:
                ocrmypdf.ocr(
                    input_file=str(pdf_path),
                    output_file=str(ocr_path),
                    language='swe',
                    deskew=True
                )
                ocr_doc = fitz.open(ocr_path)
                ocr_text = self._extract_text_from_pdf(ocr_doc, max(self._find_page_number_offset(ocr_path), 0)).strip()
                ocr_len = len(ocr_text)
                logger.info(f"OCR text length: {ocr_len}")

                # Use OCR if text gain is significant (e.g. 50% more)
                if ocr_len > original_len * self.TEXT_GAIN_FOR_OCR_CONVERSION:
                    logger.info(f"Using OCR-enhanced version of {pdf_path.name}")
                    return ocr_text
                else:
                    logger.info(f"OCR did not significantly improve content. Using original.")
                    return original_text
            except Exception as ocr_err:
                logger.warning(f"OCR failed for {pdf_path.name}: {ocr_err}")
                return original_text
        except Exception as e:
            logger.warning(f"Text extraction failed for {pdf_path.name}: {e}")
            return ""

    def _document_contains_retreivable_text(self, doc) -> bool:
        for page in doc:
            if page.get_text().strip():  # strip whitespace to be safe
                return True
        return False

    def _extract_text_from_pdf(self, doc, offset):
            return "\n\n".join([f"[Sida {page.number - offset}]\n" + page.get_text() for page in doc])
    
    def _count_tokens(self, text: str, model: str = "gpt-4o") -> int:
        enc = tiktoken.encoding_for_model(model)
        return len(enc.encode(text))

    def _chunk_text(self, text: str, max_tokens: int, model: str) -> List[str]:
        words = text.split()
        chunks, current_chunk = [], []
        token_count = 0
        for word in words:
            token_count += self._count_tokens(word + " ", model)
            current_chunk.append(word)
            if token_count >= max_tokens:
                chunks.append(" ".join(current_chunk))
                current_chunk = []
                token_count = 0
        if current_chunk:
            chunks.append(" ".join(current_chunk))
        return chunks

    def _load_instruction(self) -> str:
        return self.instruction_path.read_text(encoding=self.STANDARD_ENCODING)

    def _load_metrics(self) -> str:
        metrics = json.loads(self.metrics_path.read_text(encoding=self.STANDARD_ENCODING))
        return json.dumps(metrics, ensure_ascii=False, indent=2)

    def _build_request_text(self, extracted_text: str) -> str:
       
        request_text = f"""
            Analysera följande årsredovisningsutdrag:
            ----------------
            {extracted_text}
            ----------------
            Returnera endast en giltig JSON-struktur enligt instruktionerna – ingen annan text.
        """
        return request_text

    def _build_system_prompt(self):
        
        instruction = self._load_instruction()
        metrics_json = self._load_metrics()
        system_prompt = f"""
            {instruction}
            -------------
            Följande key_numbers ska extraheras:
            -------------
            {metrics_json}
        """
        return system_prompt
    
    def _make_openai_api_call(self, system_prompt, request_text: str, model: str = "") -> dict:
        
        logger.debug(f"Aktuell system-prompt är {self._count_tokens(system_prompt)} tokens")
        logger.debug(f"Aktuell request_text är {self._count_tokens(request_text)} tokens")
        
        try:
            response = self.openai_client.chat.completions.create(
                model=model if model else self.DEFAULT_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": request_text}
                ],
                temperature=0.5
            )
        except Exception as ex:
            logger.error(f"Fel i anrop till OpenAI. GPT-response:\n{response}")
            return ""
        else:
            logger.debug(f"GPT-response:\n{response}")
            return response.choices[0].message.content.strip()

    def _clean_presumed_prefixed_json(self, presumed_prefixed_json):
        if presumed_prefixed_json.startswith("```json"):
            presumed_prefixed_json = presumed_prefixed_json.removeprefix("```json").strip()
        if presumed_prefixed_json.endswith("```"):
            presumed_prefixed_json = presumed_prefixed_json.removesuffix("```").strip()
        return presumed_prefixed_json
    
    def _merge_json_objects(self, json_list: List[dict]) -> dict:
        
        result = {}
        for obj in json_list:
            for key, value in obj.items():
                if key not in result:
                    result[key] = value
                else:
                    result[key].update(value if isinstance(value, dict) else {f"extra_{key}": value})
        
        return result
    
    def _merge_json_fund_data(self, data):
                
        # Check if what we get is really is a non-empty dictionary
        if not (isinstance(data, dict) and bool(data)):
            return None, None
        
        # Choose the longest name assuming it's the most descriptive
        preferred_name = max(data.keys(), key=len)
        
        # Build a new JSON structure with only one name
        merged = {preferred_name: {}}
        
        conflicts = []

        for fund_name, year_data in data.items():
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
        
        return merged, conflicts
    
    def _merge_conflicted_values_json_objects(self, json_obj: dict) -> tuple[dict, int]:
        
        num_consolidated = 0
        for fund, year_data in json_obj.items():
            for year, metrics in year_data.items():
                consolidated = {}
                for key, value in metrics.items():
                    if key in consolidated:
                        continue
                    # Nytt: Hantera listor med dictar
                    if isinstance(value, list) and all(isinstance(v, dict) for v in value):
                        grouped = {}
                        for v in value:
                            val = v.get(self.FIELD_VALUE, "")
                            src = v.get(self.FIELD_SOURCE, "") 
                            if val not in grouped:
                                grouped[val] = set()
                            grouped[val].update(s.strip() for s in src.replace(self.SOURCE_PREFIX, "").split(",") if s.strip())
                        for val, srcs in grouped.items():
                            consolidated[key] = {
                                self.FIELD_VALUE: val,
                                self.FIELD_SOURCE: f"{self.SOURCE_PREFIX} {', '.join(sorted(srcs))}"
                            }
                            if len(srcs) > 1:
                                num_consolidated += len(srcs) - 1
                    else:
                        similar_entries = [
                            (alt_key, alt_value)
                            for alt_key, alt_value in metrics.items()
                            if alt_key != key and alt_key.startswith(key)
                        ]
                        main_value = value.get(self.FIELD_VALUE) if isinstance(value, dict) else None
                        sources = set()
                        all_entries = [(key, value)] + similar_entries
                        for alt_key, alt_val in all_entries:
                            if isinstance(alt_val, dict) and alt_val.get(self.FIELD_VALUE) == main_value:
                                source = alt_val.get(self.FIELD_SOURCE, "")
                                sources.update(s.strip() for s in source.replace(self.SOURCE_PREFIX, "").split(","))
                        if sources and main_value is not None:
                            consolidated[key] = {
                                self.FIELD_VALUE: main_value,
                                self.FIELD_SOURCE: f"{self.SOURCE_PREFIX} {', '.join(sorted(sources))}"
                            }
                            num_consolidated += len(sources) - 1
                        else:
                            consolidated[key] = value
                json_obj[fund][year] = consolidated
        
        return json_obj, num_consolidated
    
    def _is_valid_numeric(self, val) -> bool:
        if isinstance(val, (int, float)):
            return True
        if isinstance(val, str):
            try:
                float(val.replace(" ", "").replace("kr", "").replace(",", "."))
                return True
            except ValueError:
                return False
        return False

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

    def do_analysis(self, fil: Path, output_path: Path, model: str = "gpt-4o") -> Path:
        logger.info(f"Startar analys av fil: {fil.name}")

        if not self.upload_files:
            logger.error("No PDF files found for analysis.")
            raise ValueError("No valid PDF files found.")

        total_result = []

        first_openai_call = True
        for pdf_path in self.upload_files:
            logger.info(f"Extraherar text från: {pdf_path.name}")
            try:
                full_text = self._extract_text_from_pdf_from_pdf(pdf_path)
            except FileTypeException:
                logger.warning(f"Skipping file {pdf_path} since I could not extract any text from it (perhaps it was scanned?)")
                continue
            chunks = self._chunk_text(full_text, max_tokens=self.MAX_TOKENS, model=model)
            logger.info(f"{len(chunks)} chunk(s) genererade för {pdf_path.name}")
            partial_result = []
            for i, chunk in enumerate(chunks):
                prompt = self._build_system_prompt()
                request = self._build_request_text(chunk)
                try:
                    if first_openai_call:
                        first_openai_call = False
                    else:
                        time.sleep(self.DEFAULT_LONG_SLEEP_TIME)
                    logger.info(f"Skickar chunk {i+1}/{len(chunks)} till GPT...")
                    response = self._make_openai_api_call(prompt, request, model)
                    logger.debug(f"GPT-rådata:\n{response}")
                    response = self._clean_presumed_prefixed_json(response)
                    if not response.strip().startswith("{"):
                        logger.warning("GPT-svar är inte giltig JSON – hoppar över.")
                        continue
                    try:
                        parsed = json.loads(response)
                        partial_result.append(parsed)
                    except json.JSONDecodeError as e:
                        logger.error(f"Misslyckades ladda JSON: {e}")
                        continue
                except Exception as e:
                    logger.error(f"Fel vid GPT-anrop chunk {i+1}: {e}")
                    continue
            appended_result = self._merge_json_objects(partial_result)
            if appended_result:
                appended_result, conflicts = self._merge_json_fund_data(appended_result)
                if conflicts:
                    logger.warning(f"Last merge of JSON data resulted in {len(conflicts)} conflicts: {conflicts}")
                    appended_result, num_merged_values = self._merge_conflicted_values_json_objects(appended_result)
                    if num_merged_values > 0:
                        logger.info(f"Merged {num_merged_values} duplicate values in appended JSON structure")
                    else:
                        logger.warning(f"No conclicts were merged.")
                total_result.append(appended_result)

        if total_result:
            final_result = self._merge_json_objects(total_result)
            output_path.write_text(json.dumps(final_result, ensure_ascii=False, indent=2), encoding=self.STANDARD_ENCODING)
            logger.info(f"Analysresultat sparat till: {output_path}")
            return output_path
        else:
            logger.warning(f"Inga resultat sparades.")
            return None