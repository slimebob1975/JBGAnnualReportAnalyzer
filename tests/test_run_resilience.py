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
        # Vilket dokument som behandlas står i loggkontexten, vilket fungerar
        # lika bra med flera arbetare som med en.
        from app.src import JBGLogContext as log_context

        outcome = self.results[Path(log_context.current_document.get()).stem]
        if isinstance(outcome, Exception):
            raise outcome
        return [outcome]


def _run(analyzer, tmp_path, workers=1):
    """Drive do_analysis over the stub."""
    analyzer.MAX_WORKERS = workers

    def progress(done, total, name):
        pass

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


# ----------------------------- ett dokument som inte gav något ska ändå synas
def test_ett_dokument_utan_nyckeltal_redovisas_som_ej_analyserat(tmp_path):
    """Alfa-kassans årsredovisning för 2024 försvann spårlöst: ingen rad i
    _ejanalyserade, ingen banderoll, bara en kolumn färre än någon väntade
    sig."""
    good = {"Kassan": {"2025": {"Summa tillgångar": {"värde": 1}}}}
    analyzer = _Stub({"a": good, "b": {}, "c": good})

    out = _run(analyzer, tmp_path)

    reasons = dict(analyzer.skipped_files)
    assert "b.pdf" in reasons, "dokumentet måste synas någonstans"
    assert "Inga nyckeltal" in reasons["b.pdf"]

    written = json.loads(Path(out).read_text(encoding="utf-8"))
    skipped = written.get(JBGAnnualReportAnalyzer.SKIPPED_KEY, [])
    assert any(entry["fil"] == "b.pdf" for entry in skipped)


def test_de_ovriga_dokumenten_analyseras_som_vanligt(tmp_path):
    good = {"Kassan": {"2025": {"Summa tillgångar": {"värde": 1}}}}
    analyzer = _Stub({"a": good, "b": {}, "c": good})

    out = _run(analyzer, tmp_path)

    assert "Kassan" in json.loads(Path(out).read_text(encoding="utf-8"))
    assert len(analyzer.skipped_files) == 1


# ------------------------------------- två dokument på samma kassa och år
def test_tva_dokument_pa_samma_kassa_och_ar_rapporteras(tmp_path, caplog):
    """Lärarnas årsredovisning för 2024 fick fel räkenskapsår och slogs ihop
    med 2025 års rapport. Kolumnen såg fullständig ut och var det inte."""
    import logging

    samma = {"Lärarnas arbetslöshetskassa": {"2025": {"Summa tillgångar": {"värde": 1}}}}
    analyzer = _Stub({"a": samma, "b": samma})

    with caplog.at_level(logging.WARNING):
        _run(analyzer, tmp_path)

    assert "fanns redan från ett annat dokument" in caplog.text
    assert "Lärarnas arbetslöshetskassa 2025" in caplog.text


def test_olika_ar_for_samma_kassa_ar_helt_i_sin_ordning(tmp_path, caplog):
    """Tre årgångar i samma körning är hela poängen med jämförelsen."""
    import logging

    analyzer = _Stub({
        "a": {"Kassan": {"2024": {"Summa tillgångar": {"värde": 1}}}},
        "b": {"Kassan": {"2025": {"Summa tillgångar": {"värde": 2}}}},
    })

    with caplog.at_level(logging.WARNING):
        _run(analyzer, tmp_path)

    assert "fanns redan" not in caplog.text


# ------------------------- dokumentets arbete rör inte analysobjektet
def test_ett_dokuments_utfall_returneras_i_stallet_for_att_skrivas(tmp_path):
    """Skrev tidigare rakt in i self.skipped_files och self.stability_findings.
    Med flera arbetare blir ordningen slumpmässig, och två körningar av samma
    material skulle ge samma siffror i olika ordning."""
    good = {"Kassan": {"2025": {"Summa tillgångar": {"värde": 1}}}}
    analyzer = _Stub({"a": good})
    analyzer._index = 0

    outcome = analyzer._analyse_document(Path("a.pdf"), model="gpt-4o")

    assert outcome.result == good
    assert outcome.skipped is None
    assert analyzer.skipped_files == [], "metoden ska inte röra analysobjektet"
    assert analyzer.stability_findings == []


def test_ett_misslyckat_dokument_returnerar_sin_orsak(tmp_path):
    analyzer = _Stub({"a": RuntimeError("modellen svarade inte")})
    analyzer._index = 0

    outcome = analyzer._analyse_document(Path("a.pdf"), model="gpt-4o")

    assert outcome.result == {}
    assert outcome.skipped[0] == "a.pdf"
    assert "avbröts av ett fel" in outcome.skipped[1]
    assert analyzer.skipped_files == []


def test_utfallen_slas_ihop_i_filordning(tmp_path):
    """Två körningar av samma material ska ge samma utdata."""
    good = {"Kassan": {"2025": {"Summa tillgångar": {"värde": 1}}}}
    analyzer = _Stub({"a": good, "b": RuntimeError("x"), "c": good,
                      "d": RuntimeError("y")})

    _run(analyzer, tmp_path)

    assert [name for name, _ in analyzer.skipped_files] == ["b.pdf", "d.pdf"]


def test_ocr_diagnosen_ar_tradlokal():
    """Som fält på objektet kunde ett dokuments diagnos förklara ett annats
    avbrott."""
    import threading

    from app.src.JBGAnnualReportAnalysis import JBGAnnualReportAnalyzer as A

    a = A.__new__(A)
    a.last_ocr_diagnosis = "huvudtrådens"
    sett = {}

    def annan():
        sett["innan"] = a.last_ocr_diagnosis
        a.last_ocr_diagnosis = "den andra trådens"
        sett["efter"] = a.last_ocr_diagnosis

    t = threading.Thread(target=annan)
    t.start()
    t.join()

    assert sett["efter"] == "den andra trådens"
    assert a.last_ocr_diagnosis == "huvudtrådens", "trådarna delade fältet"


# ------------------------------------------------- flera arbetare
def test_samma_utdata_med_flera_arbetare(tmp_path):
    """Hela poängen med att slå ihop i filordning: två körningar av samma
    material ska ge samma utdata, oavsett vilket dokument som blev klart
    först."""
    good = {"Kassan": {"2025": {"Summa tillgångar": {"värde": 1}}}}
    resultat = {"a": good, "b": RuntimeError("x"), "c": good,
                "d": RuntimeError("y"), "e": good}

    en_katalog = tmp_path / "en"
    en_katalog.mkdir()
    en = _Stub(resultat)
    _run(en, en_katalog, workers=1)

    flera_katalog = tmp_path / "flera"
    flera_katalog.mkdir()
    flera = _Stub(resultat)
    _run(flera, flera_katalog, workers=4)

    assert flera.skipped_files == en.skipped_files
    assert [name for name, _ in flera.skipped_files] == ["b.pdf", "d.pdf"]


def test_alla_dokument_behandlas_med_flera_arbetare(tmp_path):
    good = {"Kassan": {"2025": {"Summa tillgångar": {"värde": 1}}}}
    analyzer = _Stub({namn: good for namn in "abcdefgh"})

    out = _run(analyzer, tmp_path, workers=4)

    assert analyzer.skipped_files == []
    assert "Kassan" in json.loads(Path(out).read_text(encoding="utf-8"))


def test_antalet_arbetare_overstiger_aldrig_antalet_filer(tmp_path):
    """Sexton arbetare på tre dokument är tretton trådar som inget gör."""
    good = {"Kassan": {"2025": {"Summa tillgångar": {"värde": 1}}}}
    analyzer = _Stub({"a": good, "b": good, "c": good})
    analyzer.MAX_WORKERS = 16

    sedda = []
    original = analyzer._analysed_documents

    def spion(model, workers, report):
        sedda.append(workers)
        return original(model=model, workers=workers, report=report)

    analyzer._analysed_documents = spion
    _run(analyzer, tmp_path, workers=16)

    assert sedda == [3]
