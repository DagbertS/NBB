"""Universe-selectie, overrides, conversievlag, peer-ratio en peer-filters."""

import csv
import importlib
import io
import zipfile
from pathlib import Path

import polars as pl
import pytest


def make_kbo_zip(path: Path) -> Path:
    """8 testbedrijven die elk één selectiecriterium raken."""
    def rows(header, data):
        return [header] + data

    tables = {
        "enterprise.csv": rows(
            ["EnterpriseNumber", "Status", "JuridicalForm", "StartDate"],
            [
                ["0100.000.001", "AC", "014", "01-01-2001"],  # A match
                ["0100.000.002", "AC", "014", "01-01-2001"],  # B verkeerde provincie
                ["0100.000.003", "AC", "014", "01-01-2001"],  # C holding
                ["0100.000.004", "ST", "014", "01-01-2001"],  # D niet actief
                ["0100.000.005", "AC", "014", "01-01-2001"],  # E andere nace
                ["0100.000.006", "AC", "014", "01-01-2001"],  # F override exclude
                ["0100.000.007", "AC", "014", "01-01-2001"],  # G override include
                ["0100.000.008", "AC", "014", "01-01-2001"],  # H 1-op-veel-conversie
            ],
        ),
        "activity.csv": rows(
            ["EntityNumber", "NaceVersion", "NaceCode", "Classification"],
            [
                ["0100.000.001", "2025", "37000", "MAIN"],
                ["0100.000.001", "2008", "37000", "MAIN"],
                ["0100.000.002", "2025", "37000", "MAIN"],
                ["0100.000.003", "2025", "64200", "MAIN"],
                ["0100.000.004", "2025", "37000", "MAIN"],
                ["0100.000.005", "2025", "10710", "MAIN"],
                ["0100.000.006", "2025", "37000", "MAIN"],
                ["0100.000.007", "2025", "10710", "MAIN"],
                ["0100.000.008", "2025", "43221", "MAIN"],
                ["0100.000.008", "2008", "43221", "MAIN"],
            ],
        ),
        "address.csv": rows(
            ["EntityNumber", "TypeOfAddress", "Zipcode", "MunicipalityNL",
             "MunicipalityFR", "StreetNL", "HouseNumber"],
            [
                ["0100.000.001", "REGO", "3500", "Hasselt", "", "x", "1"],
                ["0100.000.002", "REGO", "1000", "Brussel", "", "x", "1"],
                ["0100.000.003", "REGO", "3500", "Hasselt", "", "x", "1"],
                ["0100.000.004", "REGO", "2000", "Antwerpen", "", "x", "1"],
                ["0100.000.005", "REGO", "3500", "Hasselt", "", "x", "1"],
                ["0100.000.006", "REGO", "3500", "Hasselt", "", "x", "1"],
                ["0100.000.007", "REGO", "9000", "Gent", "", "x", "1"],
                ["0100.000.008", "REGO", "3500", "Hasselt", "", "x", "1"],
            ],
        ),
        "denomination.csv": rows(
            ["EntityNumber", "Language", "TypeOfDenomination", "Denomination"],
            [["0100.000.001", "2", "001", "Ruiming A BV"],
             ["0100.000.008", "2", "001", "Riolering H BV"]],
        ),
    }
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in tables.items():
            buf = io.StringIO()
            csv.writer(buf).writerows(data)
            zf.writestr(name, buf.getvalue())
    return path


@pytest.fixture
def peers_env(data_root, tmp_path):
    mods = {}
    for name in ("screen.parse.kbo_csv", "screen.peers.nace_bridge",
                 "screen.peers.peer_set", "screen.peers.benchmark"):
        mods[name.rsplit(".", 1)[-1]] = importlib.reload(importlib.import_module(name))
    from screen.config import SizeCriteria, Thesis

    mods["kbo_csv"].parse_zip(make_kbo_zip(tmp_path / "kbo.zip"),
                              progress=lambda *_: None)
    thesis = Thesis(
        name="test", nace_version=2025, nace_codes=["37.00", "43.22"],
        provinces=["Limburg", "Antwerpen", "Vlaams-Brabant", "Oost-Vlaanderen"],
        size=SizeCriteria(),
    )
    bridge_csv = tmp_path / "conv.csv"
    bridge_csv.write_text("nace_2008,nace_2025\n37000,37000\n43221,43221\n43221,43222\n")
    bridge = mods["nace_bridge"].load_bridge(bridge_csv)
    return mods, thesis, bridge


OVERRIDES = {
    "0100000006": {"include": False, "note": "managementvennootschap in de praktijk"},
    "0100000007": {"include": True, "note": "website toont ruimingsactiviteit"},
}


def test_universe_selection(peers_env):
    mods, thesis, bridge = peers_env
    universe = mods["peer_set"].build_universe(
        thesis, overrides=OVERRIDES, bridge=bridge, progress=lambda *_: None
    )
    selected = set(universe["enterprise_number"].to_list())
    # A (match), G (override include), H (43221) — en niemand anders
    assert selected == {"0100000001", "0100000007", "0100000008"}

    a = universe.filter(pl.col("enterprise_number") == "0100000001").row(0, named=True)
    assert a["province"] == "Limburg" and a["name"] == "Ruiming A BV"
    assert a["nace_conversion_ambiguous"] is False       # 37000 -> 1 tegenhanger

    g = universe.filter(pl.col("enterprise_number") == "0100000007").row(0, named=True)
    assert g["override_included"] is True
    assert g["crit_nace_match"] is False                 # zit er alleen door override

    h = universe.filter(pl.col("enterprise_number") == "0100000008").row(0, named=True)
    assert h["nace_conversion_ambiguous"] is True        # 43221 had 2 tegenhangers


def test_universe_without_bridge_flag_is_null(peers_env):
    mods, thesis, _ = peers_env
    universe = mods["peer_set"].build_universe(
        thesis, overrides={}, bridge=None, progress=lambda *_: None
    )
    assert universe["nace_conversion_ambiguous"].is_null().all()


def _facts_df(rows):
    base = {"schema_type": "volledig", "period": "N", "is_latest": True,
            "superseded": False, "deposit_reference": "r", "deposit_date": "2024-01-01",
            "model": "m01-f", "source": "nbb", "as_of": "2024-01-01",
            "code_in_taxonomy": None}
    return pl.DataFrame([{**base, **r} for r in rows])


def test_revenue_ratio_hand_calculated(peers_env, data_root):
    mods, thesis, bridge = peers_env
    universe = mods["peer_set"].build_universe(
        thesis, overrides=OVERRIDES, bridge=bridge, progress=lambda *_: None
    )
    # P1 (A): 2.5M / (2.5M − 0.9M − 0.4M) = 2,0833; P2 (H): 1.0M / 0.5M = 2,0
    facts = _facts_df([
        {"enterprise_number": "0100000001", "fiscal_year": 2023, "rubric_code": "70", "value": 2_500_000.0},
        {"enterprise_number": "0100000001", "fiscal_year": 2023, "rubric_code": "60", "value": 900_000.0},
        {"enterprise_number": "0100000001", "fiscal_year": 2023, "rubric_code": "61", "value": 400_000.0},
        {"enterprise_number": "0100000008", "fiscal_year": 2023, "rubric_code": "70", "value": 1_000_000.0},
        {"enterprise_number": "0100000008", "fiscal_year": 2023, "rubric_code": "60", "value": 300_000.0},
        {"enterprise_number": "0100000008", "fiscal_year": 2023, "rubric_code": "61", "value": 200_000.0},
    ])
    facts_path = data_root / "interim" / "facts.parquet"
    facts.write_parquet(facts_path)

    # standaard minimum (5) niet gehaald -> eerlijk geen ratio
    ratio, note = mods["peer_set"].compute_revenue_ratio(universe, facts_path)
    assert ratio is None and "niet geschat" in note

    ratio, note = mods["peer_set"].compute_revenue_ratio(universe, facts_path, min_n=2)
    assert ratio == pytest.approx((2_500_000 / 1_200_000 + 2.0) / 2)  # mediaan van 2
    assert "n=2" in note


def test_build_peers_filters_and_criteria(peers_env, data_root):
    mods, thesis, bridge = peers_env
    universe = mods["peer_set"].build_universe(
        thesis, overrides=OVERRIDES, bridge=bridge, progress=lambda *_: None
    )
    metrics = pl.DataFrame([
        # A: echte peer (personeelskost > 0, MVA > 0)
        {"enterprise_number": "0100000001", "fiscal_year": 2023,
         "personnel_cost": 200_000.0, "tangible_fixed_assets": 50_000.0},
        # G: personeelskost 0 -> vervuilingsfilter
        {"enterprise_number": "0100000007", "fiscal_year": 2023,
         "personnel_cost": 0.0, "tangible_fixed_assets": 10_000.0},
        # H: geen MVA gerapporteerd -> filter
        {"enterprise_number": "0100000008", "fiscal_year": 2023,
         "personnel_cost": 100_000.0, "tangible_fixed_assets": None},
    ])
    metrics_path = data_root / "interim" / "metrics.parquet"
    metrics.write_parquet(metrics_path)

    result = mods["peer_set"].build_peers(universe, thesis, metrics_path=metrics_path,
                                          progress=lambda *_: None)
    assert result.universe_count == 3
    assert result.with_financials_count == 3
    assert result.peer_count == 1
    assert any("< 20" in w for w in result.warnings)     # expliciete waarschuwing

    import json
    criteria = json.loads((data_root / "marts" / "peer_criteria.json").read_text())
    assert criteria["counts"] == {"universe": 3, "met_financials": 3, "peers": 1}
    assert criteria["filters"]["personeelskost"] == "> 0"
