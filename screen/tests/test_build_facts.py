"""facts-opbouw: is_latest/superseded, doorlopen bij kapotte neerleggingen,
taxonomie-kolom eerlijk (null zonder officiële lijst, false bij onbekende code)."""

import importlib
import shutil
from pathlib import Path

import polars as pl
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "nbb"


@pytest.fixture
def build_mod(data_root):
    import screen.parse.taxonomy as taxonomy
    importlib.reload(taxonomy)
    import screen.parse.kbo_csv as kbo_csv
    importlib.reload(kbo_csv)
    import screen.build as build
    importlib.reload(build)
    # fixtures als raw input
    nbb_raw = data_root / "raw" / "nbb"
    nbb_raw.mkdir(parents=True, exist_ok=True)
    for f in FIXTURES.glob("*.json"):
        shutil.copy(f, nbb_raw / f.name)
    return build


def test_build_facts_flags_and_survival(build_mod):
    result = build_mod.build_facts(progress=lambda *_: None)

    # kapotte neerlegging overgeslagen, run liep door (acceptatiecriterium)
    assert [name for name, _ in result.skipped] == ["dep_kapot.json"]
    assert result.deposit_count == 3
    assert result.facts_path and result.facts_path.exists()

    df = pl.read_parquet(result.facts_path)

    # verbeterende neerlegging wint: 2024-09-02 is_latest, origineel superseded
    latest_ev = df.filter(
        (pl.col("enterprise_number") == "0123456789")
        & (pl.col("rubric_code") == "10/15") & (pl.col("period") == "N")
        & pl.col("is_latest")
    )
    assert latest_ev["value"].to_list() == [510000.0]
    assert latest_ev["deposit_reference"].to_list() == ["2024-1-000099"]

    superseded = df.filter(pl.col("superseded"))
    assert set(superseded["deposit_reference"].to_list()) == {"2024-1-000001"}

    # andere onderneming: enige neerlegging is gewoon is_latest
    assert df.filter(
        (pl.col("enterprise_number") == "0987654321") & ~pl.col("is_latest")
    ).height == 0

    # zonder officiële taxonomie: kolom bestaat en is null (nooit true)
    assert not result.taxonomy_loaded
    assert df["code_in_taxonomy"].is_null().all()


def test_build_facts_with_taxonomy(build_mod, data_root):
    taxo_dir = data_root / "raw" / "nbb" / "taxonomy"
    taxo_dir.mkdir(parents=True)
    (taxo_dir / "rubrics.csv").write_text(
        "code\n10/15\n20/58\n70\n9901\n630\n9900\n"  # 62 ontbreekt bewust
    )
    result = build_mod.build_facts(progress=lambda *_: None)
    assert result.taxonomy_loaded
    assert result.unknown_codes == ["62"]

    df = pl.read_parquet(result.facts_path)
    assert df.filter(pl.col("rubric_code") == "62")["code_in_taxonomy"].to_list() == [False]
    assert df.filter(pl.col("rubric_code") == "70")["code_in_taxonomy"].to_list() == [True]


def test_build_facts_strict_taxonomy_fails_on_unknown(build_mod, data_root):
    import screen.parse.taxonomy as taxonomy

    taxo_dir = data_root / "raw" / "nbb" / "taxonomy"
    taxo_dir.mkdir(parents=True)
    (taxo_dir / "rubrics.csv").write_text("code\n10/15\n")
    with pytest.raises(taxonomy.TaxonomyError):
        build_mod.build_facts(strict_taxonomy=True, progress=lambda *_: None)


def test_build_facts_empty_raw_is_fine(data_root):
    import screen.build as build
    importlib.reload(build)
    result = build.build_facts(progress=lambda *_: None)
    assert result.fact_count == 0 and result.facts_path is None
