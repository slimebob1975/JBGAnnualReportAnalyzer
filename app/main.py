from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import shutil
import zipfile
import json
from typing import Optional
import os
from app.src.JBGAnnualReportAnalysis import JBGAnnualReportAnalyzer
from app.src.JBGJSONConverter import JsonConverter
from openai import OpenAI
import logging

app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
TITLE = "JBG nyckeltalsanalys"
SUBTITLE = "Obs! För .PDF (eller .ZIP av .PDF)"
INVALID_FILETYPE_FOR = "Ogiltig filtyp för"
FILES_ALLOWED = "Endast pdf eller zip av pdf tillåtes"

# Loggning
LOG_DIR = BASE_DIR / "log"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

class FileTypeException(BaseException):
    def __init__(self, message="Ogiltig filtyp"):
        self.message = message
        super().__init__(self.message)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "title": TITLE, 
        "subtitle": SUBTITLE, 
        "message": ""
    })

@app.post("/upload", response_class=HTMLResponse)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form(...),
    apikey: str = Form(...),
    format: str = Form(...),
    sources: str = Form(...)
):
    if sources == "yes":
        logger.info("Will include sources in final output!")
    elif sources == "no":
        logger.info("Souces will be excluded from final output!")
    else:
        raise Exception(f"Illegal value of checkbox sources: {str(sources)}. Reason: {str(ex)}")
    
    filename = file.filename
    file_ext = filename.lower().split(".")[-1]

    for f in UPLOAD_DIR.glob("*"):
        f.unlink()

    saved_path = UPLOAD_DIR / filename
    with saved_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        extracted_files = []
        if file_ext == "zip":
            with zipfile.ZipFile(saved_path, 'r') as zip_ref:
                zip_ref.extractall(UPLOAD_DIR)
                extracted_files = list(zip_ref.namelist())
                for extracted_filename in extracted_files:
                    if extracted_filename.lower().split(".")[-1] != "pdf":
                        raise FileTypeException(
                            message=f"{INVALID_FILETYPE_FOR}: {extracted_filename}. {FILES_ALLOWED}."
                        )
        elif file_ext == "pdf":
            extracted_files = [filename]
        else:
            raise FileTypeException(
                message=f"{INVALID_FILETYPE_FOR}: {filename}. {FILES_ALLOWED}."
            )

        os.environ["OPENAI_API_KEY"] = apikey
        analys = JBGAnnualReportAnalyzer(
            upload_dir=UPLOAD_DIR,
            instruction_path=BASE_DIR / "prompt" / "GPT-instruktioner.md",
            metrics_path=BASE_DIR / "prompt" / "json" / "nyckeltalsdefinitioner.json"
        )
        analys.openai_client = OpenAI(api_key=apikey)

        json_output_path = UPLOAD_DIR / f"{Path(filename).stem}_resultat.json"
        analys_result_path = analys.do_analysis(saved_path, json_output_path, model=model)
        resultat_json = json.loads(analys_result_path.read_text(encoding="utf-8"))
        
        converter = JsonConverter(json_output_path, include_sources=(sources == "yes"))

        if format == "csv":
            output_path = UPLOAD_DIR / f"{Path(filename).stem}_resultat.csv"
            converter.to_csv(output_path)

        elif format == "xlsx":
            output_path = UPLOAD_DIR / f"{Path(filename).stem}_resultat_by_fund.xlsx"
            converter.to_excel_by_year(
                output_path, 
                key_def_path=BASE_DIR / "prompt" / "json" / "nyckeltalsdefinitioner.json",
                fund_names=BASE_DIR / "src" / "json" / "kassor.json"
            )

        elif format == "json":
            # Already written by `do_analysis` → no action needed
            output_path = json_output_path

        else:
            raise ValueError("Ogiltigt format valt.")
        
        download_filename = output_path.name

        return templates.TemplateResponse("index.html", {
            "request": request,
            "title": TITLE,
            "subtitle": SUBTITLE,
            "message": f"{len(extracted_files)} fil(er) analyserade.",
            "resultat": json.dumps(resultat_json, indent=2, ensure_ascii=False),
            "download_filename": download_filename
        })

    except FileTypeException as ex:
        logger.warning(f"Fel filtyp: {ex.message}")
        return templates.TemplateResponse("index.html", {
            "request": request,
            "title": TITLE,
            "subtitle": SUBTITLE,
            "message": f"{ex.message}"
        })

    except Exception as e:
        logger.error(f"Fel vid analys: {str(e)}")
        raise(e)
        return templates.TemplateResponse("index.html", {
            "request": request,
            "title": TITLE,
            "subtitle": SUBTITLE,
            "message": f"Fel vid analys: {str(e)}"
        })

@app.get("/download/{filename}", response_class=FileResponse)
async def download_file(filename: str):
    file_path = UPLOAD_DIR / filename
    if file_path.exists():
        return FileResponse(path=file_path, filename=filename, media_type='application/octet-stream')
    return {"error": "Filen finns inte"}