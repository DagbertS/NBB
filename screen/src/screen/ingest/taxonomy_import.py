"""Officiële NBB-rubriekenlijst importeren -> data/raw/nbb/taxonomy/rubrics.csv.

De NBB publiceert de modellen van de jaarrekening als Excel (bv.
"Jaarrekening NL 2025.xlsx") en de XBRL-taxonomie als aparte download
(zie docs/SOURCES.md). Die bestanden zijn opgemaakte werkboeken, geen
nette tabellen; daarom scant deze import ALLE cellen van alle werkbladen
en herkent rubriekcodes op patroon (bv. "70", "630", "10/15", "631/4").

Gebruik:  screen ingest taxonomy --local ~/Downloads/"Jaarrekening NL 2025.xlsx"

Daarna draait de poortwachter-test (test_seed_codes_in_official_taxonomy)
automatisch tegen deze lijst mee bij pytest.
"""

import re
from pathlib import Path

import polars as pl

from ..config import RAW_DIR
from . import manifest

TAXONOMY_DIR = RAW_DIR / "nbb" / "taxonomy"
RUBRICS_PATH = TAXONOMY_DIR / "rubrics.csv"

# rubriekcodes: 1-4 cijfers, optioneel /1-4 cijfers ("10/15", "631/4", "9901")
RUBRIC_RE = re.compile(r"^\d{1,4}(?:/\d{1,4})?$")


class TaxonomyImportError(Exception):
    pass


def _codes_from_frame(df: pl.DataFrame) -> set[str]:
    codes: set[str] = set()
    for column in df.columns:
        series = df[column]
        if series.dtype != pl.Utf8:
            series = series.cast(pl.Utf8, strict=False)
        for value in series.drop_nulls().to_list():
            value = str(value).strip().replace(" ", "")
            if RUBRIC_RE.match(value):
                codes.add(value)
    return codes


def _read_frames(path: Path) -> list[pl.DataFrame]:
    if path.suffix.lower() in (".xlsx", ".xls"):
        sheets = pl.read_excel(path, sheet_id=0, has_header=False,
                               infer_schema_length=0)
        if isinstance(sheets, dict):
            return list(sheets.values())
        return [sheets]
    if path.suffix.lower() == ".csv":
        # Belgische exports wisselen tussen ',' en ';' — scan beide varianten.
        frames = []
        for separator in (",", ";"):
            try:
                frames.append(pl.read_csv(
                    path, separator=separator, infer_schema=False,
                    has_header=False, truncate_ragged_lines=True,
                ))
            except pl.exceptions.PolarsError:
                continue
        if not frames:
            raise TaxonomyImportError(f"{path.name} kon niet als CSV gelezen worden")
        return frames
    raise TaxonomyImportError(
        f"Onbekend formaat {path.suffix} — geef een .xlsx of .csv van de NBB"
    )


def import_taxonomy(source_file: str | Path, out_path: Path | None = None) -> tuple[Path, int]:
    """Scan het bronbestand en schrijf de gevonden rubriekcodes weg."""
    source_file = Path(source_file)
    if not source_file.exists():
        raise TaxonomyImportError(f"Bestand niet gevonden: {source_file}")

    codes: set[str] = set()
    for frame in _read_frames(source_file):
        codes |= _codes_from_frame(frame)
    if not codes:
        raise TaxonomyImportError(
            f"Geen rubriekcodes herkend in {source_file.name} — is dit het "
            "juiste NBB-bestand (modellen-Excel of taxonomie-export)?"
        )

    out_path = out_path or RUBRICS_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("code\n" + "\n".join(sorted(codes)) + "\n")
    manifest.register(out_path, source="nbb-taxonomy",
                      source_url=f"manual:{source_file.name}")
    return out_path, len(codes)
