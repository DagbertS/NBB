"""Assemblage van marts/longlist.parquet (spec §8): één rij per onderneming
met identiteit, laatste metrics, peer-percentielen, signaaltellingen en de
transparante score (elke component met zijn basis-string als kolom)."""

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from ..config import INTERIM_DIR, MARTS_DIR, Thesis
from . import scorecard as sc

LONGLIST_PATH = MARTS_DIR / "longlist.parquet"

GROWTH_METRIC_PREFERENCE = ("ebitda_proxy", "equity")


@dataclass
class LonglistResult:
    longlist_path: Path | None = None
    row_count: int = 0


def _signal_counts(signals: pl.DataFrame, enterprise: str) -> tuple[dict, int]:
    sub = signals.filter(pl.col("enterprise_number") == enterprise)
    derived = sub.filter(pl.col("kind") == "derived")
    counts: dict[str, int] = {}
    for row in derived.group_by("signal").len().iter_rows(named=True):
        counts[row["signal"]] = row["len"]
    manual_count = sub.filter(pl.col("kind") == "manual").height
    return counts, manual_count


def _percentiles(positions: pl.DataFrame | None, enterprise: str,
                 year: int | None) -> dict[str, float | None]:
    result: dict[str, float | None] = {m: None for m in sc.FINANCIAL_METRICS}
    if positions is None or year is None:
        return result
    sub = positions.filter(
        (pl.col("enterprise_number") == enterprise) & (pl.col("fiscal_year") == year)
    )
    for row in sub.iter_rows(named=True):
        if row["metric"] in result:
            result[row["metric"]] = row["percentile"]
    return result


def _growth_series(metrics: pl.DataFrame, enterprise: str) -> tuple[list, str]:
    sub = metrics.filter(pl.col("enterprise_number") == enterprise)
    for metric in GROWTH_METRIC_PREFERENCE:
        series = [(int(r["fiscal_year"]), r[metric])
                  for r in sub.iter_rows(named=True) if r.get(metric) is not None]
        if len(series) >= 2:
            return series, metric
    return [], GROWTH_METRIC_PREFERENCE[0]


def _in_size_range(row: dict, thesis: Thesis) -> bool | None:
    checks = []
    size = thesis.size
    if size.balance_total_min is not None or size.balance_total_max is not None:
        bt = row.get("balance_total")
        if bt is None:
            return None
        checks.append((size.balance_total_min or 0) <= bt
                      <= (size.balance_total_max or float("inf")))
    if size.fte_min is not None or size.fte_max is not None:
        fte = row.get("fte")
        if fte is None:
            return None
        checks.append((size.fte_min or 0) <= fte <= (size.fte_max or float("inf")))
    return all(checks) if checks else True


def build_longlist(thesis: Thesis, progress=print) -> LonglistResult:
    result = LonglistResult()
    universe_path = INTERIM_DIR / "universe.parquet"
    metrics_path = INTERIM_DIR / "metrics.parquet"
    if not universe_path.exists():
        progress("  geen universe.parquet — draai screen build met KBO-data")
        return result
    universe = pl.read_parquet(universe_path)
    metrics = pl.read_parquet(metrics_path) if metrics_path.exists() else pl.DataFrame()

    positions_path = MARTS_DIR / "peer_positions.parquet"
    positions = pl.read_parquet(positions_path) if positions_path.exists() else None
    signals_path = MARTS_DIR / "signals.parquet"
    signals = (pl.read_parquet(signals_path) if signals_path.exists()
               else pl.DataFrame(schema={"enterprise_number": pl.Utf8, "signal": pl.Utf8,
                                         "kind": pl.Utf8}))

    latest_metrics = (
        metrics.sort("fiscal_year").group_by("enterprise_number", maintain_order=True)
        .agg(pl.all().last())
        if not metrics.is_empty() else pl.DataFrame()
    )

    rows = []
    for uni in universe.iter_rows(named=True):
        enterprise = uni["enterprise_number"]
        met = {}
        if not latest_metrics.is_empty():
            hit = latest_metrics.filter(pl.col("enterprise_number") == enterprise)
            if hit.height:
                met = hit.row(0, named=True)

        year = met.get("fiscal_year")
        percentiles = _percentiles(positions, enterprise, year)
        counts, manual_count = _signal_counts(signals, enterprise)
        series, growth_metric = _growth_series(metrics, enterprise) \
            if not metrics.is_empty() else ([], "ebitda_proxy")

        components = {
            "financial": sc.financial_score(percentiles),
            "growth": sc.growth_score(series, growth_metric),
            "signals": sc.signals_score(counts),
        }
        total = sc.total_score(components, thesis.weights or None)

        row = {
            "enterprise_number": enterprise,
            "name": uni.get("name"),
            "nace": uni.get("nace_effective"),
            "province": uni.get("province"),
            "nace_conversion_ambiguous": uni.get("nace_conversion_ambiguous"),
            "override_included": uni.get("override_included"),
            "fiscal_year": year,
            "schema_type": met.get("schema_type"),
            "revenue": met.get("revenue"),
            "revenue_source": met.get("revenue_source"),
            "ebitda_proxy": met.get("ebitda_proxy"),
            "net_financial_debt": met.get("net_financial_debt"),
            "equity": met.get("equity"),
            "balance_total": met.get("balance_total"),
            "fte": met.get("fte"),
            "in_size_range": _in_size_range(met, thesis),
            "target_class": sc.classify_target(met.get("fte"),
                                               thesis.platform_criteria,
                                               thesis.bolt_on_criteria),
            "signals_derived_count": sum(counts.values()),
            "signals_manual_count": manual_count,
            "score_financial": components["financial"].value,
            "score_financial_basis": components["financial"].basis,
            "score_growth": components["growth"].value,
            "score_growth_basis": components["growth"].basis,
            "score_signals": components["signals"].value,
            "score_signals_basis": components["signals"].basis,
            "score_total": total.value,
            "score_total_basis": total.basis,
        }
        rows.append(row)

    if not rows:
        progress("  lege universe — geen longlist")
        return result

    # infer_schema_length=None: kolomtypes uit ÁLLE rijen afleiden — de eerste
    # honderden bedrijven hebben vaak geen cijfers (null), waarna de eerste
    # echte waarde anders een ComputeError geeft
    df = pl.DataFrame(rows, infer_schema_length=None) \
        .sort("score_total", descending=True, nulls_last=True)
    MARTS_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(LONGLIST_PATH)
    result.longlist_path = LONGLIST_PATH
    result.row_count = len(df)
    progress(f"  → longlist.parquet: {result.row_count} ondernemingen (gerankt op score)")
    return result
