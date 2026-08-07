"""KBO-zip -> parquet: rijen intact, alles als tekst (identifiers), en
ontbrekende CSV's netjes overslaan."""

import csv
import importlib
import io
import zipfile

import polars as pl
import pytest


def make_zip(path, tables: dict[str, list[list[str]]]):
    with zipfile.ZipFile(path, "w") as zf:
        for name, rows in tables.items():
            buf = io.StringIO()
            csv.writer(buf).writerows(rows)
            zf.writestr(name, buf.getvalue())
    return path


@pytest.fixture
def kbo_csv_mod(data_root):
    import screen.parse.kbo_csv as mod
    importlib.reload(mod)
    return mod


def test_parse_zip(kbo_csv_mod, tmp_path):
    zip_path = make_zip(tmp_path / "full.zip", {
        "enterprise.csv": [
            ["EnterpriseNumber", "Status", "JuridicalForm", "StartDate"],
            ["0123.456.789", "AC", "014", "01-01-2001"],
            ["0987.654.321", "AC", "015", "15-06-2010"],
        ],
        "activity.csv": [
            ["EntityNumber", "NaceVersion", "NaceCode", "Classification"],
            ["0123.456.789", "2025", "37000", "MAIN"],
        ],
    })
    results = kbo_csv_mod.parse_zip(zip_path, progress=lambda *_: None)
    assert set(results) == {"enterprise", "activity"}

    ent = pl.read_parquet(results["enterprise"])
    assert ent.height == 2
    assert ent["EnterpriseNumber"].to_list() == ["0123.456.789", "0987.654.321"]
    # identifiers blijven tekst — geen type-inferentie
    assert all(dtype == pl.Utf8 for dtype in ent.dtypes)

    act = pl.read_parquet(results["activity"])
    assert act["NaceCode"].to_list() == ["37000"]  # geen 37000 -> 37000.0


def test_zip_without_known_tables_raises(kbo_csv_mod, tmp_path):
    zip_path = make_zip(tmp_path / "leeg.zip", {"readme.txt": [["x"]]})
    with pytest.raises(kbo_csv_mod.KboParseError, match="Geen bekende"):
        kbo_csv_mod.parse_zip(zip_path, progress=lambda *_: None)


def test_missing_zip_raises(kbo_csv_mod, tmp_path):
    with pytest.raises(kbo_csv_mod.KboParseError, match="niet gevonden"):
        kbo_csv_mod.parse_zip(tmp_path / "bestaat_niet.zip")
