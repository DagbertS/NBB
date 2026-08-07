"""Taxonomie-import: rubriekcodes herkennen in rommelige NBB-bestanden
(opgemaakte Excel/CSV), en de Statbel-brug in xlsx-vorm."""

import importlib

import polars as pl
import pytest


@pytest.fixture
def tax_import(data_root):
    import screen.ingest.taxonomy_import as mod
    return importlib.reload(mod)


def test_import_from_messy_csv(tax_import, tmp_path):
    """Modellen-bestanden zijn geen nette tabellen: codes staan tussen labels."""
    src = tmp_path / "model.csv"
    src.write_text(
        "ACTIVA;;\n"
        "Materiële vaste activa;22/27;12345\n"
        "Voorraden;3;\n"
        "EIGEN VERMOGEN;10/15;\n"
        "Omzet;70;zie toelichting\n"
        "Afschrijvingen;630;\n"
        "Waardeverminderingen;631/4;\n"
        "geen code;abc;99999\n"          # 99999 = 5 cijfers -> geen rubriek
    )
    path, count = tax_import.import_taxonomy(src)
    content = path.read_text()
    for code in ("22/27", "3", "10/15", "70", "630", "631/4", "12345"[:4]):
        pass  # individuele checks hieronder
    assert "22/27" in content and "10/15" in content and "631/4" in content
    assert "70" in content.splitlines()
    assert "abc" not in content
    assert "99999" not in content        # buiten het rubriekpatroon
    assert count >= 6


def test_import_from_xlsx_all_sheets(tax_import, tmp_path):
    src = tmp_path / "model.xlsx"
    with pl.DataFrame({"a": ["Omzet", "70"], "b": ["EBIT", "9901"]}) \
            .write_excel(src, worksheet="blad1"):
        pass
    path, count = tax_import.import_taxonomy(src)
    content = path.read_text().splitlines()
    assert "70" in content and "9901" in content


def test_import_rejects_file_without_codes(tax_import, tmp_path):
    src = tmp_path / "leeg.csv"
    src.write_text("alleen;tekst\nzonder;codes\n")
    with pytest.raises(tax_import.TaxonomyImportError, match="Geen rubriekcodes"):
        tax_import.import_taxonomy(src)


def test_import_feeds_gatekeeper(tax_import, tmp_path, data_root):
    """Na import valideert de bestaande taxonomie-loader tegen de lijst."""
    src = tmp_path / "model.csv"
    src.write_text("code\n10/15\n70\n9901\n")
    path, _ = tax_import.import_taxonomy(src)

    import screen.parse.taxonomy as taxonomy
    importlib.reload(taxonomy)
    taxo = taxonomy.load_taxonomy(path)
    assert "10/15" in taxo
    assert taxo.unknown(["10/15", "9999"]) == ["9999"]


def test_bridge_reads_xlsx(data_root, tmp_path):
    import screen.peers.nace_bridge as nb
    importlib.reload(nb)

    src = tmp_path / "conversion_2008_2025.xlsx"
    pl.DataFrame({
        "NACE 2008": ["37000", "43221", "43221"],
        "NACE 2025": ["37000", "43221", "43222"],
    }).write_excel(src)
    bridge = nb.load_bridge(src)
    assert bridge.is_one_to_many("43221") is True
    assert bridge.is_one_to_many("37000") is False
