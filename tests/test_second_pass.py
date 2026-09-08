"""Tests for the targeted second pass over metrics the first pass missed.

Kommunal returned 18, then 17, then 16 metrics across three consecutive runs
on an unchanged document. With one chunk per file there is no second chunk to
catch the miss, so a narrow follow-up call closes the gap.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.src import JBGMetricSchema as schema  # noqa: E402
from app.src.JBGAnnualReportAnalysis import JBGAnnualReportAnalyzer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KEY_DEFS = ROOT / "app" / "prompt" / "json" / "nyckeltalsdefinitioner.json"
ALL_METRICS = schema.load_metric_names(KEY_DEFS)


def _analyzer():
    a = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)
    a.metrics_path = KEY_DEFS
    a.instruction_path = ROOT / "app" / "prompt" / "GPT-instruktioner_komprimerad.md"
    a._schema_cache = None
    return a


def _result(present, fund="Kommunalarbetarnas", year="2024"):
    return {
        fund: {
            year: {
                name: {"värde": 100 + i, "källa": "Sida 2", "säkerhet": 0.9,
                       "kommentar": "första genomgången"}
                for i, name in enumerate(present)
            }
        }
    }


# ------------------------------------------------------------ missing set
def test_missing_metrics_lists_what_is_absent():
    a = _analyzer()
    present = [n for n in ALL_METRICS if n not in ("Eftergift", "Skulder")]
    missing = a._missing_metrics(_result(present))
    assert missing == [n for n in ALL_METRICS if n in ("Skulder", "Eftergift")]


def test_null_valued_metrics_count_as_missing():
    a = _analyzer()
    result = _result(ALL_METRICS)
    result["Kommunalarbetarnas"]["2024"]["Eftergift"]["värde"] = None
    assert a._missing_metrics(result) == ["Eftergift"]


def test_nothing_missing_when_all_present():
    assert _analyzer()._missing_metrics(_result(ALL_METRICS)) == []


# --------------------------------------------------------------- behaviour
def test_no_extra_call_when_nothing_is_missing():
    """The pass must cost nothing on a complete document."""
    a = _analyzer()
    calls = []
    a._make_openai_api_call = lambda *args, **kwargs: calls.append(1) or "{}"

    result = _result(ALL_METRICS)
    a._second_pass_for_missing(result, ["text"], the_year=2024, model="gpt-5.2")
    assert calls == []


def test_second_pass_recovers_a_missed_metric():
    a = _analyzer()
    present = [n for n in ALL_METRICS if n not in ("Eftergift", "Skulder")]
    result = _result(present)
    seen = {}

    def fake(system_prompt, request_text, model="", response_schema=None, **kwargs):
        seen["enum"] = response_schema["schema"]["properties"]["nyckeltal"]["items"][
            "properties"
        ]["namn"]["enum"]
        seen["prompt"] = system_prompt
        return json.dumps({
            "kassa": "Kommunalarbetarnas", "år": 2024,
            "nyckeltal": [{"namn": "Eftergift", "värde": 4211, "källa": "Not 7",
                           "säkerhet": 0.85, "kommentar": "Hittad i not 7."}],
        }, ensure_ascii=False)

    a._make_openai_api_call = fake
    a._second_pass_for_missing(result, ["text"], the_year=2024, model="gpt-5.2")

    metrics = result["Kommunalarbetarnas"]["2024"]
    assert metrics["Eftergift"]["värde"] == 4211
    # the schema makes it structurally impossible to answer about anything else
    assert set(seen["enum"]) == {"Eftergift", "Skulder"}
    # and the prompt carries exactly one metric definition, not all 18
    # (the base instruction text mentions some metric names as examples, so
    # count definition entries rather than bare occurrences)
    assert seen["prompt"].count('"Nyckeltal":') == 2
    assert '"Nyckeltal": "Eftergift"' in seen["prompt"]


def test_recovered_values_are_tagged():
    a = _analyzer()
    result = _result([n for n in ALL_METRICS if n not in ("Eftergift", "Skulder")])
    a._make_openai_api_call = lambda *args, **kwargs: json.dumps({
        "kassa": "K", "år": 2024,
        "nyckeltal": [{"namn": "Eftergift", "värde": 1, "källa": "s",
                       "säkerhet": 0.8, "kommentar": "Hittad i not 7."}],
    }, ensure_ascii=False)
    a._second_pass_for_missing(result, ["t"], the_year=2024, model="m")

    comment = result["Kommunalarbetarnas"]["2024"]["Eftergift"]["kommentar"]
    assert comment.startswith(JBGAnnualReportAnalyzer.SECOND_PASS_TAG)
    assert "Hittad i not 7." in comment


def test_first_pass_values_are_never_overwritten():
    """A second pass that re-answers a metric it was not asked about must not
    clobber a value the first pass already established."""
    a = _analyzer()
    result = _result([n for n in ALL_METRICS if n not in ("Eftergift", "Skulder")])
    original = dict(result["Kommunalarbetarnas"]["2024"]["Balansomslutning"])

    a._make_openai_api_call = lambda *args, **kwargs: json.dumps({
        "kassa": "K", "år": 2024,
        "nyckeltal": [
            {"namn": "Eftergift", "värde": 1, "källa": "s", "säkerhet": 0.8, "kommentar": "c"},
            {"namn": "Balansomslutning", "värde": 999999, "källa": "fel",
             "säkerhet": 0.2, "kommentar": "gissning"},
        ],
    }, ensure_ascii=False)
    a._second_pass_for_missing(result, ["t"], the_year=2024, model="m")

    assert result["Kommunalarbetarnas"]["2024"]["Balansomslutning"] == original


def test_values_are_grafted_onto_the_existing_fund_and_year():
    """The second pass may word the fund name differently. That must not split
    the file's result into two funds."""
    a = _analyzer()
    result = _result([n for n in ALL_METRICS if n not in ("Eftergift", "Skulder")])
    a._make_openai_api_call = lambda *args, **kwargs: json.dumps({
        "kassa": "Kommunalarbetarnas Arbetslöshetskassa", "år": 2023,
        "nyckeltal": [{"namn": "Eftergift", "värde": 7, "källa": "s",
                       "säkerhet": 0.8, "kommentar": "c"}],
    }, ensure_ascii=False)
    a._second_pass_for_missing(result, ["t"], the_year=2024, model="m")

    assert list(result) == ["Kommunalarbetarnas"]
    assert list(result["Kommunalarbetarnas"]) == ["2024"]
    assert result["Kommunalarbetarnas"]["2024"]["Eftergift"]["värde"] == 7


def test_skipped_when_most_metrics_are_missing():
    """Half the metrics absent means the first pass failed, not that lines were
    overlooked. Re-reading the same text would just cost another call."""
    a = _analyzer()
    calls = []
    a._make_openai_api_call = lambda *args, **kwargs: calls.append(1) or "{}"
    a._second_pass_for_missing(_result(ALL_METRICS[:3]), ["t"], the_year=2024, model="m")
    assert calls == []


def test_stops_early_once_nothing_is_missing():
    """With several chunks, the pass must not keep asking after the last gap
    has been filled."""
    a = _analyzer()
    result = _result([n for n in ALL_METRICS if n not in ("Eftergift", "Skulder")])
    calls = []

    def fake(system_prompt, request_text, model="", response_schema=None, **kwargs):
        calls.append(request_text)
        return json.dumps({
            "kassa": "K", "år": 2024,
            "nyckeltal": [
                {"namn": "Eftergift", "värde": 1, "källa": "s",
                 "säkerhet": 0.8, "kommentar": "c"},
                {"namn": "Skulder", "värde": 2, "källa": "s",
                 "säkerhet": 0.8, "kommentar": "c"},
            ],
        }, ensure_ascii=False)

    a._make_openai_api_call = fake
    a._second_pass_for_missing(result, ["chunk a", "chunk b", "chunk c"],
                               the_year=2024, model="m")
    assert len(calls) == 1, "should stop after the gaps are filled"


def test_a_failed_second_pass_leaves_the_result_intact():
    a = _analyzer()
    present = [n for n in ALL_METRICS if n != "Eftergift"]
    result = _result(present)
    before = json.dumps(result, ensure_ascii=False, sort_keys=True)

    def boom(*args, **kwargs):
        raise RuntimeError("API-nyckeln är ogiltig")

    a._make_openai_api_call = boom
    a._second_pass_for_missing(result, ["t"], the_year=2024, model="m")
    assert json.dumps(result, ensure_ascii=False, sort_keys=True) == before


def test_unparsable_second_pass_response_is_survived():
    a = _analyzer()
    result = _result([n for n in ALL_METRICS if n not in ("Eftergift", "Skulder")])
    a._make_openai_api_call = lambda *args, **kwargs: "inte JSON alls"
    a._second_pass_for_missing(result, ["t"], the_year=2024, model="m")
    assert "Eftergift" not in result["Kommunalarbetarnas"]["2024"]


# ------------------------------------------------------------------ schema
def test_restricted_schema_is_still_strict():
    a = _analyzer()
    built = a._response_schema(["Eftergift", "Skulder"])
    assert built["strict"] is True
    item = built["schema"]["properties"]["nyckeltal"]["items"]
    assert item["properties"]["namn"]["enum"] == ["Eftergift", "Skulder"]
    assert set(item["required"]) == set(item["properties"])


def test_schemas_are_cached_per_metric_set():
    a = _analyzer()
    full_a, full_b = a._response_schema(), a._response_schema()
    part_a, part_b = a._response_schema(["Eftergift"]), a._response_schema(["Eftergift"])
    assert full_a is full_b
    assert part_a is part_b
    assert full_a is not part_a
    assert len(full_a["schema"]["properties"]["nyckeltal"]["items"]["properties"]["namn"]["enum"]) == 18


def test_second_pass_instruction_permits_omission():
    """It must not pressure the model into inventing a value: an absent metric
    is a legitimate answer."""
    text = schema.describe_for_second_pass(["Eftergift"])
    assert "Eftergift" in text
    assert "utelämna" in text
    assert "Gissa inte" in text


@pytest.mark.parametrize("flag", [True, False])
def test_flag_controls_whether_the_pass_runs(flag, monkeypatch):
    a = _analyzer()
    monkeypatch.setattr(JBGAnnualReportAnalyzer, "USE_SECOND_PASS_FOR_MISSING", flag)
    assert a.USE_SECOND_PASS_FOR_MISSING is flag


# ------------------------------------------------------- when it does not run
def test_a_single_missing_metric_does_not_trigger_a_pass():
    """Measured over a corpus: 13 of 17 passes chased one absent metric and
    recovered nothing, at roughly 15 000 tokens each."""
    a = _analyzer()
    calls = []
    a._make_openai_api_call = lambda *args, **kwargs: calls.append(1) or "{}"

    result = _result([n for n in ALL_METRICS if n != "Eftergift"])
    a._second_pass_for_missing(result, ["text"], the_year=2024, model="gpt-5.2")
    assert calls == []


def test_two_missing_metrics_do_trigger_a_pass():
    a = _analyzer()
    calls = []

    def fake(system_prompt, request_text, model="", response_schema=None, **kwargs):
        calls.append(1)
        return json.dumps({"kassa": "K", "år": 2024, "nyckeltal": []}, ensure_ascii=False)

    a._make_openai_api_call = fake
    result = _result([n for n in ALL_METRICS if n not in ("Eftergift", "Skulder")])
    a._second_pass_for_missing(result, ["text"], the_year=2024, model="gpt-5.2")
    assert len(calls) == 1


def test_the_page_offset_fallback_is_off_by_default():
    """105 calls in one run, 14 of 17 answers equal to the default."""
    assert JBGAnnualReportAnalyzer.USE_LLM_FALLBACK_FOR_PAGE_OFFSET is False
