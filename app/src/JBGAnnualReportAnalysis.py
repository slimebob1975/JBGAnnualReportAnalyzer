from pathlib import Path
import zipfile
import json
from typing import List, Union
from openai import OpenAI, RateLimitError, Timeout, APIError
from app.src.JBGAnnualReportExceptions import FileTypeException 
import logging
import fitz
import tiktoken
import time
import ocrmypdf
import re
from collections.abc import Mapping

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

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
    MAX_TOKENS = 10000
    MAX_TOKEN_OVERLAP = 1000
    MAX_TOKEN_OVERLAP_REDUCTION = 200
    USE_TOKEN_OVERLAP = True
    DEFAULT_MODEL = "gpt-4o"
    DEFAULT_OPENAI_TEMPERATURE = 0.3
    DEFAULT_OPENAI_TOP_P = 1
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

    def _find_primary_year_from_pdf(self, pdf_path: Path) -> int:
        try:
            doc = fitz.open(pdf_path)
            year_counts = {}
            most_likely_year = -1
            first_openai_call = True
            page_counter = 0

            for page in doc:
                page_counter += 1
                if first_openai_call:
                    prompt = self._prompt_instructions_pdf_actual_year()
                    first_openai_call = False
                else:
                    time.sleep(self.DEFAULT_SHORT_SLEEP_TIME)

                text = page.get_text()
                response = self._make_openai_api_call(prompt, f"[Sida {page_counter}]:\n{text}")
                logger.debug(f"GPT-rådata (årtolkning):\n{response}")

                try:
                    extracted_year = int(response.strip())
                except Exception as ex:
                    logger.warning(f"Kunde inte tolka år från GPT-svar: {response} på sida {page_counter}")
                    continue

                # Ignorera specialvärden (-1, -2)
                if extracted_year < 2000:
                    continue

                year_counts[extracted_year] = year_counts.get(extracted_year, 0) + 1
                logger.debug(f"Aktuella årfrekvenser: {year_counts}")

                # Bedöm ledande år
                most_likely_year = max(year_counts, key=year_counts.get)
                dominance_rate = year_counts[most_likely_year] / sum(year_counts.values())

                logger.debug(f"Aktuellt huvudår: {most_likely_year} (andel: {round(dominance_rate,2)})")

                if dominance_rate >= self.MIN_YEAR_AGREEMENT_RATE and page_counter >= self.MIN_CHECK_YEARS:
                    logger.info(f"Bryter årtolkningsloop vid sida {page_counter} med {round(dominance_rate,2)} dominans.")
                    break

            return most_likely_year if most_likely_year > 0 else self.FALLBACK_YEAR

        except Exception as e:
            logger.warning(f"Kunde inte tolka år från {pdf_path.name}: {e}. Återgår till standardår.")
            return self.FALLBACK_YEAR

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
        
    def _merge_broken_key_number_lines(self, text: str, key_number_terms: List[str]=None) -> str:
        
        if not key_number_terms:
            key_number_terms = self._extract_key_number_term()
        
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
    
    def _extract_key_number_terms(self) -> List[str]:
        metrics = self._load_metrics(dump=False)
        key_number_terms = [metric.get(self.METRIC_KEY_NUMBER_KEY) for metric in metrics]
        for metric in metrics:
            key_number_terms = key_number_terms + [alt_metric for alt_metric in metric.get(self.METRIC_KEY_NUMBER_ALTERNATE_KEY)]
        
        return key_number_terms
    
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

    def _chunk_text_with_overlap(
        self,
        text: str,
        max_tokens: int,
        max_overlap_tokens: Union[int, float],
        model: str = "gpt-4o"
    ) -> List[str]:
        
        # Ladda rätt tokenisering beroende på modell
        enc = tiktoken.encoding_for_model(model)
        
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
    
    def _adjust_chunks_borders_for_safe_breaks(self, chunks: List[str]) -> List[str]:
        break_patterns = ["\n\n", "\nSida ", "\nNot ", ":\n", "\n\n[A-Z]", r"\. [A-ZÅÄÖ]"]

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

    def _build_system_prompt(self, the_year: int = None):
        
        instruction = self._load_instruction()
        metrics_json = self._load_metrics()
        
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

    def _make_openai_api_call(self, system_prompt, request_text: str, model: str = "") -> str:
        MODEL_TOKEN_LIMITS = {
            "gpt-4": 8192,
            "gpt-4-0613": 8192,
            "gpt-4-1106-preview": 128000,
            "gpt-4o": 128000,
            "gpt-3.5-turbo": 4096,
            "gpt-3.5-turbo-16k": 16384,
        }

        model_used = model if model else self.DEFAULT_MODEL
        max_retries = 5
        initial_delay = 1.5
        backoff_factor = 2.0
        attempt = 0

        while attempt < max_retries:
            try:
                response = self.openai_client.chat.completions.create(
                    model=model_used,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": request_text}
                    ],
                    temperature=self.DEFAULT_OPENAI_TEMPERATURE,
                    top_p=self.DEFAULT_OPENAI_TOP_P
                )

                # Tokenkontroll
                usage = getattr(response, "usage", None)
                if usage:
                    total_tokens = usage.total_tokens or 0
                    token_limit = MODEL_TOKEN_LIMITS.get(model_used, 8192)
                    if total_tokens >= token_limit:
                        logging.warning(
                            f"[GPT-tokenvarning] Modellen '{model_used}' använde {total_tokens} tokens av max {token_limit}."
                        )

                # Finish reason-koll
                finish_reason = response.choices[0].finish_reason
                if finish_reason != "stop":
                    raise RuntimeError(
                        f"GPT-svar avslutades med '{finish_reason}' – kan vara trunkerat eller felaktigt."
                    )

                logger.debug(f"GPT-response:\n{response}")
                return response.choices[0].message.content.strip()

            except (RateLimitError, Timeout, APIError) as ex:
                delay = initial_delay * (backoff_factor ** attempt)
                logger.warning(f"OpenAI API-fel (försök {attempt+1}/{max_retries}): {ex}. Försöker igen om {delay:.1f}s.")
                time.sleep(delay)
                attempt += 1
            except Exception as ex:
                logger.error(f"Allvarligt fel i OpenAI-anrop: {ex}")
                break

        raise RuntimeError("Maximalt antal försök för API-anropet överskreds.")

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
    
    def _deep_merge_json_objects(self, json_list: List[dict]) -> dict:

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
                    result[k] = v
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
                        grouped = {}
                        for v in value:
                            val = v.get(self.FIELD_VALUE, "")
                            src = v.get(self.FIELD_SOURCE, "") 
                            cert = v.get(self.FIELD_CERTAINTY, "")
                            comm = v.get(self.FIELD_COMMENT, "")
                            if val not in grouped:
                                grouped[val] = set()
                            grouped[val].update(s.strip() for s in src.replace(self.SOURCE_PREFIX, "").split(",") if s.strip())
                        for val, srcs in grouped.items():
                            consolidated[key] = {
                                self.FIELD_VALUE: val,
                                self.FIELD_SOURCE: f"{self.SOURCE_PREFIX} {', '.join(sorted(srcs))}",
                                self.FIELD_CERTAINTY: cert,
                                self.FIELD_COMMENT: comm,
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
                        if main_value:
                            main_cert = value.get(self.FIELD_CERTAINTY)
                            main_comm = value.get(self.FIELD_COMMENT)
                        else:
                            main_cert, main_comm = None, None
                        all_entries = [(key, value)] + similar_entries
                        for alt_key, alt_val in all_entries:
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

    def do_analysis(self, fil: Path, output_path: Path, model: str = "gpt-4o") -> Path:
        logger.info(f"Startar analys av fil: {fil.name}")

        if not self.upload_files:
            logger.error("No PDF files found for analysis.")
            raise ValueError("No valid PDF files found.")

        total_result = []

        first_openai_call = True
        
        # We loop over all the pdf files
        for pdf_path in self.upload_files:
            logger.info(f"Processar fil: {pdf_path}")
            
            
            # Get the current year for the analysis
            try:
                the_year = self._find_primary_year_from_pdf(pdf_path)
                logger.info(f"Extraherade aktuellt år från: {pdf_path.name} som: {the_year}")
                if the_year < 0:
                    raise RuntimeError(f"Could not extract main year from {pdf_path} to be used in system prompt.")
            except RuntimeError as ex:
                logger.warning(f"{str(ex)}. Setting year unknown.")
                the_year = None
            
            # Get the full text of the pdf
            logger.info(f"Extraherar text från: {pdf_path.name}")
            try:
                full_text = self._extract_text_from_pdf_from_pdf(pdf_path)
                #logger.debug(f"The full text for {pdf_path} is: {full_text}")
            except FileTypeException:
                logger.warning(f"Skipping file {pdf_path} since I could not extract any text from it (perhaps it was scanned?)")
                continue
            
            # Try to fix broken lines that can contain key numbers and values
            if self.FIX_BROKEN_LINES_WITH_KEY_NUMBERS:
                try:
                    full_text = self._merge_broken_key_number_lines(full_text, self._extract_key_number_terms())
                    logger.debug(f"The full text for {pdf_path} where broken lines with key numbers are merged is: {full_text}")
                except Exception as ex:
                    logger.warning(f"Could not merge broken lines with key numbers and data in for full text of file: {pdf_path}")
            
            # Divide the text into chunks with or without overlap
            if self.USE_TOKEN_OVERLAP:
                chunks = self._chunk_text_with_overlap(
                    text=full_text, max_tokens=self.MAX_TOKENS, max_overlap_tokens=self.MAX_TOKEN_OVERLAP, model=model
                    )
            else:
                chunks = self._chunk_text(full_text, max_tokens=self.MAX_TOKENS, model=model)
            logger.info(f"{len(chunks)} chunk(s) genererade för {pdf_path.name}")
            
            # Loop over the chunks
            partial_results = []
            for i, chunk in enumerate(chunks):
                
                # Build the system prompt instructions
                prompt = self._build_system_prompt(the_year=the_year)
                logger.debug(f"Prompt {i}: {prompt}")
                
                # Build the prompt request, make API call and collect results
                request = self._build_request_text(chunk)
                logger.debug(f"Request {i}: {request}")
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
                        partial_results.append(parsed)
                    except json.JSONDecodeError as e:
                        logger.error(f"Misslyckades ladda JSON: {e}")
                        continue
                except Exception as e:
                    logger.error(f"Fel vid GPT-anrop chunk {i+1}: {e}")
                    continue
            
            # Put together and clean up the result
            appended_result = self._deep_merge_json_objects(partial_results)
            logger.debug(f"In do_analysis: partial_results:")
            for result in partial_results:
                logger.debug(f"{result}")
            logger.debug(f"In do_analysis: appended_result: {appended_result}")
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

        # Write result to JSON output
        if total_result:
            final_result = self._deep_merge_json_objects(total_result)
            output_path.write_text(json.dumps(final_result, ensure_ascii=False, indent=2), encoding=self.STANDARD_ENCODING)
            logger.info(f"Analysresultat sparat till: {output_path}")
            return output_path
        else:
            logger.warning(f"Inga resultat sparades.")
            return None