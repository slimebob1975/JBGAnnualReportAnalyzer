"""Tests for per-run token accounting.

The service was run dozens of times before anyone could say what a run cost.
Deriving it from a log is possible but should not be necessary, and the
interesting question is which part of the pipeline is spending the tokens.
"""

import json
import sys
import threading
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.src import JBGUsage as usage  # noqa: E402


def _usage(prompt, completion, cached=None):
    details = types.SimpleNamespace(cached_tokens=cached) if cached is not None else None
    return types.SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion, prompt_tokens_details=details
    )


# ------------------------------------------------------------------ totals
def test_tokens_are_grouped_by_model_and_purpose():
    tracker = usage.UsageTracker()
    tracker.record("gpt-5.2", usage.PURPOSE_EXTRACTION, _usage(12000, 900))
    tracker.record("gpt-5.2", usage.PURPOSE_SECOND_PASS, _usage(2500, 300))
    tracker.record("gpt-4o", usage.PURPOSE_PAGE_OFFSET, _usage(700, 5))

    assert tracker.calls == 3
    assert tracker.total_tokens == 12900 + 2800 + 705
    assert set(tracker.by_model) == {"gpt-5.2", "gpt-4o"}
    assert tracker.by_model["gpt-5.2"].calls == 2
    assert tracker.by_purpose[usage.PURPOSE_PAGE_OFFSET].total_tokens == 705


def test_a_call_without_usage_is_ignored():
    tracker = usage.UsageTracker()
    tracker.record("gpt-5.2", usage.PURPOSE_EXTRACTION, None)
    assert tracker.calls == 0


def test_missing_token_fields_do_not_crash():
    tracker = usage.UsageTracker()
    tracker.record("gpt-5.2", usage.PURPOSE_EXTRACTION, types.SimpleNamespace())
    assert tracker.calls == 1
    assert tracker.total_tokens == 0


def test_cached_prompt_tokens_are_counted_separately():
    tracker = usage.UsageTracker()
    tracker.record("gpt-5.2", usage.PURPOSE_EXTRACTION, _usage(12000, 900, cached=8000))
    assert tracker.cached_tokens == 8000
    # still part of the prompt total, not double counted
    assert tracker.total_tokens == 12900


def test_recording_is_thread_safe():
    """Chunks are analysed concurrently, so the tracker is written to from
    several threads at once."""
    tracker = usage.UsageTracker()

    def work():
        for _ in range(200):
            tracker.record("gpt-5.2", usage.PURPOSE_EXTRACTION, _usage(10, 1))

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert tracker.calls == 1600
    assert tracker.total_tokens == 1600 * 11


# -------------------------------------------------------------------- cost
def test_no_cost_without_configured_prices():
    tracker = usage.UsageTracker()
    tracker.record("gpt-5.2", usage.PURPOSE_EXTRACTION, _usage(1_000_000, 0))
    assert tracker.cost({}) is None


def test_cost_uses_prices_per_million_tokens():
    tracker = usage.UsageTracker()
    tracker.record("gpt-5.2", usage.PURPOSE_EXTRACTION, _usage(2_000_000, 500_000))
    cost = tracker.cost({"gpt-5.2": {"in": 1.25, "ut": 10.00}})
    assert cost == pytest.approx(2 * 1.25 + 0.5 * 10.00)


def test_a_single_unpriced_model_suppresses_the_total():
    """A partial total would understate the cost, which is the one direction
    an estimate must not err in."""
    tracker = usage.UsageTracker()
    tracker.record("gpt-5.2", usage.PURPOSE_EXTRACTION, _usage(1_000_000, 0))
    tracker.record("gpt-4o", usage.PURPOSE_YEAR, _usage(1_000_000, 0))
    assert tracker.cost({"gpt-5.2": {"in": 1.25, "ut": 10.0}}) is None


def test_shipped_price_file_is_empty():
    """Prices change and differ per account. A guessed figure would end up
    quoted in a report."""
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "app" / "config" / "model_prices.json").read_text(encoding="utf-8"))
    real = {k: v for k, v in data.items() if not k.startswith("_")}
    assert real == {}, "no model prices should be shipped"


def test_prices_can_be_pointed_elsewhere(tmp_path, monkeypatch):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"gpt-5.2": {"in": 1.0, "ut": 2.0}}), encoding="utf-8")
    monkeypatch.setenv("JBG_MODEL_PRICES", str(path))
    assert usage.load_prices()["gpt-5.2"]["in"] == 1.0


def test_a_broken_price_file_is_survived(tmp_path, monkeypatch):
    path = tmp_path / "prices.json"
    path.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("JBG_MODEL_PRICES", str(path))
    assert usage.load_prices() == {}


# ---------------------------------------------------------------- reporting
def test_summary_names_the_biggest_spender(caplog):
    tracker = usage.UsageTracker()
    for _ in range(23):
        tracker.record("gpt-5.2", usage.PURPOSE_EXTRACTION, _usage(12000, 900))
    for _ in range(15):
        tracker.record("gpt-5.2", usage.PURPOSE_PAGE_OFFSET, _usage(700, 5))

    with caplog.at_level("INFO"):
        usage.log_summary(tracker, files_analysed=23, prices={})
    text = "\n".join(r.message for r in caplog.records)
    assert "38 anrop" in text
    # purposes ordered by spend, so the expensive one is read first
    assert text.index(usage.PURPOSE_EXTRACTION) < text.index(usage.PURPOSE_PAGE_OFFSET)
    assert "per fil" in text
    assert "kostnad: okänd" in text


def test_summary_is_silent_when_nothing_was_called(caplog):
    with caplog.at_level("INFO"):
        usage.log_summary(usage.UsageTracker(), files_analysed=0, prices={})
    assert not caplog.records


def test_serialised_form_is_json_safe():
    tracker = usage.UsageTracker()
    tracker.record("gpt-5.2", usage.PURPOSE_EXTRACTION, _usage(100, 10))
    payload = tracker.as_dict({"gpt-5.2": {"in": 1.0, "ut": 2.0}, "_valuta": "USD"})
    json.dumps(payload, ensure_ascii=False)  # must not raise
    assert payload["anrop"] == 1
    # the payload rounds to four decimals; the raw figure is 0.00012
    assert tracker.cost({"gpt-5.2": {"in": 1.0, "ut": 2.0}}) == pytest.approx(0.00012)
    assert payload["kostnad"] == 0.0001
    assert payload["valuta"] == "USD"
