"""Background jobs with per-job working directories.

The analysis takes minutes. Running it inside the request meant the browser or
any reverse proxy could time out with nothing to show for it, and every job
shared one upload directory that was wiped at the start of each request, so two
users could destroy each other's work (and could download each other's files,
which for a-kassa annual reports means each other's personal data).

Each job now gets its own directory, its own result files, and a download route
scoped to that directory. Directories are removed when the job expires.
"""

import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"

# A job's directory holds uploaded PDFs and results. Both are personal data, so
# the default lifetime is short. Override with JBG_JOB_TTL_SECONDS.
DEFAULT_TTL_SECONDS = int(os.getenv("JBG_JOB_TTL_SECONDS", "3600"))
MAX_CONCURRENT_JOBS = int(os.getenv("JBG_MAX_CONCURRENT_JOBS", "2"))
# How often the background sweeper runs. Expiry used to be checked only when a
# new job was created, so an idle service kept uploaded reports and results on
# disk indefinitely after the last run of the day.
SWEEP_INTERVAL_SECONDS = int(os.getenv("JBG_SWEEP_INTERVAL_SECONDS", "300"))


@dataclass
class Job:
    id: str
    directory: Path
    status: str = STATUS_QUEUED
    message: str = "Väntar på att starta..."
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    total_files: int = 0
    done_files: int = 0
    current_file: str = ""
    output_name: str | None = None
    error: str | None = None

    @property
    def duration(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at if self.finished_at is not None else time.time()
        return max(0.0, end - self.started_at)

    def as_dict(self) -> dict:
        payload = {
            "job_id": self.id,
            "status": self.status,
            "message": self.message,
            "total_files": self.total_files,
            "done_files": self.done_files,
            "current_file": self.current_file,
            "duration_seconds": round(self.duration, 1),
        }
        if self.status == STATUS_DONE:
            payload["download_url"] = f"/api/jobs/{self.id}/download"
            payload["download_filename"] = self.output_name
        if self.error:
            payload["error"] = self.error
        return payload


class JobRegistry:
    """In-process job store.

    Deliberately in-process and not persisted: results are short-lived
    personal data and there is no benefit to surviving a restart. If this ever
    runs behind more than one worker, this needs to move to shared storage.
    """

    def __init__(
        self,
        root: Path | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        sweep_interval: int = SWEEP_INTERVAL_SECONDS,
    ):
        self.root = Path(root or os.getenv("JBG_JOB_DIR") or (Path(tempfile.gettempdir()) / "jbg-jobs"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_JOBS, thread_name_prefix="jbg-job"
        )
        self._stop_sweeper = threading.Event()
        self._sweeper = None
        logger.info(f"Jobbkatalog: {self.root} (livslängd {self.ttl_seconds}s)")
        if sweep_interval > 0:
            self.start_sweeper(sweep_interval)

    # --------------------------------------------------------------- sweeper
    def start_sweeper(self, interval: int) -> None:
        """Run purge_expired on a timer, not only when a job is created."""

        def loop():
            while not self._stop_sweeper.wait(interval):
                try:
                    self.purge_expired()
                except Exception as ex:  # pragma: no cover - must never die
                    logger.warning(f"Städning av jobbkatalogen misslyckades: {ex}")

        self._sweeper = threading.Thread(
            target=loop, name="jbg-job-sweeper", daemon=True
        )
        self._sweeper.start()
        logger.info(f"Städning av gamla jobb var {interval}s.")

    # ------------------------------------------------------------------ jobs
    def create(self) -> Job:
        self.purge_expired()
        job_id = uuid.uuid4().hex
        directory = self.root / job_id
        directory.mkdir(parents=True, exist_ok=False)
        job = Job(id=job_id, directory=directory)
        with self._lock:
            self._jobs[job_id] = job
        logger.info(f"Skapade jobb {job_id}")
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def submit(self, job: Job, work: Callable[[Job], str]) -> None:
        """Queue `work` for the job. It must return the output filename."""

        def runner():
            job.status = STATUS_RUNNING
            job.started_at = time.time()
            job.message = "Analysen har startat..."
            try:
                job.output_name = work(job)
                job.finished_at = time.time()
                job.status = STATUS_DONE
                job.message = self._completion_message(job)
                logger.info(
                    f"Jobb {job.id} klart på {self.format_duration(job.duration)} "
                    f"({job.total_files} fil(er)) -> {job.output_name}"
                )
            except Exception as ex:
                logger.exception(f"Jobb {job.id} misslyckades")
                job.status = STATUS_ERROR
                job.error = str(ex)
                job.message = f"Analysen misslyckades: {ex}"
            finally:
                job.finished_at = time.time()

        self._pool.submit(runner)

    @staticmethod
    def format_duration(seconds: float) -> str:
        total = int(round(seconds))
        return f"{total // 60}:{total % 60:02d}"

    @classmethod
    def _completion_message(cls, job: Job) -> str:
        elapsed = cls.format_duration(job.duration)
        count = job.total_files or job.done_files
        if count > 1:
            per_file = job.duration / count
            return (
                f"{count} fil(er) analyserade på {elapsed} "
                f"({per_file:.0f} s/fil). Nedladdningen startar automatiskt."
            )
        return f"{count} fil(er) analyserade på {elapsed}. Nedladdningen startar automatiskt."

    def progress_callback(self, job: Job) -> Callable[[int, int, str], None]:
        def report(done: int, total: int, filename: str):
            job.done_files = done
            job.total_files = total
            job.current_file = filename
            if done < total:
                job.message = f"Analyserar fil {done + 1} av {total}: {filename}"
            else:
                job.message = "Sammanställer resultatet..."

        return report

    # --------------------------------------------------------------- cleanup
    def resolve_output(self, job: Job, name: str) -> Path:
        """Resolve a filename inside the job directory, refusing escapes."""
        candidate = (job.directory / name).resolve()
        if not candidate.is_relative_to(job.directory.resolve()):
            raise ValueError(f"Sökvägen ligger utanför jobbkatalogen: {name}")
        return candidate

    def purge_expired(self) -> int:
        cutoff = time.time() - self.ttl_seconds
        removed = 0
        with self._lock:
            expired = [j for j in self._jobs.values() if j.created_at < cutoff]
            for job in expired:
                self._jobs.pop(job.id, None)
        for job in expired:
            shutil.rmtree(job.directory, ignore_errors=True)
            removed += 1

        # Also sweep directories with no job record, left by a previous process.
        for directory in self.root.iterdir() if self.root.exists() else []:
            if not directory.is_dir():
                continue
            with self._lock:
                if directory.name in self._jobs:
                    continue
            try:
                if directory.stat().st_mtime < cutoff:
                    shutil.rmtree(directory, ignore_errors=True)
                    removed += 1
            except OSError:
                continue

        if removed:
            logger.info(f"Rensade {removed} utgångna jobbkatalog(er).")
        return removed

    def shutdown(self) -> None:
        self._stop_sweeper.set()
        self._pool.shutdown(wait=False, cancel_futures=True)


def purge_old_logs(log_dir: Path, max_age_days: int = None, keep_at_least: int = 5) -> int:
    """Delete log files older than the retention window.

    Logs contain extracted report text at DEBUG level, so they are personal
    data too and cannot accumulate indefinitely.
    """
    max_age_days = max_age_days if max_age_days is not None else int(
        os.getenv("JBG_LOG_RETENTION_DAYS", "14")
    )
    if not log_dir.exists() or max_age_days <= 0:
        return 0

    files = sorted(log_dir.glob("app_*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for old in files[keep_at_least:]:
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info(f"Tog bort {removed} loggfil(er) äldre än {max_age_days} dagar.")
    return removed
