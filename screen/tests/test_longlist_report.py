"""Longlist-assemblage en one-pager, end-to-end op kleine marts."""

import importlib

import polars as pl
import pytest


@pytest.fixture
def marts(data_root):
    """Universe + metrics (2 jaar) + posities + signalen voor 2 bedrijven."""
    mods = {}
    for name in ("screen.score.build_longlist", "screen.report.onepager"):
        mods[name.rsplit(".", 1)[-1]] = importlib.reload(importlib.import_module(name))

    interim = data_root / "interim"
    martsdir = data_root / "marts"
    martsdir.mkdir(parents=True, exist_ok=True)

    pl.DataFrame([
        {"enterprise_number": "0100000001", "name": "Ruiming A BV",
         "nace_effective": "37000", "province": "Limburg", "status": "AC",
         "nace_conversion_ambiguous": False, "override_included": False,
         "override_excluded": False, "zipcode": "3500", "nace_2008": "37000",
         "crit_nace_match": True, "crit_active": True, "crit_not_holding": True,
         "crit_geo": True},
        {"enterprise_number": "0100000008", "name": "Riolering H BV",
         "nace_effective": "43221", "province": "Limburg", "status": "AC",
         "nace_conversion_ambiguous": True, "override_included": False,
         "override_excluded": False, "zipcode": "3500", "nace_2008": "43221",
         "crit_nace_match": True, "crit_active": True, "crit_not_holding": True,
         "crit_geo": True},
    ]).write_parquet(interim / "universe.parquet")

    def met(ent, year, **kw):
        base = {"enterprise_number": ent, "fiscal_year": year,
                "schema_type": "volledig"}
        for m in ("revenue", "ebitda_proxy", "ebit", "equity", "balance_total",
                  "net_financial_debt", "fte", "cost_per_fte", "dso_days",
                  "capex_intensity"):
            base[m] = kw.get(m)
            base[f"{m}_source"] = kw.get(f"{m}_source")
            base[f"{m}_confidence"] = None
            base[f"{m}_note"] = None
        return base

    pl.DataFrame([
        met("0100000001", 2021, ebitda_proxy=100_000.0, ebitda_proxy_source="derived",
            equity=400_000.0, equity_source="nbb"),
        met("0100000001", 2023, ebitda_proxy=121_000.0, ebitda_proxy_source="derived",
            revenue=2_500_000.0, revenue_source="nbb", equity=510_000.0,
            equity_source="nbb", balance_total=1_210_000.0, balance_total_source="nbb",
            fte=30.0, fte_source="nbb", net_financial_debt=250_000.0,
            net_financial_debt_source="derived"),
        met("0100000008", 2023, ebitda_proxy=80_000.0, ebitda_proxy_source="derived",
            revenue=1_050_000.0, revenue_source="estimate", fte=10.0, fte_source="nbb"),
    ]).write_parquet(interim / "metrics.parquet")

    pl.DataFrame([
        {"enterprise_number": "0100000001", "fiscal_year": 2023,
         "metric": "ebitda_proxy", "value": 121_000.0, "percentile": 0.8},
        {"enterprise_number": "0100000001", "fiscal_year": 2023,
         "metric": "revenue", "value": 2_500_000.0, "percentile": 0.6},
        {"enterprise_number": "0100000001", "fiscal_year": 2023,
         "metric": "net_financial_debt", "value": 250_000.0, "percentile": 0.3},
    ]).write_parquet(martsdir / "peer_positions.parquet")

    pl.DataFrame([
        {"metric": "ebitda_proxy", "fiscal_year": 2023, "n": 4,
         "q1": 17_500.0, "median": 25_000.0, "q3": 32_500.0},
        {"metric": "revenue", "fiscal_year": 2023, "n": 3,
         "q1": 100_000.0, "median": 200_000.0, "q3": 300_000.0},
    ]).write_parquet(martsdir / "benchmark.parquet")

    pl.DataFrame([
        {"enterprise_number": "0100000008", "fiscal_year": 2023,
         "signal": "late_filing", "value": "33", "source": "nbb-filing-metadata",
         "as_of": "2024-09-02", "kind": "derived", "note": ""},
        {"enterprise_number": "0100000008", "fiscal_year": None,
         "signal": "opvolgingssignaal", "value": "zaakvoerder 64",
         "source": "sectorcontact", "as_of": "2026-06-01", "kind": "manual", "note": ""},
    ], schema_overrides={"fiscal_year": pl.Int32}).write_parquet(martsdir / "signals.parquet")

    from screen.config import SizeCriteria, Thesis
    thesis = Thesis(
        name="test", nace_codes=["37.00", "43.22"],
        size=SizeCriteria(balance_total_min=500_000, balance_total_max=15_000_000,
                          fte_min=5, fte_max=120),
        platform_criteria={"fte_min": 25}, bolt_on_criteria={"fte_max": 25},
        weights={"financial": 0.5, "growth": 0.2, "signals": 0.3},
    )
    return mods, thesis, data_root


def test_longlist_scores_hand_calculated(marts):
    mods, thesis, data_root = marts
    result = mods["build_longlist"].build_longlist(thesis, progress=lambda *_: None)
    df = pl.read_parquet(result.longlist_path)
    assert result.row_count == 2

    a = df.filter(pl.col("enterprise_number") == "0100000001").row(0, named=True)
    # financieel: (0.8 + 0.6 + 0.7)/3 × 100 = 70
    assert a["score_financial"] == pytest.approx(70.0)
    # groei: 100k -> 121k over 2 jaar = 10%/jaar -> 50
    assert a["score_growth"] == pytest.approx(50.0)
    # signalen: geen -> 100
    assert a["score_signals"] == 100.0
    # totaal: 0.5×70 + 0.2×50 + 0.3×100 = 75
    assert a["score_total"] == pytest.approx(75.0)
    assert a["target_class"] == "platform"          # 30 VTE >= 25
    assert a["in_size_range"] is True

    h = df.filter(pl.col("enterprise_number") == "0100000008").row(0, named=True)
    # geen percentielen -> financieel None; late_filing -> 90
    assert h["score_financial"] is None
    assert h["score_signals"] == pytest.approx(90.0)
    # zonder financiële component géén totaalscore: minder data mag
    # nooit een hogere rang opleveren
    assert h["score_total"] is None
    assert "onvoldoende data" in h["score_total_basis"]
    assert h["target_class"] == "bolt_on"           # 10 VTE <= 25
    assert h["signals_manual_count"] == 1

    # longlist is gerankt: A boven H
    assert df["enterprise_number"].to_list()[0] == "0100000001"


def test_onepager_content(marts):
    mods, thesis, data_root = marts
    mods["build_longlist"].build_longlist(thesis, progress=lambda *_: None)

    md = mods["onepager"].generate_report("0100.000.008")
    # identiteit + conversiewaarschuwing
    assert "Riolering H BV" in md and "0100000008" in md
    assert "meerdere 2025-tegenhangers" in md
    # geschatte omzet expliciet gemarkeerd, nooit als feit
    assert "1,050,000 *(schatting)*" in md
    assert "géén gepubliceerd feit" in md
    # signalen met bron en datum
    assert "late_filing" in md and "sectorcontact" in md
    # scoreopbouw transparant, ook als er geen totaalscore is
    assert "Score-opbouw" in md and "onvoldoende data" in md
    # dataroom-vragen afgeleid van wat ontbreekt
    assert "omzetcijfers" in md.lower()
    assert "laattijdige neerlegging" in md.lower()
    assert "hoofdactiviteit verifiëren" in md.lower()

    path = mods["onepager"].write_report("0100000008")
    assert path.exists() and path.name == "0100000008.md"


def test_onepager_peer_section(marts):
    mods, thesis, data_root = marts
    mods["build_longlist"].build_longlist(thesis, progress=lambda *_: None)
    md = mods["onepager"].generate_report("0100000001")
    assert "peer-kwartielen" in md.lower()
    assert "p0.80" in md          # percentiel van A op ebitda
    assert "platform" in md


def test_report_unknown_enterprise(marts):
    mods, thesis, data_root = marts
    mods["build_longlist"].build_longlist(thesis, progress=lambda *_: None)
    with pytest.raises(mods["onepager"].ReportError, match="niet in de longlist"):
        mods["onepager"].generate_report("9999999999")
