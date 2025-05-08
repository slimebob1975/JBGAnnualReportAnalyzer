from pathlib import Path
import zipfile
import json
from typing import List, Union
from openai import OpenAI
import shutil

class JBGAnnualReportAnalyzer:
    def __init__(self, upload_dir: Union[str, Path], instruction_path: Union[str, Path], metrics_path: Union[str, Path]):
        self.upload_dir = Path(upload_dir)
        self.instruction_path = Path(instruction_path)
        self.metrics_path = Path(metrics_path)
        self.openai_client = OpenAI()  # requires OPENAI_API_KEY environment variable

    def _extract_zip(self, zip_path: Path) -> List[Path]:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(self.upload_dir)
        return [f for f in self.upload_dir.glob("*.pdf")]

    def _load_instruction(self) -> str:
        return self.instruction_path.read_text(encoding="utf-8")

    def _load_metrics(self) -> str:
        metrics = json.loads(self.metrics_path.read_text(encoding="utf-8"))
        return json.dumps(metrics, ensure_ascii=False, indent=2)

    def _build_prompt(self, pdf_filenames: List[str]) -> str:
        instruction = self._load_instruction()
        metrics_json = self._load_metrics()
        prompt = f"""
            {instruction}

            Följande nyckeltal ska extraheras:
            {metrics_json}

            Analysera följande PDF-filer: {", ".join(pdf_filenames)}.

            Returnera svaret som en JSON-struktur enligt instruktionerna.
        """
        return prompt

    def do_analysis(self, fil: Path, output_path: Path) -> Path:
        if fil.suffix.lower() == ".zip":
            pdf_files = self._extract_zip(fil)
        elif fil.suffix.lower() == ".pdf":
            target_path = self.upload_dir / fil.name
            shutil.copy(fil, target_path)
            pdf_files = [target_path]
        else:
            raise ValueError("Endast .pdf eller .zip accepteras")

        prompt = self._build_prompt([f.name for f in pdf_files])

        response = self.openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Du är en expert på ekonomisk rapportanalys."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )

        result_json = response.choices[0].message.content.strip()
        output_path.write_text(result_json, encoding="utf-8")
        return output_path