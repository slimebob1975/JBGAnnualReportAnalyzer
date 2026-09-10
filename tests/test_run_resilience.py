"""Tests for surviving a long run.

Written against a real failure: a 24-file analysis was swept off disk by the
retention sweeper after sixty minutes, while it was still reading from its
own working directory. The browser said "Jobbet finns inte eller har gått
ut", the analysis thread carried on until the next PDF was gone, and the
twenty-two documents already finished were lost with it.

    pymupdf.FileNotFoundError: no such file:
    '...\\jbg-jobs\\48708f9c...\\Årsredovisning Livs a-kassa 2025(175097) (0).pdf'
"""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.src.JBGAnnualReportAnalysis import JBGAnnualReportAnalyzer  # noqa: E402
from app.src.JBGJobs import (  # noqa: E402
    STATUS_DONE,
    STATUS_RUNNING,
    JobRegistry,
)


# ------------------------------------------------------------- job lifetime
def test_a_working_job_is_not_swept_however_long_it_runs(tmp_path):
    """The bug. A run longer than the lifetime deleted its own files."""
    registry = JobRegistry(root=tmp_path / "jobs", ttl_seconds=60, sweep_interval=0)
    try:
        job = registry.create()
        (job.directory / "arsredovisning.pdf").write_bytes(b"x")
        job.status = STATUS_RUNNING
        # Older than the lifetime by any measure, but still working.
        job.created_at = time.time() - 100_000
        job.started_at = job.created_at
        job.touch()

        registry.purge_expired()

        assert registry.get(job.id) is job, "the running job was swept"
        assert job.directory.exists()
    finally:
        registry.shutdown()


def test_progress_on_a_long_run_keeps_the_job_alive(tmp_path):
    """Progress is reported per document, so a 90-minute run never falls
    silent for a full lifetime."""
    registry = JobRegistry(root=tmp_path / "jobs", ttl_seconds=60, sweep_interval=0)
    try:
        job = registry.create()
        job.status = STATUS_RUNNING
        job.last_activity = time.time() - 3600

        registry.progress_callback(job)(5, 24, "fil.pdf")
        registry.purge_expired()

        assert registry.get(job.id) is job
    finally:
        registry.shutdown()


def test_a_job_that_has_hung_is_still_cleaned_up(tmp_path):
    """Never sweeping a running job would leak a dead thread's uploads."""
    registry = JobRegistry(root=tmp_path / "jobs", ttl_seconds=60, sweep_interval=0)
    try:
        job = registry.create()
        directory = job.directory
        job.status = STATUS_RUNNING
        job.last_activity = time.time() - 3600  # silent for an hour

        registry.purge_expired()

        assert registry.get(job.id) is None
        assert not directory.exists()
    finally:
        registry.shutdown()


def test_a_finished_job_expires_from_when_it_finished(tmp_path):
    """The lifetime is about how long results linger, not how long the work
    took: a 90-minute run should not expire the moment it completes."""
    registry = JobRegistry(root=tmp_path / "jobs", ttl_seconds=3600, sweep_interval=0)
    try:
        job = registry.create()
        job.created_at = time.time() - 90 * 60  # a long run
        job.status = STATUS_DONE
        job.finished_at = time.time()
        job.touch()

        registry.purge_expired()

        assert registry.get(job.id) is job, "results expired as soon as they existed"
    finally:
        registry.shutdown()


# ------------------------------------------------------- failure isolation
class _Stub(JBGAnnualReportAnalyzer):
    """An analyzer whose per-document work is scripted."""

    # The stubbed steps below stand in for the model, so the passes that would
    # call it are off.
    USE_SECOND_PASS_FOR_MISSING = False
    DERIVE_MISSING_SUBTOTALS = False
    FIX_BROKEN_LINES_WITH_KEY_NUMBERS = False
    VERIFY_OCR_EXTRACTION = False
    VERIFY_ALL_EXTRACTIONS = False

    def __init__(self, results):
        self.results = results
        self.upload_files = [Path(f"{name}.pdf") for name in results]
        self.skipped_files = []
        self.stability_findings = []
        self.validation_findings = []
        self.metrics_path = None
        self.fund_list_path = None
        self.use_masking = False
        self.model_roles = {}
        self.usage = self._quiet_usage()

    @staticmethod
    def _quiet_usage():
        from app.src import JBGUsage as usage

        return usage.UsageTracker()

    # The pipeline up to the text is stubbed; the point is what the loop does
    # with an exception raised from the middle of a document.
    def _ensure_readable_pdf(self, pdf_path):
        return pdf_path

    def _extract_text_from_pdf_from_pdf(self, pdf_path, model=""):
        return "x" * 5000

    def _find_primary_year_from_text(self, text, model=""):
        return 2025

    def _chunk_text_for_model(self, text, model=""):
        return ["chunk"]

    def _analyse_chunks(self, chunks, the_year=None, model=""):
        outcome = self.results[Path(self.upload_files[self._index].stem).name]
        if isinstance(outcome, Exception):
            raise outcome
        return [outcome]


def _run(analyzer, tmp_path):
    """Drive do_analysis over the stub, tracking which file is current."""
    order = list(analyzer.upload_files)

    def progress(done, total, name):
        analyzer._index = min(done, len(order) - 1)

    analyzer._index = 0
    return analyzer.do_analysis(
        tmp_path / "resultat.json", model="gpt-4o", progress_callback=progress
    )


def test_one_failing_document_does_not_discard_the_others(tmp_path):
    good = {"Kassan": {"2025": {"Summa tillgångar": {"värde": 1}}}}
    analyzer = _Stub({
        "a": good,
        "b": RuntimeError("modellen svarade inte"),
        "c": good,
    })

    out = _run(analyzer, tmp_path)

    written = json.loads(Path(out).read_text(encoding="utf-8"))
    assert "Kassan" in written, "the surviving documents were lost with the failure"
    reasons = dict(analyzer.skipped_files)
    assert "b.pdf" in reasons
    assert "avbröts av ett fel" in reasons["b.pdf"]


def test_a_partial_result_is_written_as_the_run_goes(tmp_path):
    """So that a crash, a sweep, or a closed browser still leaves the work."""
    good = {"Kassan": {"2025": {"Summa tillgångar": {"värde": 1}}}}
    analyzer = _Stub({"a": good, "b": good})

    _run(analyzer, tmp_path)

    partial = tmp_path / ("resultat" + JBGAnnualReportAnalyzer.PARTIAL_SUFFIX)
    assert partial.is_file(), "no partial result was written"
    assert "Kassan" in json.loads(partial.read_text(encoding="utf-8"))


def test_the_failure_is_recorded_in_the_result_file(tmp_path):
    good = {"Kassan": {"2025": {"Summa tillgångar": {"värde": 1}}}}
    analyzer = _Stub({"a": good, "b": RuntimeError("nätverksfel")})

    out = _run(analyzer, tmp_path)

    written = json.loads(Path(out).read_text(encoding="utf-8"))
    skipped = written.get(JBGAnnualReportAnalyzer.SKIPPED_KEY, [])
    assert any(entry["fil"] == "b.pdf" for entry in skipped)


@pytest.mark.parametrize("failure", [RuntimeError("x"), ValueError("y"), KeyError("z")])
def test_any_exception_type_is_contained(tmp_path, failure):
    good = {"Kassan": {"2025": {"Summa tillgångar": {"värde": 1}}}}
    analyzer = _Stub({"a": good, "b": failure})

    _run(analyzer, tmp_path)

    assert len(analyzer.skipped_files) == 1
