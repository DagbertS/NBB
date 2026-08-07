"""Handberekende tests voor de scorekaart — elke score herleidbaar."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from screen.score import scorecard as sc  # noqa: E402


class TestFinancialScore:
    def test_hand_calculated(self):
        # (0.8 + 0.6 + (1-0.3)) / 3 × 100 = 70.0
        s = sc.financial_score({"ebitda_proxy": 0.8, "revenue": 0.6,
                                "net_financial_debt": 0.3})
        assert s.value == pytest.approx(70.0)
        assert "1-p0.30" in s.basis           # inversie zichtbaar in de basis

    def test_partial_percentiles(self):
        # alleen ebitda: 0.5 × 100 = 50
        s = sc.financial_score({"ebitda_proxy": 0.5, "revenue": None,
                                "net_financial_debt": None})
        assert s.value == pytest.approx(50.0)

    def test_no_percentiles(self):
        s = sc.financial_score({"ebitda_proxy": None})
        assert s.value is None and "geen" in s.basis


class TestGrowthScore:
    def test_hand_calculated_cagr(self):
        # 100 -> 121 over 2 jaar = 10%/jaar; 10/20 × 100 = 50
        s = sc.growth_score([(2021, 100.0), (2023, 121.0)], "ebitda_proxy")
        assert s.value == pytest.approx(50.0)
        assert "+10.0%" in s.basis

    def test_negative_growth_is_zero(self):
        s = sc.growth_score([(2022, 100.0), (2023, 80.0)], "ebitda_proxy")
        assert s.value == 0.0

    def test_capped_at_100(self):
        s = sc.growth_score([(2022, 100.0), (2023, 150.0)], "ebitda_proxy")
        assert s.value == 100.0                # 50% >> 20% full scale

    def test_single_year_none(self):
        assert sc.growth_score([(2023, 100.0)], "x").value is None

    def test_non_positive_values_none(self):
        s = sc.growth_score([(2022, -50.0), (2023, 100.0)], "ebitda_proxy")
        assert s.value is None and "onbepaald" in s.basis


class TestSignalsScore:
    def test_no_signals_is_100(self):
        s = sc.signals_score({})
        assert s.value == 100.0

    def test_hand_calculated_penalties(self):
        # chronic 25 + late_filing 2×10=20 (cap) + modelwissel 15 = 60 → 40
        s = sc.signals_score({"chronic_late_filing": 1, "late_filing": 2,
                              "model_switch_full_to_abbreviated": 1})
        assert s.value == pytest.approx(40.0)
        assert "chronic_late_filing×1" in s.basis

    def test_late_filing_capped(self):
        # 5 late jaren zou 50 zijn maar cap = 20 → 80
        s = sc.signals_score({"late_filing": 5})
        assert s.value == pytest.approx(80.0)

    def test_floor_at_zero(self):
        s = sc.signals_score({"chronic_late_filing": 3, "model_switch_full_to_abbreviated": 3})
        assert s.value == 0.0

    def test_manual_signals_not_penalized(self):
        s = sc.signals_score({"opvolgingssignaal": 2, "bestuurderswissel": 1})
        assert s.value == 100.0


class TestTotalScore:
    def test_hand_calculated_weighted(self):
        # 0.5×70 + 0.2×50 + 0.3×40 = 35 + 10 + 12 = 57
        comps = {
            "financial": sc.ComponentScore(70.0, "f"),
            "growth": sc.ComponentScore(50.0, "g"),
            "signals": sc.ComponentScore(40.0, "s"),
        }
        s = sc.total_score(comps, {"financial": 0.5, "growth": 0.2, "signals": 0.3})
        assert s.value == pytest.approx(57.0)

    def test_renormalizes_when_component_missing(self):
        # growth ontbreekt: gewichten 0.5/0.3 -> 0.625/0.375
        # 0.625×80 + 0.375×40 = 50 + 15 = 65, en de basis meldt het
        comps = {
            "financial": sc.ComponentScore(80.0, "f"),
            "growth": sc.ComponentScore(None, "geen data"),
            "signals": sc.ComponentScore(40.0, "s"),
        }
        s = sc.total_score(comps, {"financial": 0.5, "growth": 0.2, "signals": 0.3})
        assert s.value == pytest.approx(65.0)
        assert "hernormaliseerd" in s.basis and "growth" in s.basis

    def test_nothing_available(self):
        comps = {"financial": sc.ComponentScore(None, "x")}
        assert sc.total_score(comps).value is None

    def test_no_financial_component_means_no_total(self):
        # alleen een signaalscore: geen rangorde op basis van 'minste data'
        comps = {
            "financial": sc.ComponentScore(None, "geen percentielen"),
            "signals": sc.ComponentScore(100.0, "s"),
        }
        s = sc.total_score(comps, {"financial": 0.5, "growth": 0.2, "signals": 0.3})
        assert s.value is None
        assert "onvoldoende data" in s.basis


class TestClassification:
    def test_platform(self):
        assert sc.classify_target(30, {"fte_min": 25}, {"fte_max": 25}) == "platform"

    def test_bolt_on(self):
        assert sc.classify_target(10, {"fte_min": 25}, {"fte_max": 25}) == "bolt_on"

    def test_between(self):
        assert sc.classify_target(30, {"fte_min": 50}, {"fte_max": 25}) == "tussenmaat"

    def test_unknown_without_fte(self):
        assert sc.classify_target(None, {"fte_min": 25}, {"fte_max": 25}) is None
