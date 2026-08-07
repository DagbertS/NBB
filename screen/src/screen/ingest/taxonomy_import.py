"""Officiële NBB-rubriekenlijst importeren -> data/raw/nbb/taxonomy/rubrics.csv.

De NBB publiceert de modellen van de jaarrekening als PDF (pagina "Modellen
vennootschappen") en de XBRL-taxonomie als zip-download (zie docs/SOURCES.md).
Geen van beide is een nette tabel; daarom scant deze import de volledige
inhoud en herkent rubriekcodes op patroon (bv. "70", "630", "10/15", "631/4").

Ondersteunde formaten: .pdf (modellen), .zip (taxonomie), .xlsx/.xls en .csv.

Vals-positieven (bv. jaartallen die op een code lijken) zijn onschadelijk:
extra codes maken de poortwachter-validatie hoogstens milder, nooit fout —
de test faalt alleen als een DOOR ONS GEBRUIKTE code in de lijst ontbreekt.

Gebruik:  screen ingest taxonomy --local ~/Downloads/volledig-model.pdf
"""

import io
import re
import zipfile
from pathlib import Path

import polars as pl

from ..config import RAW_DIR
from . import manifest

TAXONOMY_DIR = RAW_DIR / "nbb" / "taxonomy"
RUBRICS_PATH = TAXONOMY_DIR / "rubrics.csv"

# rubriekcodes: 1-4 cijfers, optioneel /1-4 cijfers ("10/15", "631/4", "9901")
RUBRIC_RE = re.compile(r"^\d{1,4}(?:/\d{1,4})?$")
# let op: '/' NIET in de splitser — dat is deel van codes zoals 10/15
TOKEN_SPLIT_RE = re.compile(r"[\s;,()\[\]{}«»<>=\"']+")

ZIP_TEXT_SUFFIXES = (".xml", ".xsd", ".csv", ".txt", ".htm", ".html", ".json")


class TaxonomyImportError(Exception):
    pass


def _codes_from_text(text: str) -> set[str]:
    codes: set[str] = set()
    for token in TOKEN_SPLIT_RE.split(text):
        token = token.strip().rstrip(".:")
        if RUBRIC_RE.match(token):
            codes.add(token)
    return codes


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


def _codes_from_excel(path: Path) -> set[str]:
    sheets = pl.read_excel(path, sheet_id=0, has_header=False, infer_schema_length=0)
    frames = list(sheets.values()) if isinstance(sheets, dict) else [sheets]
    codes: set[str] = set()
    for frame in frames:
        codes |= _codes_from_frame(frame)
    return codes


def _codes_from_csv(path: Path) -> set[str]:
    # Belgische exports wisselen tussen ',' en ';' — scan beide varianten.
    codes: set[str] = set()
    read_ok = False
    for separator in (",", ";"):
        try:
            frame = pl.read_csv(path, separator=separator, infer_schema=False,
                                has_header=False, truncate_ragged_lines=True)
        except pl.exceptions.PolarsError:
            continue
        read_ok = True
        codes |= _codes_from_frame(frame)
    if not read_ok:
        raise TaxonomyImportError(f"{path.name} kon niet als CSV gelezen worden")
    return codes


def _codes_from_pdf(path: Path) -> set[str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise TaxonomyImportError(
            "pypdf ontbreekt — installeer met: uv sync (of pip install pypdf)"
        ) from exc
    reader = PdfReader(str(path))
    codes: set[str] = set()
    for page in reader.pages:
        codes |= _codes_from_text(page.extract_text() or "")
    return codes


def _codes_from_zip(path: Path) -> set[str]:
    codes: set[str] = set()
    with zipfile.ZipFile(path) as zf:
        for member in zf.namelist():
            suffix = Path(member).suffix.lower()
            if suffix in ZIP_TEXT_SUFFIXES:
                codes |= _codes_from_text(
                    zf.read(member).decode("utf-8", errors="replace")
                )
            elif suffix == ".pdf":
                try:
                    from pypdf import PdfReader
                except ImportError:  # pragma: no cover
                    continue
                reader = PdfReader(io.BytesIO(zf.read(member)))
                for page in reader.pages:
                    codes |= _codes_from_text(page.extract_text() or "")
    return codes


def import_taxonomy(source_file: str | Path, out_path: Path | None = None) -> tuple[Path, int]:
    """Scan het bronbestand en schrijf de gevonden rubriekcodes weg."""
    source_file = Path(source_file)
    if not source_file.exists():
        raise TaxonomyImportError(f"Bestand niet gevonden: {source_file}")

    suffix = source_file.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        codes = _codes_from_excel(source_file)
    elif suffix == ".csv":
        codes = _codes_from_csv(source_file)
    elif suffix == ".pdf":
        codes = _codes_from_pdf(source_file)
    elif suffix == ".zip":
        codes = _codes_from_zip(source_file)
    else:
        raise TaxonomyImportError(
            f"Onbekend formaat {suffix} — geef een .pdf, .zip, .xlsx of .csv van de NBB"
        )

    if not codes:
        raise TaxonomyImportError(
            f"Geen rubriekcodes herkend in {source_file.name} — is dit het "
            "juiste NBB-bestand (model-PDF, taxonomie-zip of rubriekenlijst)?"
        )

    out_path = out_path or RUBRICS_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("code\n" + "\n".join(sorted(codes)) + "\n")
    manifest.register(out_path, source="nbb-taxonomy",
                      source_url=f"manual:{source_file.name}")
    return out_path, len(codes)
