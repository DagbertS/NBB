"""Handberekende unit tests voor elke financiële metric (spec §9: 'elke
financiële metric heeft een unit test met een handberekend voorbeeld')."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from screen.normalize import metrics as M  # noqa: E402
from screen.normalize import rubrics as R  # noqa: E402


class TestEbitdaProxy:
    def test_hand_calculated(self):
        # EBIT 180.000 + afschr 90.000 + waardeverm 5.000 + voorz 3.000 = 278.000
        rub = {R.EBIT: 180_000.0, R.AFSCHRIJVINGEN: 90_000.0,
               R.WAARDEVERMINDERINGEN: 5_000.0, R.VOORZIENINGEN: 3_000.0}
        m = M.ebitda_proxy(rub)
        assert m.value == 278_000.0
        assert m.source == "derived" and m.confidence == "high" and not m.note

    def test_negative_addback_terugneming(self):
        # terugneming voorziening: 100.000 + 20.000 + (-8.000) = 112.000
        rub = {R.EBIT: 100_000.0, R.AFSCHRIJVINGEN: 20_000.0, R.VOORZIENINGEN: -8_000.0}
        m = M.ebitda_proxy(rub)
        assert m.value == 112_000.0
        assert m.confidence == "medium"           # 631/4 ontbreekt
        assert "631/4" in m.note                  # en dat staat er expliciet

    def test_no_ebit_means_none(self):
        m = M.ebitda_proxy({R.AFSCHRIJVINGEN: 1.0})
        assert m.value is None and "9901" in m.note


class TestNetFinancialDebt:
    def test_hand_calculated(self):
        # 200.000 + 50.000 + 30.000 − 20.000 − 10.000 = 250.000
        rub = {R.LT_FIN_SCHULD: 200_000.0, R.KT_DEEL_LT: 50_000.0,
               R.KT_FIN_SCHULD: 30_000.0, R.GELDBELEGGINGEN: 20_000.0,
               R.LIQUIDE_MIDDELEN: 10_000.0}
        m = M.net_financial_debt(rub)
        assert m.value == 250_000.0
        assert m.source == "derived" and m.confidence == "high"

    def test_net_cash_position_negative_value(self):
        # alleen cash: 0 − 40.000 = −40.000 (netto kaspositie), lagere confidence
        m = M.net_financial_debt({R.LIQUIDE_MIDDELEN: 40_000.0})
        assert m.value == -40_000.0
        assert m.confidence == "low"              # 4 componenten ontbreken
        assert "170/4" in m.note

    def test_nothing_reported_means_none(self):
        assert M.net_financial_debt({}).value is None


class TestRevenue:
    def test_full_schema_is_fact(self):
        m = M.revenue({R.OMZET: 2_500_000.0})
        assert m.value == 2_500_000.0
        assert m.source == "nbb" and m.confidence == "high"

    def test_abbreviated_without_ratio_never_estimates(self):
        m = M.revenue({R.BRUTOMARGE: 350_000.0})
        assert m.value is None
        assert "ratio" in m.note                  # uitleg waarom niet

    def test_abbreviated_with_ratio_is_estimate_never_fact(self):
        # 350.000 × 3.2 = 1.120.000 — en duidelijk gemarkeerd als schatting
        m = M.revenue({R.BRUTOMARGE: 350_000.0}, gross_margin_ratio=3.2)
        assert m.value == pytest.approx(1_120_000.0)
        assert m.source == "estimate" and m.confidence == "low"
        assert "SCHATTING" in m.note

    def test_invalid_ratio_rejected(self):
        assert M.revenue({R.BRUTOMARGE: 1.0}, gross_margin_ratio=0).value is None

    def test_nothing_reported(self):
        assert M.revenue({}).value is None


class TestValueAdded:
    def test_full_schema_hand_calculated(self):
        # 2.500.000 − 900.000 − 400.000 = 1.200.000
        rub = {R.OMZET: 2_500_000.0, R.HANDELSGOEDEREN: 900_000.0, R.DIENSTEN: 400_000.0}
        m = M.value_added_proxy(rub)
        assert m.value == 1_200_000.0
        assert m.source == "derived" and m.confidence == "medium"

    def test_abbreviated_uses_gross_margin(self):
        m = M.value_added_proxy({R.BRUTOMARGE: 350_000.0})
        assert m.value == 350_000.0 and "9900" in m.note


class TestWorkingCapitalDays:
    def test_dso_hand_calculated(self):
        # 250.000 / 2.500.000 × 365 = 36,5 dagen
        rev = M.revenue({R.OMZET: 2_500_000.0})
        m = M.dso_days({R.HANDELSVORDERINGEN: 250_000.0}, rev)
        assert m.value == pytest.approx(36.5)
        assert m.source == "derived"

    def test_dso_on_estimated_revenue_is_estimate(self):
        rev = M.revenue({R.BRUTOMARGE: 350_000.0}, gross_margin_ratio=3.2)
        m = M.dso_days({R.HANDELSVORDERINGEN: 100_000.0}, rev)
        assert m.source == "estimate" and m.confidence == "low"
        assert "GESCHATTE" in m.note

    def test_dpo_hand_calculated(self):
        # 130.000 / (900.000 + 400.000) × 365 = 36,5 dagen
        rub = {R.HANDELSSCHULDEN: 130_000.0, R.HANDELSGOEDEREN: 900_000.0,
               R.DIENSTEN: 400_000.0}
        m = M.dpo_days(rub)
        assert m.value == pytest.approx(36.5)

    def test_dpo_missing_purchases_abbreviated(self):
        m = M.dpo_days({R.HANDELSSCHULDEN: 130_000.0})
        assert m.value is None and "verkort" in m.note

    def test_inventory_days_hand_calculated(self):
        # 90.000 / 900.000 × 365 = 36,5
        m = M.inventory_days({R.VOORRADEN: 90_000.0, R.HANDELSGOEDEREN: 900_000.0})
        assert m.value == pytest.approx(36.5)


class TestCapexIntensity:
    def test_hand_calculated(self):
        # 240.000 / TW(2.5M − 0.9M − 0.4M = 1.2M) = 0,20
        rub = {R.AANSCHAF_MVA: 240_000.0, R.OMZET: 2_500_000.0,
               R.HANDELSGOEDEREN: 900_000.0, R.DIENSTEN: 400_000.0}
        m = M.capex_intensity(rub)
        assert m.value == pytest.approx(0.20)
        assert m.confidence == "low"              # noemer is proxy
        assert "proxy" in m.note

    def test_missing_acquisitions(self):
        assert M.capex_intensity({R.OMZET: 1.0}).value is None


class TestCostPerFte:
    def test_hand_calculated(self):
        # 620.000 / 10 VTE = 62.000
        m = M.cost_per_fte({R.BEZOLDIGINGEN: 620_000.0, R.VTE_GEMIDDELD: 10.0})
        assert m.value == 62_000.0 and m.confidence == "high"

    def test_zero_fte(self):
        assert M.cost_per_fte({R.BEZOLDIGINGEN: 1.0, R.VTE_GEMIDDELD: 0.0}).value is None


class TestReported:
    def test_passthrough(self):
        m = M.reported({R.EIGEN_VERMOGEN: 510_000.0}, R.EIGEN_VERMOGEN)
        assert m.value == 510_000.0 and m.source == "nbb"

    def test_absent(self):
        assert M.reported({}, R.EIGEN_VERMOGEN).value is None
