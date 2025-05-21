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
from app.src.JBGAnnualReportExceptions import FileTypeException, EmptyOutputException
from app.src.JBGJSONConverter import JsonConverter
from app.src.JBGPDFMasking import PDFMasker
from openai import OpenAI
import logging
from datetime import datetime

app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
TITLE = "JBG nyckeltalsanalys"
SUBTITLE = "Obs! För .PDF (eller .ZIP av .PDF)"
TITLE_MASKING = "JBG filmaskning"
SUBTITLE_MASKING = "Obs! För .PDF"
INVALID_FILETYPE_FOR = "Ogiltig filtyp för"
FILES_ALLOWED = "Endast pdf eller zip av pdf tillåtes"
USE_COMPRESSED_GPT = True

# Loggning
LOG_DIR = BASE_DIR / "log"
LOG_DIR.mkdir(exist_ok=True)
timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
LOG_FILE = LOG_DIR / f"app_{timestamp}.log"

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

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "title": TITLE, 
        "subtitle": SUBTITLE, 
        "title_masking": TITLE_MASKING, 
        "subtitle_masking": SUBTITLE_MASKING, 
        "message": "",
        "active_tab": "analysis"
    })

@app.post("/upload", response_class=HTMLResponse)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form(...),
    apikey: str = Form(...),
    format: str = Form(...),
    sources: str = Form(...),
    use_masking: str = Form(...)
):
    if use_masking == "yes":
        logger.info("Will use masking in each pdf to analyze...")
    elif use_masking == "no":
        logger.info("Will not use masking...")
    else:
        raise Exception(f"Illegal values of masking parameter: {masking}")
    if sources == "yes":
        logger.info("Will include sources in final output!")
    elif sources == "no":
        logger.info("Souces will be excluded from final output!")
    else:
        raise Exception(f"Illegal value of checkbox sources: {str(sources)}. Reason: {str(ex)}")
    
    filename = file.filename
    file_ext = filename.lower().split(".")[-1]

    for f in UPLOAD_DIR.glob("*"):
        if f.is_file():
            f.unlink()
        elif f.is_dir():
            shutil.rmtree(f)

    saved_path = UPLOAD_DIR / filename
    with saved_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        extracted_files = []
        
        # If a zip file, examine the directory structure (if any) such that only PDF:s are found
        if file_ext == "zip":
            with zipfile.ZipFile(saved_path, 'r') as zip_ref:
                # List all files in the archive, including paths
                zip_members = [info for info in zip_ref.infolist() if not info.is_dir()]
                
                # Validate all files are PDFs
                for member in zip_members:
                    if not member.filename.lower().endswith(".pdf"):
                        raise FileTypeException(
                            message=f"{INVALID_FILETYPE_FOR}: {member.filename}. {FILES_ALLOWED}."
                        )
                
                # Extract all valid files
                zip_ref.extractall(UPLOAD_DIR)
                extracted_files = [member.filename for member in zip_members]
        
        # Single PDF
        elif file_ext == "pdf":
            extracted_files = [filename]
            
        # all other cases
        else:
            raise FileTypeException(
                message=f"{INVALID_FILETYPE_FOR}: {filename}. {FILES_ALLOWED}."
            )

        os.environ["OPENAI_API_KEY"] = apikey
        analys = JBGAnnualReportAnalyzer(
            upload_dir=UPLOAD_DIR,
            instruction_path=\
                BASE_DIR / "prompt" / "GPT-instruktioner.md" if not USE_COMPRESSED_GPT else \
                    BASE_DIR / "prompt" / "GPT-instruktioner_komprimerad.md",
            metrics_path=BASE_DIR / "prompt" / "json" / "nyckeltalsdefinitioner.json",
            use_masking = (use_masking == "yes")
        )
        analys.openai_client = OpenAI(api_key=apikey)

        json_output_path = UPLOAD_DIR / f"{Path(filename).stem}_resultat.json"
        
        # Do analysis and take care of result
        analys_result_path = analys.do_analysis(json_output_path, model=model)
        if analys_result_path:
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
                "title_masking": TITLE_MASKING, 
                "subtitle_masking": SUBTITLE_MASKING, 
                "message": f"{len(extracted_files)} fil(er) analyserade.",
                "resultat": json.dumps(resultat_json, indent=2, ensure_ascii=False),
                "download_filename": download_filename
            })
        else:
            logger.warning(f"Inget resultat")
            raise EmptyOutputException(message="Ingen fil verkar ha analyserats")

    except FileTypeException as ex:
        logger.warning(f"Fel filtyp: {ex.message}")
        return templates.TemplateResponse("index.html", {
            "request": request,
            "title": TITLE,
            "subtitle": SUBTITLE,
            "title_masking": TITLE_MASKING, 
            "subtitle_masking": SUBTITLE_MASKING, 
            "message": f"{ex.message}"
        })

    except EmptyOutputException as e:
        logger.error(f"Ingen fil verkar ha analyserats: {str(e)}")
        return templates.TemplateResponse("index.html", {
            "request": request,
            "title": TITLE,
            "subtitle": SUBTITLE,
            "title_masking": TITLE_MASKING, 
            "subtitle_masking": SUBTITLE_MASKING, 
            "message": f"Ett fel uppstod vid nyckeltalsanalysen: {str(e)}"
        })
    
    except Exception as e:
        logger.error(f"Ett fel uppstod vid nyckeltalsanalysen: {str(e)}")
        raise(e)
        return templates.TemplateResponse("index.html", {
            "request": request,
            "title": TITLE,
            "subtitle": SUBTITLE,
            "title_masking": TITLE_MASKING, 
            "subtitle_masking": SUBTITLE_MASKING, 
            "message": f"Fel vid analys: {str(e)}"
        })

@app.get("/download/{filename}", response_class=FileResponse)
async def download_file(filename: str):
    file_path = UPLOAD_DIR / filename
    if file_path.exists():
        return FileResponse(path=file_path, filename=filename, media_type='application/octet-stream')
    return {"error": "Filen finns inte"}

@app.post("/mask", response_class=HTMLResponse)
async def mask_only(
    request: Request,
    file: UploadFile = File(...)
):
    try:
        # Spara fil
        filename = file.filename
        saved_path = UPLOAD_DIR / filename
        with saved_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Kör maskering
        masker = PDFMasker()
        masked_output = saved_path.with_name(saved_path.stem + "_masked.pdf")
        masked_output = Path(masker.do_masking(Path(saved_path), Path(masked_output)))

        return templates.TemplateResponse("index.html", {
            "request": request,
            "title": TITLE,
            "subtitle": SUBTITLE,
            "title_masking": TITLE_MASKING, 
            "subtitle_masking": SUBTITLE_MASKING, 
            "message": f"Filen '{filename}' maskerad.",
            "masked_filename": masked_output.name,
            "active_tab": "masking"
        })

    except Exception as e:
        logger.error(f"Fel vid maskering: {e}")
        return templates.TemplateResponse("index.html", {
            "request": request,
            "title": TITLE,
            "subtitle": SUBTITLE,
            "title_masking": TITLE_MASKING, 
            "subtitle_masking": SUBTITLE_MASKING, 
            "message": f"Fel vid maskering: {str(e)}",
            "active_tab": "masking"
        })
