"""Taxonomie: mechanica van de validatie + de poortwachter-test die de
seed-codes uit de projectspecificatie tegen de OFFICIËLE lijst houdt."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import screen.parse.taxonomy as taxonomy  # noqa: E402


def make_taxonomy(tmp_path, codes) -> Path:
    p = tmp_path / "rubrics.csv"
    p.write_text("code,label\n" + "\n".join(f"{c},label {c}" for c in codes))
    return p


def test_load_and_membership(tmp_path):
    p = make_taxonomy(tmp_path, ["10/15", "20/58", "9901"])
    taxo = taxonomy.load_taxonomy(p)
    assert "10/15" in taxo
    assert "10/15 " in taxo          # normalisatie: spaties
    assert "9999" not in taxo
    assert taxo.unknown(["10/15", "9999", "8888"]) == ["8888", "9999"]


def test_missing_file_returns_none(tmp_path):
    assert taxonomy.load_taxonomy(tmp_path / "bestaat_niet.csv") is None


def test_empty_file_raises(tmp_path):
    p = tmp_path / "rubrics.csv"
    p.write_text("code,label\n")
    with pytest.raises(taxonomy.TaxonomyError):
        taxonomy.load_taxonomy(p)


def test_seed_codes_in_official_taxonomy():
    """DE poortwachter (spec §5): elke seed-code moet in de officiële
    NBB-taxonomie voorkomen. Skipt met duidelijke reden zolang de officiële
    lijst niet gedownload is — slaagt dus nooit stilzwijgend."""
    # Pad expliciet vanaf de repo-root berekenen: andere tests herladen
    # screen.config met tijdelijke datapaden, en dit moet altijd tegen de
    # ECHTE officiële lijst draaien.
    official = (Path(__file__).resolve().parents[2]
                / "data" / "raw" / "nbb" / "taxonomy" / "rubrics.csv")
    taxo = taxonomy.load_taxonomy(official) if official.exists() else None
    if taxo is None:
        pytest.skip(
            "Officiële taxonomie nog niet gedownload "
            "(data/raw/nbb/taxonomy/rubrics.csv) — zie docs/SOURCES.md. "
            "Seed-codes uit de spec blijven ONGEVALIDEERD tot deze test draait."
        )
    unknown = taxo.unknown(taxonomy.SEED_CODES)
    assert not unknown, (
        f"Seed-codes NIET in de officiële taxonomie: {unknown} — "
        "corrigeer screen/parse/taxonomy.py::SEED_CODES en de normalize-mapping"
    )
