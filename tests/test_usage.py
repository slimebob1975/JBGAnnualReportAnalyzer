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


def test_shipped_prices_are_from_the_published_list():
    """These were transcribed from OpenAI's price page rather than guessed."""
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "app" / "config" / "model_prices.json").read_text(encoding="utf-8"))
    assert data["gpt-5.2"] == {"in": 1.75, "cachad": 0.175, "ut": 14.00}
    assert data["gpt-5.6-luna"] == {"in": 0.20, "cachad": 0.02, "ut": 1.20}


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


# ------------------------------------------------------------ model roles
def test_no_roles_means_the_selected_model_is_used(tmp_path, monkeypatch):
    monkeypatch.setenv("JBG_MODEL_ROLES", str(tmp_path / "absent.json"))
    assert usage.load_roles() == {}


def test_roles_map_config_keys_to_purposes(tmp_path, monkeypatch):
    path = tmp_path / "roles.json"
    path.write_text(json.dumps({
        "extraktion": "gpt-5.6-solar",
        "omsokning": "gpt-5.4-mini",
        "stabilitet": "gpt-5.2",
        "aar": "",
    }), encoding="utf-8")
    monkeypatch.setenv("JBG_MODEL_ROLES", str(path))

    roles = usage.load_roles()
    assert roles[usage.PURPOSE_EXTRACTION] == "gpt-5.6-solar"
    assert roles[usage.PURPOSE_SECOND_PASS] == "gpt-5.4-mini"
    assert roles[usage.PURPOSE_STABILITY] == "gpt-5.2"
    # blank means "use whatever the form selected", so it is not a role
    assert usage.PURPOSE_YEAR not in roles


def test_a_broken_roles_file_falls_back_to_the_selected_model(tmp_path, monkeypatch):
    path = tmp_path / "roles.json"
    path.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("JBG_MODEL_ROLES", str(path))
    assert usage.load_roles() == {}


def test_the_committed_example_is_neutral():
    """The real file is written on every run and therefore gitignored; only
    the example is committed, and it configures nothing."""
    root = Path(__file__).resolve().parents[1]
    config = root / "app" / "config"
    data = json.loads((config / "model_roles.example.json").read_text(encoding="utf-8"))
    assert {k: v for k, v in data.items() if not k.startswith("_") and v} == {}


def test_the_written_roles_file_is_not_tracked():
    """It changes on every analysis; committing it makes git permanently dirty
    and conflicts on every pull."""
    root = Path(__file__).resolve().parents[1]
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    assert "app/config/model_roles.json" in ignored


def test_a_fresh_checkout_falls_back_to_the_example(tmp_path, monkeypatch):
    monkeypatch.delenv("JBG_MODEL_ROLES", raising=False)
    config = tmp_path / "config"
    config.mkdir()
    (config / "model_roles.example.json").write_text(
        json.dumps({"extraktion": "", "omsokning": ""}), encoding="utf-8")
    assert usage.load_roles(config) == {}
    assert usage.selected_roles(config)["extraktion"] == usage.DEFAULT_MODEL


def test_a_role_overrides_the_selected_model(tmp_path, monkeypatch):
    """The whole point: the second pass can run on a different model from the
    first without changing the form."""
    import types

    from app.src.JBGAnnualReportAnalysis import JBGAnnualReportAnalyzer

    a = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)
    a.model_roles = {usage.PURPOSE_SECOND_PASS: "gpt-5.4-mini"}
    a.usage = usage.UsageTracker()
    a.MAX_COMPLETION_TOKENS = 100
    a.OPENAI_MAX_RETRIES = 1

    seen = {}

    def create(**kwargs):
        seen["model"] = kwargs["model"]
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content="{}", refusal=None),
                finish_reason="stop")],
            usage=types.SimpleNamespace(prompt_tokens=1, completion_tokens=1,
                                        prompt_tokens_details=None),
            model="gpt-5.4-mini")

    a.openai_client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))

    a._make_openai_api_call("p", "r", model="gpt-5.6-solar",
                            purpose=usage.PURPOSE_SECOND_PASS)
    assert seen["model"] == "gpt-5.4-mini"

    a._make_openai_api_call("p", "r", model="gpt-5.6-solar",
                            purpose=usage.PURPOSE_EXTRACTION)
    assert seen["model"] == "gpt-5.6-solar"


# --------------------------------------------------------- cached pricing
def test_cached_tokens_are_billed_at_the_cached_rate():
    """Cached prompt tokens cost about a tenth of fresh input. On a real run
    88% of prompt tokens were cached."""
    tracker = usage.UsageTracker()
    tracker.record("gpt-5.2", usage.PURPOSE_EXTRACTION, _usage(1_000_000, 0, cached=900_000))
    prices = {"gpt-5.2": {"in": 1.75, "cachad": 0.175, "ut": 14.00}}
    # 100k fresh at 1.75 + 900k cached at 0.175
    assert tracker.cost(prices) == pytest.approx(0.1 * 1.75 + 0.9 * 0.175)


def test_a_missing_cached_rate_falls_back_to_the_input_rate():
    tracker = usage.UsageTracker()
    tracker.record("gpt-5.2", usage.PURPOSE_EXTRACTION, _usage(1_000_000, 0, cached=500_000))
    assert tracker.cost({"gpt-5.2": {"in": 2.0, "ut": 1.0}}) == pytest.approx(2.0)


def test_a_dated_model_name_matches_its_price_key():
    """The API answers with "gpt-5.2-2025-12-11"; the price list says
    "gpt-5.2"."""
    prices = {"gpt-5.2": {"in": 1.75, "cachad": 0.175, "ut": 14.0}}
    assert usage.UsageTracker.price_for("gpt-5.2-2025-12-11", prices) is not None
    assert usage.UsageTracker.price_for("gpt-4o", prices) is None


def test_the_longest_matching_key_wins():
    """"gpt-5.4-mini" must not be priced as "gpt-5.4"."""
    prices = {"gpt-5.4": {"in": 2.5, "ut": 15.0}, "gpt-5.4-mini": {"in": 0.75, "ut": 4.5}}
    assert usage.UsageTracker.price_for("gpt-5.4-mini", prices)["in"] == 0.75


def test_the_shipped_price_list_covers_every_model_in_the_form():
    root = Path(__file__).resolve().parents[1]
    prices = json.loads(
        (root / "app" / "config" / "model_prices.json").read_text(encoding="utf-8")
    )
    for model in usage.known_models():
        assert usage.UsageTracker.price_for(model, prices), f"no price for {model}"


def test_every_price_entry_has_all_three_rates():
    root = Path(__file__).resolve().parents[1]
    prices = json.loads(
        (root / "app" / "config" / "model_prices.json").read_text(encoding="utf-8")
    )
    for model, entry in prices.items():
        if model.startswith("_"):
            continue
        assert set(entry) == {"in", "cachad", "ut"}, model


def test_pro_models_never_understate_the_cost():
    """No published cached rate, so they fall back to the full input rate."""
    root = Path(__file__).resolve().parents[1]
    prices = json.loads(
        (root / "app" / "config" / "model_prices.json").read_text(encoding="utf-8")
    )
    for model in ("gpt-5.5-pro", "gpt-5.4-pro", "gpt-5.2-pro", "gpt-5-pro"):
        assert prices[model]["cachad"] == prices[model]["in"], model


def test_the_dropdown_shows_the_price_of_each_choice():
    """The cost of a model should be visible where the model is chosen."""
    options = [o for _, opts in usage.model_choices() for o in opts]
    assert len(options) == len(usage.known_models()) >= 16
    for value, label in options:
        assert value in label, f"{value} not named in its own label"
        assert "USD" in label, f"no price shown for {value}"


def test_specialised_models_are_not_offered():
    """codex, search and cyber variants are not general extraction models."""
    for excluded in ("codex", "cyber", "search-api", "realtime", "audio"):
        assert not any(excluded in m for m in usage.known_models()), excluded


# ----------------------------------------------------- roles from the form
def test_choices_are_persisted_and_come_back_selected(tmp_path, monkeypatch):
    path = tmp_path / "roles.json"
    monkeypatch.setenv("JBG_MODEL_ROLES", str(path))

    usage.save_roles({"extraktion": "gpt-5.6-solar", "omsokning": "gpt-5.4-mini",
                      "stabilitet": "gpt-5.6-terra"})
    selected = usage.selected_roles()
    assert selected["extraktion"] == "gpt-5.6-solar"
    assert selected["omsokning"] == "gpt-5.4-mini"
    assert selected["stabilitet"] == "gpt-5.6-terra"
    # and the analyzer reads the same file
    assert usage.load_roles()[usage.PURPOSE_SECOND_PASS] == "gpt-5.4-mini"


def test_saving_keeps_roles_the_form_does_not_offer(tmp_path, monkeypatch):
    """aar and sidnummer are configured by hand and must survive a save."""
    path = tmp_path / "roles.json"
    path.write_text(json.dumps({
        "_kommentar": ["behåll mig"], "aar": "gpt-5-nano", "sidnummer": "gpt-5-mini",
    }), encoding="utf-8")
    monkeypatch.setenv("JBG_MODEL_ROLES", str(path))

    usage.save_roles({"extraktion": "gpt-5.2", "omsokning": "", "stabilitet": ""})
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["aar"] == "gpt-5-nano"
    assert saved["sidnummer"] == "gpt-5-mini"
    assert saved["_kommentar"] == ["behåll mig"]


def test_an_unknown_model_is_not_written(tmp_path, monkeypatch):
    """The value arrives from a form field and ends up in a config file."""
    path = tmp_path / "roles.json"
    monkeypatch.setenv("JBG_MODEL_ROLES", str(path))
    usage.save_roles({"extraktion": "gpt-9-imaginary"})
    assert json.loads(path.read_text(encoding="utf-8")).get("extraktion") is None


def test_an_unrecognised_saved_value_falls_back_to_the_default(tmp_path, monkeypatch):
    path = tmp_path / "roles.json"
    path.write_text(json.dumps({"extraktion": "gpt-9-imaginary"}), encoding="utf-8")
    monkeypatch.setenv("JBG_MODEL_ROLES", str(path))
    assert usage.selected_roles()["extraktion"] == usage.DEFAULT_MODEL


def test_every_role_defaults_to_the_same_model(tmp_path, monkeypatch):
    monkeypatch.setenv("JBG_MODEL_ROLES", str(tmp_path / "absent.json"))
    assert set(usage.selected_roles().values()) == {usage.DEFAULT_MODEL}
