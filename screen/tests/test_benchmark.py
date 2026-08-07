"""Kwartielen (handberekend, quantile_cont) en percentielposities; schattingen
doen niet mee aan de benchmark."""

import importlib

import polars as pl
import pytest


@pytest.fixture
def bench(data_root):
    import screen.peers.benchmark as mod
    return importlib.reload(mod)


def make_peers(data_root) -> str:
    """4 peers; revenue van P4 is een SCHATTING en mag niet meetellen."""
    df = pl.DataFrame([
        {"enterprise_number": "P1", "fiscal_year": 2023,
         "ebitda_proxy": 10.0, "ebitda_proxy_source": "derived",
         "revenue": 100.0, "revenue_source": "nbb"},
        {"enterprise_number": "P2", "fiscal_year": 2023,
         "ebitda_proxy": 20.0, "ebitda_proxy_source": "derived",
         "revenue": 200.0, "revenue_source": "nbb"},
        {"enterprise_number": "P3", "fiscal_year": 2023,
         "ebitda_proxy": 30.0, "ebitda_proxy_source": "derived",
         "revenue": 300.0, "revenue_source": "nbb"},
        {"enterprise_number": "P4", "fiscal_year": 2023,
         "ebitda_proxy": 40.0, "ebitda_proxy_source": "derived",
         "revenue": 999.0, "revenue_source": "estimate"},
    ])
    path = data_root / "marts" / "peers.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    return str(path)


def test_quartiles_hand_calculated(bench, data_root):
    peers_path = make_peers(data_root)
    result = bench.build_benchmark(peers_path=peers_path, progress=lambda *_: None)
    bm = pl.read_parquet(result.benchmark_path)

    # ebitda over 10,20,30,40 (quantile_cont, lineair geïnterpoleerd):
    # Q1 = 17,5 · mediaan = 25 · Q3 = 32,5 · n = 4
    row = bm.filter(pl.col("metric") == "ebitda_proxy").row(0, named=True)
    assert row["n"] == 4
    assert row["q1"] == pytest.approx(17.5)
    assert row["median"] == pytest.approx(25.0)
    assert row["q3"] == pytest.approx(32.5)

    # revenue: schatting van P4 uitgesloten -> n=3, mediaan 200
    row = bm.filter(pl.col("metric") == "revenue").row(0, named=True)
    assert row["n"] == 3
    assert row["median"] == pytest.approx(200.0)

    # metrics zonder kolommen in het bestand worden gemeld, niet stil genegeerd
    assert "equity" in result.skipped_metrics


def test_positions_percent_rank(bench, data_root):
    peers_path = make_peers(data_root)
    result = bench.build_benchmark(peers_path=peers_path, progress=lambda *_: None)
    pos = pl.read_parquet(result.positions_path)

    ebitda = pos.filter(pl.col("metric") == "ebitda_proxy")
    by_ent = {r["enterprise_number"]: r["percentile"] for r in ebitda.iter_rows(named=True)}
    assert by_ent["P1"] == pytest.approx(0.0)     # laagste
    assert by_ent["P4"] == pytest.approx(1.0)     # hoogste
    assert by_ent["P2"] == pytest.approx(1 / 3)

    # P4's geschatte revenue zit ook niet in de posities
    revenue_ents = set(pos.filter(pl.col("metric") == "revenue")["enterprise_number"])
    assert revenue_ents == {"P1", "P2", "P3"}


def test_missing_peers_file(bench, data_root):
    result = bench.build_benchmark(peers_path=data_root / "marts" / "niet.parquet",
                                   progress=lambda *_: None)
    assert result.benchmark_path is None
