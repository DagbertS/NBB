"""Signalen: handberekende laattijdigheid, modelwissel, verbeterende
neerlegging, boekjaarwijziging; handmatige invoer met verplichte source/as_of."""

import importlib
import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from screen.signals import filing, manual_input  # noqa: E402
from screen.signals.filing import add_months  # noqa: E402
from datetime import date  # noqa: E402


def make_facts(rows):
    base = {"schema_type": "volledig", "rubric_code": "10/15", "period": "N",
            "value": 1.0, "model": "m01-f", "source": "nbb",
            "exercise_start": "", "exercise_end": "", "superseded": False,
            "is_latest": True, "code_in_taxonomy": None, "as_of": ""}
    return pl.DataFrame([{**base, **r} for r in rows])


class TestAddMonths:
    def test_normal(self):
        assert add_months(date(2023, 12, 31), 7) == date(2024, 7, 31)

    def test_day_overflow(self):
        # 31 juli + 7m -> 28/29 feb (dagoverloop afgekapt)
        assert add_months(date(2023, 7, 31), 7) == date(2024, 2, 29)
        assert add_months(date(2022, 7, 31), 7) == date(2023, 2, 28)


class TestLateFiling:
    def test_hand_calculated_days_late(self):
        # afsluiting 31/12/2023 -> deadline 31/07/2024; neergelegd 02/09/2024
        # = 33 dagen te laat (31 aug + 2 sep)
        facts = make_facts([{
            "enterprise_number": "0123456789", "fiscal_year": 2023,
            "deposit_reference": "r1", "deposit_date": "2024-09-02",
            "exercise_start": "2023-01-01", "exercise_end": "2023-12-31",
        }])
        signals = filing.build_filing_signals(facts)
        late = [s for s in signals if s["signal"] == "late_filing"]
        assert len(late) == 1
        assert late[0]["value"] == "33"
        assert late[0]["kind"] == "derived"
        assert "2024-07-31" in late[0]["note"]          # deadline expliciet

    def test_on_time_no_signal(self):
        facts = make_facts([{
            "enterprise_number": "0123456789", "fiscal_year": 2023,
            "deposit_reference": "r1", "deposit_date": "2024-07-10",
            "exercise_start": "2023-01-01", "exercise_end": "2023-12-31",
        }])
        assert not [s for s in filing.build_filing_signals(facts)
                    if s["signal"] == "late_filing"]

    def test_chronic_two_of_three(self):
        rows = []
        for year, deposited in ((2021, "2022-10-01"), (2022, "2023-10-01"),
                                (2023, "2024-07-01")):  # 2x laat, 1x op tijd
            rows.append({
                "enterprise_number": "0123456789", "fiscal_year": year,
                "deposit_reference": f"r{year}", "deposit_date": deposited,
                "exercise_start": f"{year}-01-01", "exercise_end": f"{year}-12-31",
            })
        signals = filing.build_filing_signals(make_facts(rows))
        chronic = [s for s in signals if s["signal"] == "chronic_late_filing"]
        assert len(chronic) == 1
        assert "2 van laatste 3" in chronic[0]["value"]

    def test_one_late_year_is_not_chronic(self):
        rows = [{
            "enterprise_number": "0123456789", "fiscal_year": 2023,
            "deposit_reference": "r1", "deposit_date": "2024-10-01",
            "exercise_start": "2023-01-01", "exercise_end": "2023-12-31",
        }]
        signals = filing.build_filing_signals(make_facts(rows))
        assert not [s for s in signals if s["signal"] == "chronic_late_filing"]


class TestModelSwitch:
    def test_full_to_abbreviated(self):
        rows = [
            {"enterprise_number": "0123456789", "fiscal_year": 2022,
             "deposit_reference": "r22", "deposit_date": "2023-06-01",
             "schema_type": "volledig"},
            {"enterprise_number": "0123456789", "fiscal_year": 2023,
             "deposit_reference": "r23", "deposit_date": "2024-06-01",
             "schema_type": "verkort"},
        ]
        signals = filing.build_filing_signals(make_facts(rows))
        switch = [s for s in signals if s["signal"] == "model_switch_full_to_abbreviated"]
        assert len(switch) == 1
        assert switch[0]["fiscal_year"] == 2023

    def test_abbreviated_to_full_is_no_signal(self):
        rows = [
            {"enterprise_number": "0123456789", "fiscal_year": 2022,
             "deposit_reference": "r22", "deposit_date": "2023-06-01",
             "schema_type": "verkort"},
            {"enterprise_number": "0123456789", "fiscal_year": 2023,
             "deposit_reference": "r23", "deposit_date": "2024-06-01",
             "schema_type": "volledig"},
        ]
        signals = filing.build_filing_signals(make_facts(rows))
        assert not [s for s in signals if s["signal"] == "model_switch_full_to_abbreviated"]


class TestCorrectionAndFiscalYear:
    def test_correction_filing(self):
        rows = [
            {"enterprise_number": "0123456789", "fiscal_year": 2023,
             "deposit_reference": "orig", "deposit_date": "2024-07-10",
             "is_latest": False, "superseded": True},
            {"enterprise_number": "0123456789", "fiscal_year": 2023,
             "deposit_reference": "verbeterd", "deposit_date": "2024-09-02"},
        ]
        signals = filing.build_filing_signals(make_facts(rows))
        corr = [s for s in signals if s["signal"] == "correction_filing"]
        assert len(corr) == 1
        assert "orig" in corr[0]["note"]

    def test_irregular_length_and_end_change(self):
        rows = [
            {"enterprise_number": "0123456789", "fiscal_year": 2022,
             "deposit_reference": "r22", "deposit_date": "2023-06-01",
             "exercise_start": "2022-01-01", "exercise_end": "2022-12-31"},
            # verlengd boekjaar van 18 maanden, afsluitmaand 12 -> 6
            {"enterprise_number": "0123456789", "fiscal_year": 2024,
             "deposit_reference": "r24", "deposit_date": "2024-12-01",
             "exercise_start": "2023-01-01", "exercise_end": "2024-06-30"},
        ]
        signals = filing.build_filing_signals(make_facts(rows))
        kinds = {s["signal"] for s in signals}
        assert "irregular_fiscal_year_length" in kinds
        assert "fiscal_year_end_changed" in kinds


class TestManualSignals:
    def test_load_valid(self, tmp_path):
        p = tmp_path / "sig.yaml"
        p.write_text("""
- enterprise_number: "0123.456.789"
  signal: bestuurderswissel
  value: "nieuwe bestuurder"
  source: "Staatsblad publicatie X"
  as_of: "2026-05-12"
""")
        signals = manual_input.load_manual_signals(p)
        assert len(signals) == 1
        assert signals[0]["enterprise_number"] == "0123456789"  # punten weg
        assert signals[0]["kind"] == "manual"

    def test_missing_source_rejected(self, tmp_path):
        p = tmp_path / "sig.yaml"
        p.write_text("""
- enterprise_number: "0123456789"
  signal: iets
  as_of: "2026-01-01"
""")
        with pytest.raises(manual_input.ManualSignalError, match="source"):
            manual_input.load_manual_signals(p)

    def test_missing_as_of_rejected(self, tmp_path):
        p = tmp_path / "sig.yaml"
        p.write_text("""
- enterprise_number: "0123456789"
  signal: iets
  source: bron
""")
        with pytest.raises(manual_input.ManualSignalError, match="as_of"):
            manual_input.load_manual_signals(p)

    def test_missing_file_is_empty(self, tmp_path):
        assert manual_input.load_manual_signals(tmp_path / "nope.yaml") == []

    def test_example_file_is_valid(self):
        example = Path(__file__).resolve().parents[1] / "manual_signals.example.yaml"
        signals = manual_input.load_manual_signals(example)
        assert len(signals) == 3
        assert all(s["source"] and s["as_of"] for s in signals)


def test_build_signals_end_to_end(data_root, tmp_path):
    import screen.signals.build_signals as bs
    importlib.reload(bs)

    facts = make_facts([{
        "enterprise_number": "0123456789", "fiscal_year": 2023,
        "deposit_reference": "r1", "deposit_date": "2024-09-02",
        "exercise_start": "2023-01-01", "exercise_end": "2023-12-31",
    }])
    facts_path = data_root / "interim" / "facts.parquet"
    facts.write_parquet(facts_path)

    manual = tmp_path / "manual.yaml"
    manual.write_text("""
- enterprise_number: "0123456789"
  signal: opvolgingssignaal
  value: "zaakvoerder 64"
  source: "sectorcontact"
  as_of: "2026-06-01"
""")
    result = bs.build_signals(manual_path=manual, progress=lambda *_: None)
    assert result.total == 2
    assert result.counts == {"late_filing": 1, "opvolgingssignaal": 1}

    df = pl.read_parquet(result.signals_path)
    assert set(df["kind"].to_list()) == {"derived", "manual"}
    assert df["source"].null_count() == 0 and df["as_of"].null_count() == 0
