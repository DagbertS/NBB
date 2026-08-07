"""metrics.parquet-opbouw: laatste neerlegging wint, schattingen expliciet,
owner-vlaggen eerlijk leeg, en doorlopen op magere data."""

import importlib
import shutil
from pathlib import Path

import polars as pl
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "nbb"


@pytest.fixture
def norm(data_root):
    for mod in ("screen.parse.taxonomy", "screen.parse.kbo_csv", "screen.build",
                "screen.normalize.build_metrics"):
        importlib.reload(importlib.import_module(mod))
    import screen.build as build
    import screen.normalize.build_metrics as norm_mod

    nbb_raw = data_root / "raw" / "nbb"
    nbb_raw.mkdir(parents=True, exist_ok=True)
    for f in FIXTURES.glob("*.json"):
        shutil.copy(f, nbb_raw / f.name)
    build.build_facts(progress=lambda *_: None)
    return norm_mod


def test_metrics_from_latest_deposit(norm):
    result = norm.build_metrics(progress=lambda *_: None)
    df = pl.read_parquet(result.metrics_path)
    assert result.row_count == 2  # 2 ondernemingen × boekjaar 2023

    full = df.filter(pl.col("enterprise_number") == "0123456789").row(0, named=True)
    # eigen vermogen uit de VERBETERENDE neerlegging (510k), niet de originele
    assert full["equity"] == 510_000.0
    assert full["equity_source"] == "nbb"
    # balanstotaal idem (1.210k)
    assert full["balance_total"] == 1_210_000.0


def test_abbreviated_revenue_honest_without_ratio(norm):
    result = norm.build_metrics(progress=lambda *_: None)
    df = pl.read_parquet(result.metrics_path)
    abbr = df.filter(pl.col("enterprise_number") == "0987654321").row(0, named=True)

    assert abbr["schema_type"] == "verkort"
    assert abbr["revenue"] is None                       # geen stille schatting
    assert "ratio" in abbr["revenue_note"]
    assert result.estimated_revenue_count == 0
    # personeelskost per VTE: 9087 ontbreekt in fixture -> None met uitleg
    assert abbr["cost_per_fte"] is None


def test_abbreviated_revenue_estimate_with_explicit_ratio(norm):
    result = norm.build_metrics(revenue_ratio=3.0,
                                revenue_ratio_note="test-aanname",
                                progress=lambda *_: None)
    df = pl.read_parquet(result.metrics_path)
    abbr = df.filter(pl.col("enterprise_number") == "0987654321").row(0, named=True)

    # 350.000 × 3.0 = 1.050.000 — maar als SCHATTING gemarkeerd
    assert abbr["revenue"] == pytest.approx(1_050_000.0)
    assert abbr["revenue_source"] == "estimate"
    assert abbr["revenue_confidence"] == "low"
    assert "SCHATTING" in abbr["revenue_note"]
    assert result.estimated_revenue_count == 1

    # het volledig schema blijft een feit, geen schatting
    full = df.filter(pl.col("enterprise_number") == "0123456789").row(0, named=True)
    assert full["revenue_source"] == "nbb"


def test_owner_flags_exist_but_are_honestly_null(norm):
    result = norm.build_metrics(progress=lambda *_: None)
    df = pl.read_parquet(result.metrics_path)
    for flag in ("flag_management_fees", "flag_shareholder_current_account"):
        assert df[flag].is_null().all()
        assert all("peer-benchmark" in n for n in df[f"{flag}_note"].to_list())


def test_runs_without_facts(data_root):
    import screen.normalize.build_metrics as norm_mod
    importlib.reload(norm_mod)
    result = norm_mod.build_metrics(progress=lambda *_: None)
    assert result.row_count == 0 and result.metrics_path is None
