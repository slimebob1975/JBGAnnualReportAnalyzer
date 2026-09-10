"""Tests for Step 4: canonical fund names, arithmetic validation, and the
certainty and comment fields reaching the exports."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.src import JBGValidation as validation  # noqa: E402
from app.src.JBGFundNames import (  # noqa: E402
    FundNameResolver,
    normalise_result_fund_names,
)

ROOT = Path(__file__).resolve().parents[1]
KASSOR = ROOT / "app" / "src" / "json" / "kassor.json"
KEY_DEFS = ROOT / "app" / "prompt" / "json" / "nyckeltalsdefinitioner.json"


@pytest.fixture(scope="module")
def resolver():
    return FundNameResolver(KASSOR)


# ------------------------------------------------------------- fund names
@pytest.mark.parametrize(
    "reported, expected_short",
    [
        # Every one of these came out of a real run and none of them matched
        # the old exact-dict lookup.
        ("Byggnads a-kassa", "Byggnadsarbetarnas"),
        ("Lärarnas a-kassa", "Lärarnas"),
        ("Journalisternas Arbetslöshetskassa", "Journalisternas"),
        ("Finans- och Försäkringsbranschens Arbetslöshetskassa", "Finans- och Försäkringsbranschens"),
        ("Livsmedelsarbetarnas", "Livsmedelsarbetarnas"),
        ("Fastighets", "Fastighets"),
        ("Kommunalarbetarnas", "Kommunalarbetarnas"),
        # other spellings the cover pages use
        ("Arbetslöshetskassan Handels", "Handels"),
        ("Handels a-kassa", "Handels"),
        ("Unionens Arbetslöshetskassa", "Unionens"),
        ("IF Metalls a-kassa", "IF Metalls"),
        ("Akademikernas erkända arbetslöshetskassa", "Akademikernas"),
        ("Arbetslöshetskassan för service och kommunikation", "Sekos"),
    ],
)
def test_real_reported_names_resolve(resolver, reported, expected_short):
    assert resolver.short_name(reported) == expected_short


def test_case_difference_alone_used_to_break_the_lookup(resolver):
    a = resolver.canonical_name("Journalisternas Arbetslöshetskassa")
    b = resolver.canonical_name("Journalisternas arbetslöshetskassa")
    assert a == b == "Journalisternas arbetslöshetskassa"


@pytest.mark.parametrize("unknown", ["Något Okänt a-kassa", "Fiktiva yrkens a-kassa", ""])
def test_unknown_names_are_left_alone_not_guessed(resolver, unknown):
    """A wrong canonical name is worse than an unnormalised one."""
    entry, how = resolver.resolve(unknown)
    assert entry is None, how
    assert resolver.canonical_name(unknown) == unknown


def test_every_official_and_short_name_resolves_to_itself(resolver):
    entries = json.loads(KASSOR.read_text(encoding="utf-8"))
    for entry in entries:
        for spelling in (entry["Officiellt namn"], entry["Kort namn"]):
            assert resolver.canonical_name(spelling) == entry["Officiellt namn"], spelling


def test_two_spellings_of_one_fund_are_merged():
    result = {
        "Journalisternas Arbetslöshetskassa": {"2023": {"Summa eget kapital": {"värde": 1}}},
        "Journalisternas arbetslöshetskassa": {"2024": {"Summa eget kapital": {"värde": 2}}},
    }
    merged, unresolved = normalise_result_fund_names(result, KASSOR)
    assert unresolved == []
    assert list(merged) == ["Journalisternas arbetslöshetskassa"]
    assert set(merged["Journalisternas arbetslöshetskassa"]) == {"2023", "2024"}


def test_unresolved_names_are_reported_back():
    result = {"Helt Okänd kassa": {"2023": {}}}
    merged, unresolved = normalise_result_fund_names(result, KASSOR)
    assert unresolved == ["Helt Okänd kassa"]
    assert "Helt Okänd kassa" in merged


def test_missing_fund_register_is_not_fatal(tmp_path):
    result = {"X": {"2023": {}}}
    merged, unresolved = normalise_result_fund_names(result, tmp_path / "nope.json")
    assert merged == result


# ------------------------------------------------------------- validation
def _fund(**metrics):
    return {"K": {"2023": {k: {"värde": v} for k, v in metrics.items()}}}


def test_balanced_balance_sheet_produces_no_findings():
    result = _fund(
        **{"Summa tillgångar": 63853, "Summa eget kapital": 16339, "Summa skulder": 42401,
           "Summa avsättningar": 5113}
    )
    assert validation.validate(result) == []


def test_a_balance_sheet_that_does_not_add_up_is_caught():
    """Neither EK+S nor EK+S+A equals BO, so a figure is genuinely wrong."""
    result = _fund(
        **{"Summa tillgångar": 45776, "Summa eget kapital": 20000, "Summa skulder": 20000,
           "Summa avsättningar": 2867}
    )
    findings = validation.validate(result)
    assert len(findings) == 1
    assert findings[0].rule == "Balansräkningen balanserar"
    assert findings[0].severity == validation.SEVERITY_ERROR
    assert "differens" in findings[0].message


def test_rounding_in_tkr_does_not_trip_the_check():
    result = _fund(
        **{"Summa tillgångar": 730143, "Summa eget kapital": 300000, "Summa skulder": 430142,
           "Summa avsättningar": 0}
    )
    assert validation.validate(result) == []


def test_missing_metrics_skip_the_rule_rather_than_failing_it():
    """The model is told to omit what it cannot find, so absence is normal."""
    result = _fund(**{"Summa tillgångar": 63853, "Summa eget kapital": 16339})
    assert validation.validate(result) == []


def test_subtotals_are_checked_from_the_definitions(tmp_path):
    """The föreskrift states every subtotal, so the arithmetic is data: one
    generic rule replaces a hand-written check per identity."""
    built = validation.rules_from_definitions(KEY_DEFS)
    assert len(built) >= 20
    names = {r.name for r in built}
    assert "Delsummering: Summa tillgångar" in names
    assert "Delsummering: Summa intäkter" in names


def test_a_subtotal_that_does_not_add_up_is_reported():
    result = _fund(**{"Summa intäkter": 100000, "Medlemsavgifter": 60000,
                      "Övriga intäkter": 30000})
    findings = validation.validate(result, KEY_DEFS)
    assert any("Summa intäkter" in f.message for f in findings)


def test_a_correct_subtotal_is_silent():
    result = _fund(**{"Summa intäkter": 90000, "Medlemsavgifter": 60000,
                      "Övriga intäkter": 30000})
    assert validation.validate(result, KEY_DEFS) == []


def test_a_note_with_the_opposite_sign_is_named_as_a_convention(tmp_path):
    """Four of six funds in the first subset tripped this check with a note
    equal to its statement row but negative. The definitions ask for exactly
    that: the row is normalised positive, the note is reported as printed.
    Calling it a difference of twice the amount described the rule, not the
    document."""
    result = _fund(**{"Not till Kostnad arbetslöshetsersättning: Summa": -2923688,
                      "Kostnad arbetslöshetsersättning": 2923688})
    findings = validation.validate(result, KEY_DEFS)
    assert len(findings) == 1
    assert "omvänt tecken" in findings[0].message
    assert "differens" not in findings[0].message


def test_a_note_that_is_genuinely_wrong_is_still_reported():
    result = _fund(**{"Not till Övriga fordringar: Summa": 1109,
                      "Övriga fordringar": 363})
    findings = validation.validate(result, KEY_DEFS)
    assert len(findings) == 1
    assert "differens" in findings[0].message
    assert "omvänt tecken" not in findings[0].message


def test_kronor_read_where_tkr_was_expected_is_named_as_a_scale_error():
    """Småföretagarnas Finansieringsavgift came back as 117 308 746 against a
    Summa avgifter till staten of 117 309: one figure off the resultaträkning
    in tkr, the other out of the förvaltningsberättelse in kronor."""
    result = _fund(**{"Summa avgifter till staten": 117309,
                      "Finansieringsavgift": 117308746})
    findings = validation.validate(result, KEY_DEFS)
    assert len(findings) == 1
    assert "Skalfel" in findings[0].message
    assert "tusental kronor" in findings[0].message


def test_a_thousandfold_difference_that_is_not_a_scale_error_stays_a_difference():
    """A factor of a thousand only means a unit mix-up when the digits match."""
    result = _fund(**{"Summa avgifter till staten": 117309,
                      "Finansieringsavgift": 954100000})
    findings = validation.validate(result, KEY_DEFS)
    assert len(findings) == 1
    assert "Skalfel" not in findings[0].message


def test_negative_cost_is_flagged_since_the_prompt_asks_for_positives():
    result = _fund(**{"Summa administrationskostnader": -58493})
    findings = validation.validate(result)
    assert len(findings) == 1
    assert findings[0].severity == validation.SEVERITY_WARNING


def test_string_values_are_still_checked():
    result = {"K": {"2023": {
        "Summa tillgångar": {"värde": "63 853"},
        "Summa eget kapital": {"värde": "16339"},
        "Summa skulder": {"värde": "42401"},
        "Summa avsättningar": {"värde": "5113"},
    }}}
    assert validation.validate(result) == []


def test_malformed_result_does_not_raise():
    for junk in [None, {}, {"K": None}, {"K": {"2023": None}}, {"K": {"2023": {"M": 5}}}]:
        assert validation.validate(junk) == []


def test_findings_index_by_cell():
    result = _fund(**{"Summa tillgångar": 100, "Summa eget kapital": 1, "Summa skulder": 1,
                      "Summa avsättningar": 1})
    index = validation.findings_by_cell(validation.validate(result))
    assert ("K", "2023", "Summa tillgångar") in index
    assert ("K", "2023", "Summa skulder") in index


# ----------------------------------------------------------------- export
@pytest.fixture
def sample_json(tmp_path):
    data = {
        "Journalisternas arbetslöshetskassa": {
            "2024": {
                "Summa tillgångar": {
                    "värde": 15959, "källa": "Sida 10, Balansräkning",
                    "säkerhet": 1.0, "kommentar": "Summa tillgångar.",
                },
                "Summa eget kapital": {
                    "värde": 9000, "källa": "Sida 10",
                    "säkerhet": 0.45, "kommentar": "Osäker tolkning.",
                },
            }
        }
    }
    path = tmp_path / "r.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_csv_now_carries_certainty_and_comment(sample_json, tmp_path):
    from app.src.JBGJSONConverter import JsonConverter

    out = tmp_path / "out.csv"
    JsonConverter(sample_json, include_sources=True).to_csv(out)
    header = out.read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.split(";") == [
        "Fund", "Year", "Key", "Value", "Source", "Certainty", "Comment", "Validering",
    ]


def test_csv_without_sources_stays_minimal(sample_json, tmp_path):
    from app.src.JBGJSONConverter import JsonConverter

    out = tmp_path / "out.csv"
    JsonConverter(sample_json, include_sources=False).to_csv(out)
    header = out.read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.split(";") == ["Fund", "Year", "Key", "Value"]


def test_excel_shades_by_certainty_and_attaches_notes(sample_json, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from app.src.JBGJSONConverter import JsonConverter

    out = tmp_path / "out.xlsx"
    JsonConverter(sample_json, include_sources=True).to_excel_by_year(
        out, key_def_path=KEY_DEFS, fund_names=KASSOR, findings=[]
    )

    wb = openpyxl.load_workbook(out)
    assert "Läsanvisning" in wb.sheetnames
    ws = wb["2024"]
    # short name in the header, resolved through the fund register
    assert ws.cell(row=1, column=2).value == "Journalisternas"

    cells = {r[0].value: r for r in ws.iter_rows(min_row=2)}
    high = cells["Summa tillgångar"][1]
    low = cells["Summa eget kapital"][1]
    assert high.fill.start_color.rgb.endswith("C6EFCE")   # high certainty, green
    assert low.fill.start_color.rgb.endswith("FFC7CE")    # low certainty, red
    assert "Summa tillgångar" in high.comment.text
    assert "Säkerhet: 1.0" in high.comment.text


def test_excel_marks_cells_named_in_a_finding(sample_json, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from app.src.JBGJSONConverter import JsonConverter

    finding = validation.Finding(
        fund="Journalisternas arbetslöshetskassa",
        year="2024",
        rule="Balansräkningen balanserar",
        message="Testanmärkning",
        severity=validation.SEVERITY_ERROR,
        metrics=["Summa tillgångar"],
    )
    out = tmp_path / "out.xlsx"
    JsonConverter(sample_json, include_sources=True).to_excel_by_year(
        out, key_def_path=KEY_DEFS, fund_names=KASSOR, findings=[finding]
    )

    wb = openpyxl.load_workbook(out)
    ws = wb["2024"]
    cells = {r[0].value: r for r in ws.iter_rows(min_row=2)}
    flagged = cells["Summa tillgångar"][1]
    # the flag colour wins over the certainty colour
    assert flagged.fill.start_color.rgb.endswith("E1BEE7")
    assert "Testanmärkning" in flagged.comment.text
    assert "Testanmärkning" in "".join(
        str(c) for row in wb["Läsanvisning"].iter_rows(values_only=True) for c in row if c
    )


# ------------------------------------------------------- packaging: no pandas
def test_csv_export_needs_no_pandas(sample_json, tmp_path, monkeypatch):
    """pandas was a hard dependency of the whole package purely so that to_csv
    could write a semicolon-separated file. It is optional now."""
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "pandas" or name.startswith("pandas."):
            raise ImportError("No module named 'pandas'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)

    from app.src.JBGJSONConverter import JsonConverter

    out = tmp_path / "out.csv"
    JsonConverter(sample_json, include_sources=True).to_csv(out)
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].split(";") == [
        "Fund", "Year", "Key", "Value", "Source", "Certainty", "Comment", "Validering",
    ]
    assert len(lines) == 3  # header plus two metrics


def test_csv_values_are_not_mangled(sample_json, tmp_path):
    from app.src.JBGJSONConverter import JsonConverter

    out = tmp_path / "out.csv"
    JsonConverter(sample_json, include_sources=True).to_csv(out)
    rows = out.read_text(encoding="utf-8-sig").splitlines()[1:]
    values = {r.split(";")[2]: r.split(";")[3] for r in rows}
    assert values["Summa tillgångar"] == "15959"
    assert values["Summa eget kapital"] == "9000"


def test_dataframe_still_works_when_pandas_is_present(sample_json):
    pytest.importorskip("pandas")
    from app.src.JBGJSONConverter import JsonConverter

    df = JsonConverter(sample_json, include_sources=True).to_dataframe()
    assert list(df.columns) == [
        "Fund", "Year", "Key", "Value", "Source", "Certainty", "Comment", "Validering",
    ]
    assert len(df) == 2


# ------------------------------------------ findings in every output format
@pytest.fixture
def flagged_json(tmp_path):
    """A result whose balance sheet does not balance, with the finding
    recorded in the file the way do_analysis now writes it."""
    result = {
        "Journalisternas arbetslöshetskassa": {
            "2024": {
                "Summa tillgångar": {"värde": 45776, "källa": "Sida 15",
                                     "säkerhet": 1.0, "kommentar": "Summa tillgångar."},
                "Summa eget kapital": {"värde": 29179, "källa": "Sida 16",
                                 "säkerhet": 1.0, "kommentar": "."},
                "Summa skulder": {"värde": 16597, "källa": "Sida 16",
                            "säkerhet": 1.0, "kommentar": "."},
                "Summa avsättningar": {"värde": 2867, "källa": "Not 9",
                                          "säkerhet": 0.8, "kommentar": "."},
            }
        }
    }
    findings = validation.validate(result)
    assert len(findings) == 1, "fixture should trip the balance check"
    result["_rimlighetskontroller"] = [f.as_dict() for f in findings]

    path = tmp_path / "r.json"
    path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return path


def test_metadata_key_is_not_treated_as_a_fund(flagged_json):
    from app.src.JBGJSONConverter import JsonConverter

    conv = JsonConverter(flagged_json, include_sources=True)
    assert "_rimlighetskontroller" not in conv._funds()
    assert list(conv._funds()) == ["Journalisternas arbetslöshetskassa"]
    assert len(conv.findings()) == 1


def test_csv_names_the_failed_check_per_cell(flagged_json, tmp_path):
    """Choosing CSV used to lose the checks entirely; they were only logged."""
    from app.src.JBGJSONConverter import JsonConverter

    out = tmp_path / "out.csv"
    JsonConverter(flagged_json, include_sources=True).to_csv(out)
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].endswith("Validering")

    flagged = [line for line in lines[1:] if line.rsplit(";", 1)[-1]]
    keys = {line.split(";")[2] for line in flagged}
    assert keys == {"Summa tillgångar", "Summa eget kapital", "Summa skulder", "Summa avsättningar"}
    assert all("Balansräkningen balanserar" in line for line in flagged)


def test_unflagged_rows_have_an_empty_validation_column(flagged_json, tmp_path):
    from app.src.JBGJSONConverter import JsonConverter

    out = tmp_path / "out.csv"
    JsonConverter(flagged_json, include_sources=True).to_csv(out)
    # every metric in this fixture is named by the one finding, so add a
    # second fund that is fine and check it stays blank
    data = json.loads(flagged_json.read_text(encoding="utf-8"))
    data["Fastighets arbetslöshetskassa"] = {
        "2024": {"Summa tillgångar": {"värde": 1, "källa": "s", "säkerhet": 1, "kommentar": "."}}
    }
    flagged_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    JsonConverter(flagged_json, include_sources=True).to_csv(out)
    rows = out.read_text(encoding="utf-8-sig").splitlines()[1:]
    fine = [r for r in rows if r.startswith("Fastighets")]
    assert fine and all(r.endswith(";") for r in fine)


def test_excel_recovers_findings_from_the_result_file(flagged_json, tmp_path):
    """Exporting an existing JSON keeps the flags even when no findings list
    is passed in."""
    openpyxl = pytest.importorskip("openpyxl")
    from app.src.JBGJSONConverter import JsonConverter

    out = tmp_path / "out.xlsx"
    JsonConverter(flagged_json, include_sources=True).to_excel_by_year(
        out, key_def_path=KEY_DEFS, fund_names=KASSOR  # note: no findings=
    )
    wb = openpyxl.load_workbook(out)
    ws = wb["2024"]
    cells = {r[0].value: r[1] for r in ws.iter_rows(min_row=2)}
    assert cells["Summa tillgångar"].fill.start_color.rgb.endswith("E1BEE7")
    assert "Balansräkningen balanserar" in cells["Summa tillgångar"].comment.text
    # a high-certainty value that is not flagged keeps its own colour
    assert cells["Summa eget kapital"].fill.start_color.rgb.endswith("E1BEE7")


# --------------------------------------------------- certainty calibration
def test_certainty_histogram_counts_the_three_levels():
    result = {"K": {"2023": {
        "a": {"värde": 1, "säkerhet": "explicit"},
        "b": {"värde": 1, "säkerhet": "explicit"},
        "c": {"värde": 1, "säkerhet": "härledd"},
        "d": {"värde": 1, "säkerhet": "osäker"},
        "e": {"värde": 1},
    }}}
    assert validation.certainty_histogram(result) == {
        "explicit": 2, "härledd": 1, "osäker": 1, "saknas": 1
    }


def test_histogram_maps_legacy_floats_onto_the_levels():
    """Result files produced before the enum still contain numbers."""
    result = {"K": {"2023": {
        "a": {"värde": 1, "säkerhet": 1.0},
        "b": {"värde": 1, "säkerhet": 0.95},
        "c": {"värde": 1, "säkerhet": 0.6},
        "d": {"värde": 1, "säkerhet": 0.2},
    }}}
    assert validation.certainty_histogram(result) == {
        "explicit": 2, "härledd": 1, "osäker": 1, "saknas": 0
    }


def test_histogram_ignores_the_metadata_key():
    result = {
        "K": {"2023": {"a": {"värde": 1, "säkerhet": 1.0}}},
        "_rimlighetskontroller": [{"kassa": "K"}],
    }
    assert sum(validation.certainty_histogram(result).values()) == 1


def test_no_warning_when_almost_everything_is_explicit(caplog):
    """A real run was 98% explicit and the three exceptions were exactly the
    values worth checking. For a lookup task against structured statements
    that is the expected outcome, not a failure of the scale."""
    metrics = {str(i): {"värde": 1, "säkerhet": "explicit"} for i in range(117)}
    metrics.update({f"h{i}": {"värde": 1, "säkerhet": "härledd"} for i in range(2)})
    metrics["o"] = {"värde": 1, "säkerhet": "osäker"}

    with caplog.at_level("INFO"):
        validation.log_certainty_histogram({"K": {"2024": metrics}})
    assert any("explicit: 117" in r.message for r in caplog.records)
    assert not any(r.levelname == "WARNING" for r in caplog.records)


def test_warning_only_when_the_scale_has_no_variation_at_all(caplog):
    metrics = {str(i): {"värde": 1, "säkerhet": "explicit"} for i in range(30)}
    with caplog.at_level("INFO"):
        validation.log_certainty_histogram({"K": {"2024": metrics}})
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings and "samma säkerhetsnivå" in warnings[0].message


def test_no_warning_on_a_small_sample(caplog):
    """A handful of values sharing a level means a small run, not a broken
    scale."""
    metrics = {str(i): {"värde": 1, "säkerhet": "explicit"} for i in range(5)}
    with caplog.at_level("INFO"):
        validation.log_certainty_histogram({"K": {"2024": metrics}})
    assert not any(r.levelname == "WARNING" for r in caplog.records)


def test_skulder_definition_states_the_exclusive_convention():
    """The prompt has to tell the model what to do when only
    "Summa avsättningar och skulder" is presented."""
    definitions = json.loads(KEY_DEFS.read_text(encoding="utf-8"))
    skulder = next(d for d in definitions if d["Nyckeltal"] == "Summa skulder")
    instructions = skulder["Specifika instruktioner"]
    assert "EXKLUSIVE avsättningar" in instructions
    assert "Summa avsättningar och skulder" in instructions


# ------------------------------------------------- fund aliases as data
def test_alias_from_the_register_resolves():
    """A real report wrote "Industrifacket Metalls arbetslöshetskassa" where
    the register has "IF Metalls arbetslöshetskassa"."""
    resolver = FundNameResolver(KASSOR)
    assert resolver.short_name("Industrifacket Metalls arbetslöshetskassa") == "IF Metalls"
    assert resolver.canonical_name("IF Metalls a-kassa") == "IF Metalls arbetslöshetskassa"


def test_aliases_live_in_the_register_not_in_code():
    """Adding a spelling must be a data change, so the mechanism has to be
    generic rather than a per-fund branch."""
    entries = json.loads(KASSOR.read_text(encoding="utf-8"))
    with_alias = [e for e in entries if e.get("Alternativa namn")]
    assert with_alias, "expected at least one alias in kassor.json"
    for entry in with_alias:
        assert isinstance(entry["Alternativa namn"], list)

    # No fund name may appear as a string literal in the executable code of
    # the resolver: comments and docstrings are fine, branches are not.
    import ast

    tree = ast.parse((ROOT / "app" / "src" / "JBGFundNames.py").read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)

    literals = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value not in docstrings
    ]
    for entry in entries:
        for name in [entry["Officiellt namn"], entry["Kort namn"]]:
            assert not any(name in lit for lit in literals), name


def test_entries_without_aliases_still_work():
    resolver = FundNameResolver(KASSOR)
    assert resolver.short_name("Byggnadsarbetarnas arbetslöshetskassa") == "Byggnadsarbetarnas"


# ------------------------------------------- the expanded specification
def test_metric_names_are_unique():
    """They are an enum in the response schema. The source document reuses
    "SUMMA" eight times, plus "Övriga fordringar", "Övriga skulder",
    "Övriga externa kostnader", "Källskatt arbetslöshetsersättning",
    "Årets avstående från återkrav" and "Antal beslut" twice each."""
    names = [d["Nyckeltal"] for d in json.loads(KEY_DEFS.read_text(encoding="utf-8"))]
    assert len(names) == len(set(names)), "duplicate metric name"


def test_all_four_sections_of_the_foreskrift_are_covered():
    definitions = json.loads(KEY_DEFS.read_text(encoding="utf-8"))
    groups = {d["Grupp"] for d in definitions}
    assert len(groups) == 4
    assert any("Resultaträkning" in g for g in groups)
    assert any("Balansräkning" in g for g in groups)
    assert any("Noter" in g for g in groups)
    assert any("bilaga 2" in g for g in groups)


def test_note_items_are_named_by_subject_not_by_number():
    """Funds add notes of their own, so their note 7 is rarely the
    föreskrift's note 7. Naming by subject keeps the metric stable whatever
    the report numbers it."""
    definitions = json.loads(KEY_DEFS.read_text(encoding="utf-8"))
    note_names = [d["Nyckeltal"] for d in definitions if "Noter" in d["Grupp"]]
    assert note_names, "expected note metrics"
    assert all(n.startswith("Not till ") for n in note_names)
    import re

    assert not any(re.match(r"^Not \d", n) for n in note_names), "no bare note numbers"
    names = set(d["Nyckeltal"] for d in definitions)
    # the pairs that collide in the source document
    assert "Övriga fordringar" in names
    assert "Not till Övriga fordringar: Övriga fordringar" in names
    assert "Övriga skulder" in names
    assert "Not till Övriga skulder: Övriga skulder" in names
    assert "Övriga externa kostnader" in names
    assert "Not till Övriga externa kostnader: Övriga externa kostnader" in names


def test_eftergift_is_an_alias_for_avstaende():
    """Some funds call it eftergift. The term is being phased out but means
    the same thing."""
    definitions = json.loads(KEY_DEFS.read_text(encoding="utf-8"))
    entry = next(
        d for d in definitions
        if d["Nyckeltal"].endswith("Årets avstående från återkrav")
        and "Fordringar" in d["Nyckeltal"]
    )
    assert "Eftergift" in entry["Alternativa benämningar"]


def test_every_subtotal_references_metrics_that_exist():
    definitions = json.loads(KEY_DEFS.read_text(encoding="utf-8"))
    names = {d["Nyckeltal"] for d in definitions}
    for entry in definitions:
        for component in entry.get("Delposter", {}):
            assert component in names, f"{entry['Nyckeltal']} -> {component}"


def test_the_prompt_forbids_extra_items_and_comparison_years():
    """Two explicit instructions: the föreskrift is a minimum and funds may add
    rows, which must not be reported; and only the current year counts."""
    text = " ".join(
        (ROOT / "app" / "prompt" / "GPT-instruktioner_komprimerad.md")
        .read_text(encoding="utf-8").split()
    )
    assert "Lägg inte till egna poster" in text
    assert "jämförelseår" in text.lower()
    assert "Endast innevarande räkenskapsår" in text


# --------------------------------------------------- derived subtotals
def _analyzer_for_derivation():
    from app.src.JBGAnnualReportAnalysis import JBGAnnualReportAnalyzer

    a = JBGAnnualReportAnalyzer.__new__(JBGAnnualReportAnalyzer)
    a.metrics_path = KEY_DEFS
    return a


def _cell(value):
    return {"värde": value, "källa": "Sida 5", "säkerhet": "explicit", "kommentar": "c"}


def test_a_missing_subtotal_is_computed_from_its_components():
    """FINANSIELLA POSTER is only a heading; the two figures below it are what
    the report states, and the summing row follows from them."""
    result = {"K": {"2025": {"Finansiella intäkter": _cell(500),
                             "Finansiella kostnader": _cell(120)}}}
    assert _analyzer_for_derivation()._derive_missing_subtotals(result) == 1
    entry = result["K"]["2025"]["Summa finansiella poster"]
    assert entry["värde"] == 380
    assert entry["källa"] == "Beräknad ur delposter"
    assert entry["säkerhet"] == "härledd"
    assert entry["kommentar"].startswith("[Beräknad]")


def test_derivation_chains_through_dependent_subtotals():
    """Summa tillgångar needs Summa anläggningstillgångar, which needs its own
    components, so one pass is not enough."""
    result = {"K": {"2025": {
        "Immateriella anläggningstillgångar": _cell(0),
        "Materiella anläggningstillgångar": _cell(1000),
        "Andra långfristiga värdepappersinnehav": _cell(19000),
        "Medlemsavgifter": _cell(60000),
        "Övriga intäkter": _cell(30000),
        "Personalkostnader": _cell(40000),
        "Övriga externa kostnader": _cell(15000),
        "Avskrivningar": _cell(5000),
    }}}
    _analyzer_for_derivation()._derive_missing_subtotals(result)
    metrics = result["K"]["2025"]
    assert metrics["Summa anläggningstillgångar"]["värde"] == 20000
    assert metrics["Summa intäkter"]["värde"] == 90000
    # depends on two subtotals that were themselves derived
    assert metrics["Resultat före avgifter till staten"]["värde"] == 30000


def test_a_stated_subtotal_is_never_overwritten():
    """If the report says it, that is what we report, even if the components
    do not add up — the sum check exists to surface that."""
    result = {"K": {"2025": {"Finansiella intäkter": _cell(500),
                             "Finansiella kostnader": _cell(120),
                             "Summa finansiella poster": _cell(999)}}}
    assert _analyzer_for_derivation()._derive_missing_subtotals(result) == 0
    assert result["K"]["2025"]["Summa finansiella poster"]["värde"] == 999


def test_nothing_is_derived_from_incomplete_components():
    result = {"K": {"2025": {"Finansiella intäkter": _cell(500)}}}
    assert _analyzer_for_derivation()._derive_missing_subtotals(result) == 0
    assert "Summa finansiella poster" not in result["K"]["2025"]


def test_signs_are_respected():
    """Resultat före avgifter till staten is intäkter MINUS kostnader."""
    result = {"K": {"2025": {"Summa intäkter": _cell(90000),
                             "Summa administrationskostnader": _cell(60000)}}}
    _analyzer_for_derivation()._derive_missing_subtotals(result)
    assert result["K"]["2025"]["Resultat före avgifter till staten"]["värde"] == 30000


# ------------------------------------- a note must agree with its statement row
def test_a_note_total_must_equal_the_row_it_explains():
    """Nine of the ten notes specify one row of the statements. Not 2 is
    excluded: medelantal anställda is a headcount, not an amount."""
    built = validation.rules_from_definitions(KEY_DEFS)
    links = [r for r in built if r.name.startswith("Not mot räkning")]
    assert len(links) == 9
    assert not any("Personalkostnader" in r.name for r in links)


def test_a_note_that_disagrees_with_its_row_is_reported():
    """A real case: Not 1 came back as 1 338 451 while Övriga intäkter was 193."""
    result = _fund(**{"Not till Övriga intäkter: Summa": 1338451, "Övriga intäkter": 193})
    findings = validation.validate(result, KEY_DEFS)
    assert any(f.rule == "Not mot räkning: Övriga intäkter" for f in findings)


def test_a_matching_note_is_silent():
    result = _fund(**{"Not till Övriga intäkter: Summa": 431, "Övriga intäkter": 431})
    assert validation.validate(result, KEY_DEFS) == []


def test_the_tolerance_is_one_krona():
    """A relative tolerance let 730 000 pass on a 730 million balance sheet."""
    assert validation.ABSOLUTE_TOLERANCE == 1.0
    assert validation.RELATIVE_TOLERANCE == 0.0
    ok = _fund(**{"Not till Övriga intäkter: Summa": 431, "Övriga intäkter": 432})
    assert validation.validate(ok, KEY_DEFS) == []
    bad = _fund(**{"Not till Övriga intäkter: Summa": 431, "Övriga intäkter": 433})
    assert validation.validate(bad, KEY_DEFS)


def test_an_inverted_net_is_diagnosed_as_such():
    """A net posted without its sign is a different fault from a wrong figure."""
    result = _fund(**{"Summa finansiella poster": -6158,
                      "Finansiella intäkter": 6159, "Finansiella kostnader": 1})
    findings = validation.validate(result, KEY_DEFS)
    assert any("omvänt tecken" in f.message for f in findings)


def test_the_result_chain_is_checked_end_to_end():
    """Årets resultat had no check at all until now."""
    names = {r.name for r in validation.rules_from_definitions(KEY_DEFS)}
    for target in ("Resultat före avgifter till staten",
                   "Resultat före finansiella poster",
                   "Resultat före poster arbetslöshetsförsäkringen",
                   "Årets resultat"):
        assert f"Delsummering: {target}" in names, target


def test_the_updated_specification_wording_is_used():
    """IAFFS 2026:1 renamed two balance sheet rows. The old names stay as
    aliases, because reports written to the previous wording still exist."""
    definitions = json.loads(KEY_DEFS.read_text(encoding="utf-8"))
    by_name = {d["Nyckeltal"]: d for d in definitions}

    assert "Fordringar medlemsavgifter" in by_name
    assert "Fordringar medlemsavgift" in by_name["Fordringar medlemsavgifter"][
        "Alternativa benämningar"
    ]

    assert "Andra kortfristiga placeringar" in by_name
    assert "Övriga kortfristiga placeringar" in by_name["Andra kortfristiga placeringar"][
        "Alternativa benämningar"
    ]

    # and nothing still refers to the superseded names as a metric
    assert "Fordringar medlemsavgift" not in by_name
    assert "Övriga kortfristiga placeringar" not in by_name


def test_notes_are_not_checked_against_their_own_components():
    """The föreskrift is a minimum and funds add rows, so the components we
    know cannot reach the note's total. On six funds that check failed five
    times over for two separate notes while the note-to-statement check passed
    on both."""
    built = validation.rules_from_definitions(KEY_DEFS)
    sums = [r for r in built if r.name.startswith("Delsummering")]
    assert sums, "the statements still have subtotals to check"
    assert not any("Not till" in r.name for r in sums)


def test_the_statements_are_still_checked():
    names = {r.name for r in validation.rules_from_definitions(KEY_DEFS)}
    for target in ("Summa intäkter", "Årets resultat", "Summa tillgångar",
                   "Summa eget kapital, avsättningar och skulder"):
        assert f"Delsummering: {target}" in names, target
