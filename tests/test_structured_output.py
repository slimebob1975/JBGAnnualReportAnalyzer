"""Tests for Step 2b: structured outputs, page-aware chunking, concurrency
and the JSON endpoints that drive the automatic download."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.src import JBGMetricSchema as schema  # noqa: E402
from app.src.JBGAnnualReportAnalysis import JBGAnnualReportAnalyzer  # noqa: E402

METRICS = Path(__file__).resolve().parents[1] / "app" / "prompt" / "json" / "nyckeltalsdefinitioner.json"


# ------------------------------------------------------------------ schema
def test_schema_is_strict_and_closed():
    names = schema.load_metric_names(METRICS)
    built = schema.build_schema(names)

    assert built["strict"] is True
    root = built["schema"]
    assert root["additionalProperties"] is False
    # Strict mode requires every property to be listed as required.
    assert set(root["required"]) == set(root["properties"])

    item = root["properties"][schema.FIELD_METRICS]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])
    assert item["properties"][schema.FIELD_NAME]["enum"] == names
    assert len(names) == 18


def test_metric_names_are_enumerated_so_invention_is_impossible():
    names = schema.load_metric_names(METRICS)
    assert "Balansomslutning" in names
    assert "Hittepå-nyckeltal" not in names


def test_flat_reply_becomes_nested_structure():
    payload = {
        "kassa": "Livs arbetslöshetskassa",
        "år": 2023,
        "nyckeltal": [
            {
                "namn": "Balansomslutning",
                "värde": 63853,
                "källa": "Sida 14 – Balansräkning",
                "säkerhet": 1.0,
                "kommentar": "Summa tillgångar.",
            }
        ],
    }
    nested = schema.flat_to_nested(payload)
    assert nested == {
        "Livs arbetslöshetskassa": {
            "2023": {
                "Balansomslutning": {
                    "värde": 63853,
                    "källa": "Sida 14 – Balansräkning",
                    "säkerhet": 1.0,
                    "kommentar": "Summa tillgångar.",
                }
            }
        }
    }


def test_null_valued_metrics_are_dropped():
    """The exact behaviour that produced all 18 merge conflicts: a chunk that
    could not see the balance sheet reporting null with säkerhet 0.1."""
    payload = {
        "kassa": "K",
        "år": 2023,
        "nyckeltal": [
            {"namn": "Balansomslutning", "värde": None, "källa": "saknas",
             "säkerhet": 0.1, "kommentar": "Balansräkningen finns inte i utdraget."},
            {"namn": "Eget kapital", "värde": 16339, "källa": "Sida 14",
             "säkerhet": 1.0, "kommentar": "Explicit."},
        ],
    }
    nested = schema.flat_to_nested(payload)
    metrics = nested["K"]["2023"]
    assert "Balansomslutning" not in metrics
    assert metrics["Eget kapital"]["värde"] == 16339


def test_year_falls_back_when_model_omits_it():
    payload = {"kassa": "K", "år": None,
               "nyckeltal": [{"namn": "Eget kapital", "värde": 1, "källa": "s",
                              "säkerhet": 1, "kommentar": "c"}]}
    assert "2024" in schema.flat_to_nested(payload, fallback_year=2024)["K"]


def test_reply_without_fund_name_is_discarded():
    payload = {"kassa": "", "år": 2023, "nyckeltal": []}
    assert schema.flat_to_nested(payload) == {}


# ------------------------------------------------------- page-aware chunking
def _analyzer(**attrs):
    a = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)
    a.metrics_path = METRICS
    a._schema_cache = None
    for k, v in attrs.items():
        setattr(a, k, v)
    return a


def _paged_text(n_pages, words_per_page=50):
    return "\n\n".join(
        f"[Sida {i}]\n" + " ".join(f"ord{i}_{w}" for w in range(words_per_page))
        for i in range(1, n_pages + 1)
    )


def test_every_chunk_keeps_a_page_marker():
    """The källa field depends on these markers. The old token-window split
    could strip them off the front of a chunk."""
    text = _paged_text(30)
    chunks = _analyzer()._chunk_text_by_pages(text, max_tokens=300, model="gpt-4o")
    assert len(chunks) > 1, "expected the text to need splitting"
    for chunk in chunks:
        assert chunk.lstrip().startswith("[Sida "), chunk[:60]


def test_pages_are_never_split_across_chunks():
    text = _paged_text(20)
    chunks = _analyzer()._chunk_text_by_pages(text, max_tokens=400, model="gpt-4o")
    seen = []
    for chunk in chunks:
        seen.extend(int(m) for m in __import__("re").findall(r"\[Sida (\d+)\]", chunk))
    # Every page appears exactly once, and in order.
    assert seen == sorted(seen)
    assert len(seen) == len(set(seen)) == 20


def test_whole_document_fits_one_chunk_at_default_budget():
    """The sample corpus tops out around 68k characters. At MAX_TOKENS=30000
    that is a single call, which removes the cross-chunk conflicts entirely."""
    text = _paged_text(36, words_per_page=120)
    chunks = _analyzer()._chunk_text_for_model(text, model="gpt-4o")
    assert len(chunks) == 1


def test_oversized_single_page_is_still_split():
    huge = "[Sida 1]\n" + " ".join(f"ord{i}" for i in range(20000))
    a = _analyzer()
    chunks = a._chunk_text_by_pages(huge, max_tokens=500, model="gpt-4o")
    assert len(chunks) > 1


# ---------------------------------------------------------------- API calls
def test_reasoning_models_get_no_temperature():
    assert JBGAnnualReportAnalyzer._is_reasoning_model("gpt-5.2")
    assert JBGAnnualReportAnalyzer._is_reasoning_model("gpt-5-mini")
    assert JBGAnnualReportAnalyzer._is_reasoning_model("o3-mini")
    assert not JBGAnnualReportAnalyzer._is_reasoning_model("gpt-4o")


def test_schema_is_built_once_and_cached():
    a = _analyzer()
    first = a._response_schema()
    assert first is a._response_schema()


def test_chunks_are_merged_in_order_not_completion_order():
    """Concurrency must not make the output depend on which call finished
    first, or two identical runs could disagree."""
    import time as _time

    a = _analyzer(MAX_CONCURRENT_CHUNKS=4)
    order = []

    def fake(index, total, chunk, the_year, model, purpose=None):
        # deliberately finish in reverse order
        _time.sleep(0.05 * (total - index))
        order.append(index)
        return {"K": {"2023": {"Eget kapital": {"värde": index, "källa": "", "säkerhet": 1, "kommentar": "c"}}}}

    a._analyse_chunk = fake
    results = a._analyse_chunks(["a", "b", "c", "d"], the_year=2023, model="gpt-4o")
    assert order != sorted(order), "test setup failed to reverse completion order"
    values = [r["K"]["2023"]["Eget kapital"]["värde"] for r in results]
    assert values == [0, 1, 2, 3]


def test_failed_chunks_do_not_sink_the_batch():
    a = _analyzer(MAX_CONCURRENT_CHUNKS=2)

    def fake(index, total, chunk, the_year, model, purpose=None):
        if index == 1:
            return None  # simulates a parse failure or API error
        return {"K": {"2023": {"Eget kapital": {"värde": index, "källa": "",
                                                "säkerhet": 1, "kommentar": "c"}}}}

    a._analyse_chunk = fake
    assert len(a._analyse_chunks(["a", "b", "c"], the_year=2023, model="gpt-4o")) == 2


# ---------------------------------------------------------------- endpoints
# The endpoint surface moved to background jobs in Step 3; those tests live in
# tests/test_jobs.py. What matters here is that a rejected request still comes
# back as JSON rather than an HTML error page, since the form no longer reloads.
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JBG_JOB_DIR", str(tmp_path / "jobs"))
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    import importlib

    import app.main as main

    importlib.reload(main)
    with fastapi_testclient.TestClient(main.app) as c:
        yield c, main


def test_index_renders_and_references_the_new_script(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200
    assert "/static/javascript/script.js" in r.text
    assert 'data-kind="analysis"' in r.text
    # the old link-and-return panel is gone from the default view
    assert "back-button" not in r.text


def test_bad_format_is_rejected_before_any_work(client):
    c, _ = client
    r = c.post(
        "/api/analyze",
        data={"model": "gpt-5.2", "apikey": "sk-x", "format": "pdf",
              "sources": "yes", "use_masking": "yes"},
        files={"file": ("t.pdf", b"x", "application/pdf")},
    )
    assert r.status_code == 400


def test_wrong_filetype_is_reported_as_json(client):
    c, _ = client
    r = c.post(
        "/api/analyze",
        data={"model": "gpt-5.2", "apikey": "sk-x", "format": "json",
              "sources": "yes", "use_masking": "yes"},
        files={"file": ("x.docx", b"x", "application/octet-stream")},
    )
    assert r.status_code == 400
    assert r.json()["ok"] is False
    assert "Ogiltig filtyp" in r.json()["message"]


def test_rejected_upload_leaves_no_job_directory_behind(client):
    """A refused upload must not leak an empty working directory."""
    c, main = client
    before = len(list(main.jobs.root.iterdir()))
    c.post(
        "/api/analyze",
        data={"model": "gpt-5.2", "apikey": "sk-x", "format": "json",
              "sources": "yes", "use_masking": "yes"},
        files={"file": ("x.docx", b"x", "application/octet-stream")},
    )
    assert len(list(main.jobs.root.iterdir())) == before
