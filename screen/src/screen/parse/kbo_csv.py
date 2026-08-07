"""Parser: KBO Open Data Full-zip -> parquet-tabellen in data/interim/kbo/.

Alle kolommen als tekst (geen type-inferentie: ondernemingsnummers en codes
zijn identifiers, geen getallen). Streaming via Polars scan/sink zodat de
multi-GB CSV's niet in het geheugen hoeven.
"""

import tempfile
import zipfile
from pathlib import Path

import polars as pl

from ..config import INTERIM_DIR

KBO_INTERIM_DIR = INTERIM_DIR / "kbo"

TABLES = ("enterprise", "activity", "address", "denomination", "establishment", "code")


class KboParseError(Exception):
    pass


def parse_zip(zip_path: str | Path, tables: tuple[str, ...] = TABLES,
              progress=print) -> dict[str, Path]:
    """Extraheer de gevraagde CSV's en schrijf ze als parquet. Idempotent
    in effect: bestaande parquets worden gewoon overschreven met dezelfde
    input -> zelfde output."""
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise KboParseError(f"Zip niet gevonden: {zip_path}")
    KBO_INTERIM_DIR.mkdir(parents=True, exist_ok=True)

    results: dict[str, Path] = {}
    with zipfile.ZipFile(zip_path) as zf:
        members = {Path(n).name.lower(): n for n in zf.namelist()}
        for table in tables:
            member = members.get(f"{table}.csv")
            if not member:
                progress(f"  ! {table}.csv niet in zip — overgeslagen")
                continue
            target = KBO_INTERIM_DIR / f"{table}.parquet"
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=True) as tmp:
                with zf.open(member) as src:
                    while chunk := src.read(1024 * 1024):
                        tmp.write(chunk)
                tmp.flush()
                (
                    pl.scan_csv(tmp.name, infer_schema=False, encoding="utf8-lossy")
                    .sink_parquet(target)
                )
            progress(f"  → {table}.parquet")
            results[table] = target
    if not results:
        raise KboParseError(f"Geen bekende CSV's gevonden in {zip_path.name}")
    return results
