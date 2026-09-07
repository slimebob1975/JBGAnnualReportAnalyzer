"""Tests for Step 3: per-job isolation, scoped downloads, retention limits
and the polling flow that drives the automatic download."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.src.JBGJobs import (  # noqa: E402
    STATUS_DONE,
    STATUS_ERROR,
    JobRegistry,
    purge_old_logs,
)


@pytest.fixture
def registry(tmp_path):
    reg = JobRegistry(root=tmp_path / "jobs", ttl_seconds=3600)
    yield reg
    reg.shutdown()


def _wait(job, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if job.status in (STATUS_DONE, STATUS_ERROR):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job stuck in status {job.status}")


# -------------------------------------------------------------- isolation
def test_each_job_gets_its_own_directory(registry):
    a, b = registry.create(), registry.create()
    assert a.directory != b.directory
    assert a.directory.is_dir() and b.directory.is_dir()


def test_one_job_cannot_disturb_another(registry):
    """The old code wiped a single shared uploads folder at the start of every
    request, so a second upload destroyed the first one's files."""
    a, b = registry.create(), registry.create()
    (a.directory / "rapport.pdf").write_bytes(b"A")

    registry.create()  # a third job, which used to be the destructive step
    assert (a.directory / "rapport.pdf").read_bytes() == b"A"
    assert not (b.directory / "rapport.pdf").exists()


# ----------------------------------------------------- scoped downloads
def test_download_path_must_stay_inside_the_job_directory(registry):
    job = registry.create()
    (job.directory / "ok.json").write_text("{}", encoding="utf-8")
    assert registry.resolve_output(job, "ok.json").is_file()

    for escape in ["../../etc/passwd", "../other/result.json", "/etc/passwd"]:
        with pytest.raises(ValueError):
            registry.resolve_output(job, escape)


def test_another_jobs_file_is_not_reachable(registry):
    victim, attacker = registry.create(), registry.create()
    (victim.directory / "hemligt.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        registry.resolve_output(attacker, f"../{victim.id}/hemligt.json")


# ------------------------------------------------------------ retention
def test_expired_jobs_and_their_files_are_removed(tmp_path):
    reg = JobRegistry(root=tmp_path / "jobs", ttl_seconds=0)
    try:
        job = reg.create()
        (job.directory / "personuppgifter.pdf").write_bytes(b"x")
        directory = job.directory
        time.sleep(0.01)

        reg.purge_expired()
        assert reg.get(job.id) is None
        assert not directory.exists()
    finally:
        reg.shutdown()


def test_orphaned_directories_from_a_previous_process_are_swept(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    orphan = root / "deadbeef"
    orphan.mkdir()
    (orphan / "leftover.pdf").write_bytes(b"x")

    reg = JobRegistry(root=root, ttl_seconds=0)
    try:
        time.sleep(0.01)
        reg.purge_expired()
        assert not orphan.exists()
    finally:
        reg.shutdown()


def test_log_retention_keeps_the_most_recent_files(tmp_path):
    for i in range(9):
        f = tmp_path / f"app_2026-01-0{i}_00-00-00.log"
        f.write_text("x", encoding="utf-8")
        # everything except the newest five is well past the window
        import os
        age = 0 if i >= 4 else 40 * 86400
        os.utime(f, (time.time() - age, time.time() - age))

    purge_old_logs(tmp_path, max_age_days=14, keep_at_least=5)
    remaining = sorted(f.name for f in tmp_path.glob("app_*.log"))
    assert len(remaining) == 5, remaining


# ------------------------------------------------------- job lifecycle
def test_completion_message_reports_total_and_average(registry):
    job = registry.create()
    job.total_files = 7
    job.started_at = time.time() - 251  # 4:11, as in the sample run
    job.finished_at = time.time()

    message = JobRegistry._completion_message(job)
    assert "7 fil(er) analyserade på 4:11" in message
    assert "36 s/fil" in message


def test_single_file_message_omits_the_average(registry):
    job = registry.create()
    job.total_files = 1
    job.started_at = time.time() - 30
    job.finished_at = time.time()
    message = JobRegistry._completion_message(job)
    assert "s/fil" not in message
    assert "0:30" in message


def test_progress_callback_updates_the_job(registry):
    job = registry.create()
    report = registry.progress_callback(job)

    report(0, 3, "a.pdf")
    assert job.total_files == 3 and job.done_files == 0
    assert "1 av 3" in job.message

    report(3, 3, "c.pdf")
    assert job.done_files == 3
    assert "Sammanställer" in job.message


def test_failed_work_marks_the_job_and_keeps_the_reason(registry):
    job = registry.create()
    registry.submit(job, lambda j: (_ for _ in ()).throw(RuntimeError("API-nyckeln är ogiltig")))
    _wait(job)
    assert job.status == STATUS_ERROR
    assert "API-nyckeln är ogiltig" in job.message


def test_successful_work_exposes_a_scoped_download_url(registry):
    job = registry.create()

    def work(j):
        (j.directory / "resultat.json").write_text("{}", encoding="utf-8")
        j.done_files = j.total_files = 2
        return "resultat.json"

    registry.submit(job, work)
    _wait(job)
    assert job.status == STATUS_DONE
    payload = job.as_dict()
    assert payload["download_url"] == f"/api/jobs/{job.id}/download"
    assert payload["download_filename"] == "resultat.json"


# ---------------------------------------------------------- HTTP surface
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JBG_JOB_DIR", str(tmp_path / "httpjobs"))
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    import importlib

    import app.main as main

    importlib.reload(main)
    with fastapi_testclient.TestClient(main.app) as c:
        yield c, main


def test_page_has_no_result_box_and_no_download_button(client):
    c, _ = client
    body = c.get("/").text
    assert "data-role=\"result\"" not in body
    assert "download-link" not in body
    assert "data-role=\"elapsed\"" in body


def test_analyze_returns_a_job_id_immediately(client, monkeypatch):
    c, main = client
    monkeypatch.setattr(main, "_run_analysis", lambda *a, **k: "r.json")

    pdf = b"%PDF-1.4\n%%EOF\n"
    r = c.post(
        "/api/analyze",
        data={"model": "gpt-5.2", "apikey": "sk-x", "format": "json",
              "sources": "yes", "use_masking": "no"},
        files={"file": ("r.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["job_id"]
    assert body["status"] in ("queued", "running", "done")


def test_unknown_job_is_404_not_500(client):
    c, _ = client
    assert c.get("/api/jobs/doesnotexist").status_code == 404
    assert c.get("/api/jobs/doesnotexist/download").status_code == 404


def test_download_refused_before_the_job_finishes(client, monkeypatch):
    c, main = client
    job = main.jobs.create()
    assert c.get(f"/api/jobs/{job.id}/download").status_code == 409


def test_oversized_upload_is_rejected(client, monkeypatch):
    c, main = client
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1024)
    r = c.post(
        "/api/analyze",
        data={"model": "gpt-5.2", "apikey": "sk-x", "format": "json",
              "sources": "yes", "use_masking": "no"},
        files={"file": ("big.pdf", b"x" * 5000, "application/pdf")},
    )
    assert r.status_code == 400
    assert "för stor" in r.json()["message"]


def test_upload_filename_cannot_escape_the_job_directory(client, monkeypatch):
    """A client-supplied name like ../../evil.pdf must be reduced to a basename."""
    c, main = client
    job = main.jobs.create()

    class FakeUpload:
        filename = "../../evil.pdf"

        def __init__(self):
            import io
            self.file = io.BytesIO(b"%PDF-1.4\n")

    saved = main._save_upload(FakeUpload(), job.directory)
    assert saved.parent == job.directory
    assert saved.name == "evil.pdf"


# ---------------------------------------------------------------- packaging
def test_health_endpoint_reports_optional_components(client):
    """The container healthcheck hits this, and it must say whether masking
    and OCR are actually installed rather than only that the port is open."""
    c, _ = client
    r = c.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert set(body) >= {
        "status", "masking_available", "ocr_available", "job_dir_writable", "active_jobs"
    }
    assert body["job_dir_writable"] is True


def test_masking_without_transformers_gives_an_actionable_error(monkeypatch):
    """A slim deployment installs no torch. The failure should name the extra
    to install, not surface as a bare ImportError."""
    import builtins

    from app.src.masking.JBGPDFMasking import PDFMasker

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "transformers":
            raise ImportError("No module named 'transformers'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(RuntimeError, match=r"\[masking\]"):
        PDFMasker()
