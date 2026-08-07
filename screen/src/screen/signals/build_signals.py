"""Signals-opbouw: neerleggingssignalen + handmatige signalen ->
marts/signals.parquet. Strikte scheiding feit/afleiding via `kind`
('derived' uit officiële metadata, 'manual' met verplichte source/as_of)."""

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from ..config import INTERIM_DIR, MARTS_DIR
from . import filing, manual_input

SIGNALS_PATH = MARTS_DIR / "signals.parquet"

SIGNAL_SCHEMA = {
    "enterprise_number": pl.Utf8,
    "fiscal_year": pl.Int32,
    "signal": pl.Utf8,
    "value": pl.Utf8,
    "source": pl.Utf8,
    "as_of": pl.Utf8,
    "kind": pl.Utf8,
    "note": pl.Utf8,
}


@dataclass
class SignalsResult:
    signals_path: Path | None = None
    counts: dict = field(default_factory=dict)
    total: int = 0


def build_signals(facts_path: Path | None = None,
                  manual_path: str | Path | None = None,
                  progress=print) -> SignalsResult:
    result = SignalsResult()
    rows: list[dict] = []

    facts_path = facts_path or (INTERIM_DIR / "facts.parquet")
    if Path(facts_path).exists():
        facts = pl.read_parquet(facts_path)
        if not facts.is_empty():
            rows.extend(filing.build_filing_signals(facts))
    else:
        progress("  geen facts.parquet — alleen handmatige signalen")

    if manual_path:
        rows.extend(manual_input.load_manual_signals(manual_path))

    if not rows:
        progress("  geen signalen gevonden")
        return result

    df = pl.DataFrame(rows, schema=SIGNAL_SCHEMA)
    MARTS_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(SIGNALS_PATH)

    result.signals_path = SIGNALS_PATH
    result.total = len(df)
    result.counts = dict(Counter(df["signal"].to_list()))
    progress(f"  → signals.parquet: {result.total} signalen "
             + ", ".join(f"{k}={v}" for k, v in sorted(result.counts.items())))
    return result
