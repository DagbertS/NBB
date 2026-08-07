"""`screen build` fase 2: raw -> tidy.

- KBO: nieuwste Full-zip -> parquet-tabellen (interim/kbo/)
- NBB: alle JSON-neerleggingen in raw/nbb/ -> interim/facts.parquet, met:
  * is_latest per (onderneming, boekjaar): nieuwste neerleggingsdatum wint —
    een verbeterende neerlegging verdringt de eerdere (superseded=true,
    zelf ook een signaal in fase 5)
  * code_in_taxonomy: true/false indien de officiële taxonomie geladen is,
    anders null (onbekend — nooit stilzwijgend goedgekeurd)
  * neerleggingen die niet parsen stoppen de run niet: ze worden verzameld
    en gerapporteerd (acceptatiecriterium: doorlopen bij ontbrekende jaren)
"""

from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from .config import INTERIM_DIR, RAW_DIR
from .parse import jsonxbrl, kbo_csv, taxonomy

FACTS_PATH = INTERIM_DIR / "facts.parquet"

FACT_SCHEMA = {
    "enterprise_number": pl.Utf8,
    "fiscal_year": pl.Int32,
    "schema_type": pl.Utf8,
    "rubric_code": pl.Utf8,
    "period": pl.Utf8,
    "value": pl.Float64,
    "deposit_reference": pl.Utf8,
    "deposit_date": pl.Utf8,
    "model": pl.Utf8,
    "source": pl.Utf8,
    "as_of": pl.Utf8,
}


@dataclass
class BuildResult:
    facts_path: Path | None = None
    fact_count: int = 0
    deposit_count: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (bestand, reden)
    taxonomy_loaded: bool = False
    unknown_codes: list[str] = field(default_factory=list)


def latest_full_zip() -> Path | None:
    kbo_dir = RAW_DIR / "kbo"
    if not kbo_dir.exists():
        return None
    fulls = sorted(kbo_dir.glob("KboOpenData_*_Full.zip"))
    return fulls[-1] if fulls else None


def build_kbo(progress=print) -> dict[str, Path]:
    zip_path = latest_full_zip()
    if not zip_path:
        progress("  geen KBO Full-zip in data/raw/kbo/ — sla over (draai eerst: screen ingest kbo)")
        return {}
    progress(f"  bron: {zip_path.name}")
    return kbo_csv.parse_zip(zip_path, progress=progress)


def build_facts(strict_taxonomy: bool = False, progress=print) -> BuildResult:
    result = BuildResult()
    nbb_dir = RAW_DIR / "nbb"
    deposits = sorted(nbb_dir.glob("*.json")) if nbb_dir.exists() else []

    all_rows: list[dict] = []
    for path in deposits:
        try:
            rows = jsonxbrl.parse_deposit(path)
        except jsonxbrl.ParseError as exc:
            result.skipped.append((path.name, str(exc)))
            progress(f"  ! {path.name} overgeslagen: {exc}")
            continue
        result.deposit_count += 1
        all_rows.extend(rows)

    if not all_rows:
        progress("  geen parsebare NBB-neerleggingen in data/raw/nbb/")
        return result

    df = pl.DataFrame(all_rows, schema=FACT_SCHEMA)

    # is_latest / superseded per (onderneming, boekjaar): nieuwste depot wint
    latest_ref = (
        df.group_by(["enterprise_number", "fiscal_year"])
        .agg(pl.col("deposit_reference")
             .sort_by(["deposit_date", "deposit_reference"])
             .last().alias("_latest_ref"))
    )
    df = (
        df.join(latest_ref, on=["enterprise_number", "fiscal_year"])
        .with_columns((pl.col("deposit_reference") == pl.col("_latest_ref")).alias("is_latest"))
        .with_columns((~pl.col("is_latest")).alias("superseded"))
        .drop("_latest_ref")
    )

    # Taxonomie-validatie: true/false indien geladen, anders null (onbekend)
    taxo = taxonomy.load_taxonomy()
    if taxo:
        result.taxonomy_loaded = True
        known = {c: (c in taxo) for c in df["rubric_code"].unique().to_list()}
        result.unknown_codes = sorted(c for c, ok in known.items() if not ok)
        df = df.with_columns(
            pl.col("rubric_code").replace_strict(known, return_dtype=pl.Boolean)
            .alias("code_in_taxonomy")
        )
        if result.unknown_codes:
            msg = (f"  ! {len(result.unknown_codes)} rubriekcode(s) niet in de "
                   f"officiële taxonomie: {', '.join(result.unknown_codes[:10])}")
            if strict_taxonomy:
                raise taxonomy.TaxonomyError(msg.strip())
            progress(msg)
    else:
        df = df.with_columns(pl.lit(None, dtype=pl.Boolean).alias("code_in_taxonomy"))
        progress("  taxonomie niet geladen (data/raw/nbb/taxonomy/rubrics.csv ontbreekt) "
                 "— code_in_taxonomy = null, zie docs/SOURCES.md")

    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(FACTS_PATH)
    result.facts_path = FACTS_PATH
    result.fact_count = len(df)
    progress(f"  → {FACTS_PATH.name}: {result.fact_count} feiten uit "
             f"{result.deposit_count} neerleggingen"
             + (f", {len(result.skipped)} overgeslagen" if result.skipped else ""))
    return result
