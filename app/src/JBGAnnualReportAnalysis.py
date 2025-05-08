from pathlib import Path
import zipfile
import json
from typing import List, Union
from openai import OpenAI
import shutil
import logging
import fitz

logger = logging.getLogger(__name__)

class JBGAnnualReportAnalyzer:
    def __init__(self, upload_dir: Union[str, Path], instruction_path: Union[str, Path], metrics_path: Union[str, Path]):
        self.upload_dir = Path(upload_dir)
        self.instruction_path = Path(instruction_path)
        self.metrics_path = Path(metrics_path)
        self.openai_client = OpenAI()  # requires OPENAI_API_KEY

    def _extract_zip(self, zip_path: Path) -> List[Path]:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(self.upload_dir)
        return [f for f in self.upload_dir.glob("*.pdf")]

    def _load_instruction(self) -> str:
        return self.instruction_path.read_text(encoding="utf-8")

    def _load_metrics(self) -> str:
        metrics = json.loads(self.metrics_path.read_text(encoding="utf-8"))
        return json.dumps(metrics, ensure_ascii=False, indent=2)

    def _extract_text_from_pdf(self, pdf_path: Path) -> str:
        try:
            doc = fitz.open(pdf_path)
            return "\n\n".join([f"[Sida {i+1}]\n" + page.get_text() for i, page in enumerate(doc)])
        except Exception as e:
            logger.warning(f"Kunde inte extrahera text från {pdf_path.name}: {e}")
            return ""
    
    def _build_prompt(self, pdf_filenames: List[str]) -> str:
        instruction = self._load_instruction()
        metrics_json = self._load_metrics()

        # Extrahera text för varje PDF
        extracted_texts = []
        for filename in pdf_filenames:
            path = self.upload_dir / filename
            extracted = self._extract_text_from_pdf(path)
            if extracted:
                extracted_texts.append(f"---\nInnehåll från {filename}:\n{extracted}")

        dokumenttext = "\n\n".join(extracted_texts)

        prompt = f"""{instruction}
            Följande nyckeltal ska extraheras:
            {metrics_json}

            Analysera följande årsredovisningar:

            {dokumenttext}

            Returnera svaret som en JSON-struktur enligt instruktionerna.
        """
        return prompt

    def do_analysis(self, fil: Path, output_path: Path, the_model: str = "gtp-4o") -> Path:
        logger.info(f"Startar analys av fil: {fil.name}")
        logger.info(f"Modell som används: {the_model}")

        if fil.suffix.lower() == ".zip":
            logger.info("Extraherar ZIP...")
            pdf_files = self._extract_zip(fil)
            logger.info(f"{len(pdf_files)} PDF-filer extraherade.")
        elif fil.suffix.lower() == ".pdf":
            target_path = self.upload_dir / fil.name
            if fil.resolve() != target_path.resolve():
                shutil.copy(fil, target_path)
            pdf_files = [target_path]
            logger.info("PDF klar för analys.")
        else:
            logger.error("Ogiltig filtyp.")
            raise ValueError("Endast .pdf eller .zip accepteras")

        logger.info(f"Bygger prompt för {len(pdf_files)} fil(er)...")
        prompt = self._build_prompt([f.name for f in pdf_files])
        logger.info("Skickar prompt till OpenAI...")

        try:
            response = self.openai_client.chat.completions.create(
                model = the_model,
                messages = [
                    {"role": "system", "content": "Du är en expert på ekonomisk rapportanalys."},
                    {"role": "user", "content": prompt}
                ],
                temperature = 0.5
            )
            logger.info("Svar mottaget från GPT.")
        except Exception as e:
            logger.error(f"Fel vid GPT-anrop: {e}")
            raise

        result_json = response.choices[0].message.content.strip()
        output_path.write_text(result_json, encoding="utf-8")
        logger.info(f"Analysresultat sparat till: {output_path}")
        return output_path