import logging
import os
import shutil
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.src.JBGAnnualReportAnalysis import JBGAnnualReportAnalyzer
from app.src.JBGAnnualReportExceptions import EmptyOutputException, FileTypeException
from app.src.JBGJobs import STATUS_DONE, STATUS_ERROR, JobRegistry, purge_old_logs
from app.src.JBGJSONConverter import JsonConverter
from app.src.masking.JBGPDFMasking import PDFMasker

BASE_DIR = Path(__file__).resolve().parent
TITLE = "JBG nyckeltalsanalys"
SUBTITLE = "Obs! För .PDF (eller .ZIP av .PDF)"
TITLE_MASKING = "JBG filmaskning"
SUBTITLE_MASKING = "Obs! För .PDF"
INVALID_FILETYPE_FOR = "Ogiltig filtyp för"
FILES_ALLOWED = "Endast pdf eller zip av pdf tillåtes"
USE_COMPRESSED_GPT = True

# Refuse absurd uploads before writing them to disk.
MAX_UPLOAD_BYTES = int(os.getenv("JBG_MAX_UPLOAD_MB", "200")) * 1024 * 1024

# Loggning
LOG_DIR = BASE_DIR / "log"
LOG_DIR.mkdir(exist_ok=True)
timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
LOG_FILE = LOG_DIR / f"app_{timestamp}.log"

LOG_LEVEL = os.getenv("JBG_LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# Third-party libraries log one INFO line per HTTP request, which buried the
# application's own messages. Raise their threshold.
# ocrmypdf emits an empty ERROR record alongside the exception we already
# catch and log ourselves, which produced ten blank "[ERROR]" lines in a run.
logging.getLogger("ocrmypdf").setLevel(logging.CRITICAL)

# ocrmypdf pulls in fontTools, pikepdf and img2pdf, which together emit
# several hundred INFO lines per OCR-ed document (every glyph name, twice).
for noisy in ("httpx", "httpcore", "openai", "urllib3", "filelock",
              "transformers", "huggingface_hub", "PIL",
              "fontTools", "fontTools.subset", "fontTools.ttLib",
              "pikepdf", "img2pdf", "pdfminer"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
logger.info(f"Loggnivå: {LOG_LEVEL} (styrs av miljövariabeln JBG_LOG_LEVEL)")

purge_old_logs(LOG_DIR)

# One working directory per job, removed when the job expires. Replaces the
# single shared uploads folder that every request wiped on arrival.
jobs = JobRegistry()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    jobs.shutdown()


app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def render_page(request: Request, active_tab: str = "analysis", **extra):
    """Render index.html with the constant page chrome always filled in."""
    context = {
        "request": request,
        "title": TITLE,
        "subtitle": SUBTITLE,
        "title_masking": TITLE_MASKING,
        "subtitle_masking": SUBTITLE_MASKING,
        "message": "",
        "active_tab": active_tab,
    }
    context.update(extra)
    # Starlette 0.29+ takes the request first; passing it inside the context
    # dict is deprecated and now raises on newer versions.
    return templates.TemplateResponse(request, "index.html", context)


@app.get("/health")
def health():
    """Readiness probe for the container healthcheck.

    Reports whether the optional components are actually available, so a
    misconfigured image is visible before a user uploads anything rather than
    as a warning buried in the log.
    """
    try:
        # Both are needed: transformers imports without a backend and only
        # fails when a pipeline is actually built.
        import torch  # noqa: F401
        from transformers import pipeline  # noqa: F401

        masking_available = True
        masking_detail = "transformers och torch hittades"
    except ImportError as ex:
        masking_available = False
        masking_detail = f"{ex.name} saknas. Installera med: pip install '.[masking]'"

    from app.src.JBGAnnualReportAnalysis import ocr_availability

    ocr_available, ocr_reason = ocr_availability()

    return {
        "status": "ok",
        "masking_available": masking_available,
        "masking_detail": masking_detail,
        "ocr_available": ocr_available,
        "ocr_detail": ocr_reason,
        "job_dir_writable": os.access(jobs.root, os.W_OK),
        "active_jobs": len(
            [j for j in jobs._jobs.values() if j.status in ("queued", "running")]
        ),
    }


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return render_page(request)

def _save_upload(file: UploadFile, target_dir: Path) -> Path:
    """Stream an upload into the job directory, enforcing the size cap.

    The file is written in chunks and abandoned as soon as the cap is passed,
    so an oversized upload cannot fill the disk before being rejected.
    """
    name = Path(file.filename or "upload").name  # strip any client-supplied path
    if not name:
        raise FileTypeException(message="Filen saknar namn.")

    destination = target_dir / name
    written = 0
    with destination.open("wb") as buffer:
        while True:
            block = file.file.read(1024 * 1024)
            if not block:
                break
            written += len(block)
            if written > MAX_UPLOAD_BYTES:
                buffer.close()
                destination.unlink(missing_ok=True)
                raise FileTypeException(
                    message=f"Filen är för stor. Max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
                )
            buffer.write(block)
    return destination


def _prepare_job_input(file: UploadFile, job_dir: Path) -> tuple[Path, int]:
    """Save the upload and expand it, returning (saved_path, pdf_count)."""
    saved_path = _save_upload(file, job_dir)
    file_ext = saved_path.name.lower().rsplit(".", 1)[-1]

    if file_ext == "zip":
        with zipfile.ZipFile(saved_path, "r") as zip_ref:
            members = [info for info in zip_ref.infolist() if not info.is_dir()]
            for member in members:
                if not member.filename.lower().endswith(".pdf"):
                    raise FileTypeException(
                        message=f"{INVALID_FILETYPE_FOR}: {member.filename}. {FILES_ALLOWED}."
                    )
            total = sum(info.file_size for info in members)
            if total > MAX_UPLOAD_BYTES:
                raise FileTypeException(
                    message="Zip-filens innehåll är för stort att packa upp."
                )
            zip_ref.extractall(job_dir)
            return saved_path, len(members)

    if file_ext == "pdf":
        return saved_path, 1

    raise FileTypeException(
        message=f"{INVALID_FILETYPE_FOR}: {saved_path.name}. {FILES_ALLOWED}."
    )


def _validate_options(format: str, sources: str, use_masking: str) -> None:
    if use_masking not in ("yes", "no"):
        raise HTTPException(
            status_code=400,
            detail=f"Ogiltigt värde på parametern use_masking: {use_masking!r}",
        )
    if sources not in ("yes", "no"):
        raise HTTPException(
            status_code=400, detail=f"Ogiltigt värde på kryssrutan sources: {sources!r}"
        )
    if format not in ("json", "csv", "xlsx"):
        raise HTTPException(status_code=400, detail=f"Ogiltigt format valt: {format!r}")


def _run_analysis(
    job_dir: Path,
    stem: str,
    model: str,
    apikey: str,
    format: str,
    sources: str,
    use_masking: str,
    progress_callback=None,
) -> str:
    """Analyse the PDFs in job_dir and return the output filename."""
    logger.info(
        "Will use masking in each pdf to analyze..."
        if use_masking == "yes"
        else "Will not use masking..."
    )
    logger.info(
        "Will include sources in final output!"
        if sources == "yes"
        else "Sources will be excluded from final output!"
    )

    # The key is passed to the client directly. Assigning it to os.environ
    # mutated process-global state from a form field, so concurrent requests
    # could overwrite each other's key.
    analys = JBGAnnualReportAnalyzer(
        upload_dir=job_dir,
        instruction_path=(
            BASE_DIR / "prompt" / "GPT-instruktioner_komprimerad.md"
            if USE_COMPRESSED_GPT
            else BASE_DIR / "prompt" / "GPT-instruktioner.md"
        ),
        metrics_path=BASE_DIR / "prompt" / "json" / "nyckeltalsdefinitioner.json",
        use_masking=(use_masking == "yes"),
        api_key=apikey,
        fund_list_path=BASE_DIR / "src" / "json" / "kassor.json",
    )

    json_output_path = job_dir / f"{stem}_resultat.json"
    analys_result_path = analys.do_analysis(
        json_output_path, model=model, progress_callback=progress_callback
    )

    if not analys_result_path:
        logger.warning("Inget resultat")
        raise EmptyOutputException(message="Ingen fil verkar ha analyserats")

    converter = JsonConverter(json_output_path, include_sources=(sources == "yes"))

    if format == "csv":
        output_path = job_dir / f"{stem}_resultat.csv"
        converter.to_csv(output_path)
    elif format == "xlsx":
        output_path = job_dir / f"{stem}_resultat_by_fund.xlsx"
        converter.to_excel_by_year(
            output_path,
            key_def_path=BASE_DIR / "prompt" / "json" / "nyckeltalsdefinitioner.json",
            fund_names=BASE_DIR / "src" / "json" / "kassor.json",
            findings=analys.validation_findings,
        )
    else:  # json, already written by do_analysis
        output_path = json_output_path

    if analys.skipped_files:
        # Surface this in the browser, not only in the log.
        names = ", ".join(name for name, _ in analys.skipped_files)
        logger.warning(f"Filer som hoppades över: {names}")

    return output_path.name, len(analys.skipped_files)


@app.post("/api/analyze")
def api_analyze(
    file: UploadFile = File(...),
    model: str = Form(...),
    apikey: str = Form(...),
    format: str = Form(...),
    sources: str = Form(...),
    use_masking: str = Form(...),
):
    """Accept the upload, queue the work, and return a job id immediately.

    The analysis runs for minutes. Doing it inside the request risked a
    gateway timeout with nothing to show, and blocked the event loop for
    every other request while it ran.
    """
    _validate_options(format, sources, use_masking)

    job = jobs.create()
    try:
        saved_path, pdf_count = _prepare_job_input(file, job.directory)
    except FileTypeException as ex:
        shutil.rmtree(job.directory, ignore_errors=True)
        logger.warning(f"Fel filtyp: {ex.message}")
        return JSONResponse(status_code=400, content={"ok": False, "message": ex.message})
    except zipfile.BadZipFile:
        shutil.rmtree(job.directory, ignore_errors=True)
        return JSONResponse(
            status_code=400, content={"ok": False, "message": "Zip-filen kunde inte läsas."}
        )

    job.total_files = pdf_count
    stem = Path(saved_path).stem

    def work(j):
        output_name, skipped = _run_analysis(
            j.directory,
            stem,
            model,
            apikey,
            format,
            sources,
            use_masking,
            progress_callback=jobs.progress_callback(j),
        )
        j.skipped_files = skipped
        return output_name

    jobs.submit(job, work)

    return {"ok": True, **job.as_dict()}


@app.get("/api/jobs/{job_id}")
def api_job_status(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Jobbet finns inte eller har gått ut.")
    return {"ok": job.status != STATUS_ERROR, **job.as_dict()}


@app.get("/api/jobs/{job_id}/download", response_class=FileResponse)
def api_job_download(job_id: str):
    """Serve a result file, scoped to its own job.

    Replaces /download/{filename}, which served anything in a shared folder to
    anyone who could guess a name.
    """
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Jobbet finns inte eller har gått ut.")
    if job.status != STATUS_DONE or not job.output_name:
        raise HTTPException(status_code=409, detail="Jobbet är inte klart.")

    try:
        path = jobs.resolve_output(job, job.output_name)
    except ValueError as ex:
        raise HTTPException(status_code=400, detail="Ogiltigt filnamn.") from ex
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Filen finns inte.")

    return FileResponse(
        path=path, filename=job.output_name, media_type="application/octet-stream"
    )


@app.post("/upload", response_class=HTMLResponse)
def upload_file(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form(...),
    apikey: str = Form(...),
    format: str = Form(...),
    sources: str = Form(...),
    use_masking: str = Form(...),
):
    """Synchronous form fallback for browsers without JavaScript."""
    _validate_options(format, sources, use_masking)
    job = jobs.create()
    try:
        saved_path, pdf_count = _prepare_job_input(file, job.directory)
        job.total_files = pdf_count
        job.started_at = time.time()
        job.output_name, _ = _run_analysis(
            job.directory,
            Path(saved_path).stem,
            model,
            apikey,
            format,
            sources,
            use_masking,
        )
        job.finished_at = time.time()
        job.status = STATUS_DONE
    except FileTypeException as ex:
        logger.warning(f"Fel filtyp: {ex.message}")
        return render_page(request, message=ex.message)
    except EmptyOutputException as e:
        logger.error(f"Ingen fil verkar ha analyserats: {e}")
        return render_page(request, message=f"Ett fel uppstod vid nyckeltalsanalysen: {e}")
    except Exception as e:
        logger.exception("Ett fel uppstod vid nyckeltalsanalysen")
        return render_page(request, message=f"Fel vid analys: {e}")

    return render_page(
        request,
        message=f"{pdf_count} fil(er) analyserade på {jobs.format_duration(job.duration)}.",
        download_url=f"/api/jobs/{job.id}/download",
        download_filename=job.output_name,
    )


def _mask_saved_file(saved_path: Path) -> str:
    """Mask one already-saved PDF and return the output filename."""
    if saved_path.suffix.lower() != ".pdf":
        raise FileTypeException(
            message=f"{INVALID_FILETYPE_FOR}: {saved_path.name}. Endast pdf tillåtes."
        )

    masker = PDFMasker()
    masked_output = saved_path.with_name(saved_path.stem + "_masked.pdf")
    result = masker.do_masking(saved_path, masked_output, logger=logger)
    if result is None:
        # do_masking signals failure with None; Path(None) used to raise a
        # TypeError that surfaced to the user as an unrelated error message.
        raise EmptyOutputException(
            message=f"Maskeringen av '{saved_path.name}' misslyckades. Se loggen för detaljer."
        )
    return Path(result).name


@app.post("/api/mask")
def api_mask(file: UploadFile = File(...)):
    job = jobs.create()
    try:
        saved = _save_upload(file, job.directory)
    except FileTypeException as ex:
        shutil.rmtree(job.directory, ignore_errors=True)
        return JSONResponse(status_code=400, content={"ok": False, "message": ex.message})
    # The upload stream must be consumed inside the request, so the file is
    # saved here and the worker only does the masking.
    job.total_files = 1

    def work(j):
        name = _mask_saved_file(saved)
        j.done_files = 1
        return name

    jobs.submit(job, work)
    return {"ok": True, **job.as_dict()}


@app.post("/mask", response_class=HTMLResponse)
def mask_only(request: Request, file: UploadFile = File(...)):
    """Synchronous form fallback for browsers without JavaScript."""
    job = jobs.create()
    try:
        job.started_at = time.time()
        job.output_name = _mask_saved_file(_save_upload(file, job.directory))
        job.finished_at = time.time()
        job.status = STATUS_DONE
    except (FileTypeException, EmptyOutputException) as ex:
        return render_page(request, active_tab="masking", message=ex.message)
    except Exception as e:
        logger.exception("Fel vid maskering")
        return render_page(request, active_tab="masking", message=f"Fel vid maskering: {e}")

    return render_page(
        request,
        active_tab="masking",
        message=f"Filen '{file.filename}' maskerad.",
        download_url=f"/api/jobs/{job.id}/download",
        download_filename=job.output_name,
    )
