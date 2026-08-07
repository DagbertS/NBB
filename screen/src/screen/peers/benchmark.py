"""Benchmark op de eigen peer set (spec §6): kwartielen, nooit gemiddelden.

DuckDB rechtstreeks op de parquet-bestanden:
- marts/benchmark.parquet     — per metric × boekjaar: n, Q1, mediaan, Q3
- marts/peer_positions.parquet — per onderneming × metric: percentiel
  (percent_rank) binnen de peer set van dat boekjaar

Alleen metrics met source 'nbb' of 'derived' worden gebenchmarkt tegen
elkaar; geschatte waarden ('estimate') doen niet mee aan de kwartielen —
een kwartiel op schattingen zou een schijnzekerheid zijn.
"""

from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from ..config import MARTS_DIR

BENCHMARK_PATH = MARTS_DIR / "benchmark.parquet"
POSITIONS_PATH = MARTS_DIR / "peer_positions.parquet"

BENCHMARK_METRICS = (
    "revenue", "ebitda_proxy", "ebit", "equity", "balance_total",
    "net_financial_debt", "fte", "cost_per_fte", "dso_days", "dpo_days",
    "inventory_days", "capex_intensity", "value_added",
)


@dataclass
class BenchmarkResult:
    benchmark_path: Path | None = None
    positions_path: Path | None = None
    metric_count: int = 0
    skipped_metrics: list[str] = field(default_factory=list)


def build_benchmark(peers_path: Path | None = None, progress=print) -> BenchmarkResult:
    peers_path = peers_path or (MARTS_DIR / "peers.parquet")
    result = BenchmarkResult()
    if not Path(peers_path).exists():
        progress("  geen peers.parquet — draai de peer-set-stap eerst")
        return result

    con = duckdb.connect()
    con.execute(f"CREATE VIEW peers AS SELECT * FROM read_parquet('{peers_path}')")
    columns = {row[0] for row in con.execute("DESCRIBE peers").fetchall()}

    quartile_parts = []
    position_parts = []
    for metric in BENCHMARK_METRICS:
        if metric not in columns or f"{metric}_source" not in columns:
            result.skipped_metrics.append(metric)
            continue
        # kwartielen alleen op feiten/afleidingen, nooit op schattingen
        base = (
            f"SELECT fiscal_year, {metric} AS value, enterprise_number "
            f"FROM peers WHERE {metric} IS NOT NULL "
            f"AND {metric}_source IN ('nbb', 'derived')"
        )
        quartile_parts.append(f"""
            SELECT '{metric}' AS metric, fiscal_year,
                   COUNT(*) AS n,
                   quantile_cont(value, 0.25) AS q1,
                   quantile_cont(value, 0.50) AS median,
                   quantile_cont(value, 0.75) AS q3
            FROM ({base}) GROUP BY fiscal_year
        """)
        position_parts.append(f"""
            SELECT enterprise_number, fiscal_year, '{metric}' AS metric, value,
                   percent_rank() OVER (PARTITION BY fiscal_year ORDER BY value)
                       AS percentile
            FROM ({base})
        """)

    if not quartile_parts:
        progress("  geen benchmarkbare metrics gevonden in peers.parquet")
        return result

    MARTS_DIR.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"COPY ({' UNION ALL '.join(quartile_parts)} ORDER BY metric, fiscal_year) "
        f"TO '{BENCHMARK_PATH}' (FORMAT parquet)"
    )
    con.execute(
        f"COPY ({' UNION ALL '.join(position_parts)}) "
        f"TO '{POSITIONS_PATH}' (FORMAT parquet)"
    )
    result.benchmark_path = BENCHMARK_PATH
    result.positions_path = POSITIONS_PATH
    result.metric_count = len(quartile_parts)
    progress(f"  → benchmark.parquet (Q1/mediaan/Q3, {result.metric_count} metrics) "
             "+ peer_positions.parquet")
    if result.skipped_metrics:
        progress(f"  metrics zonder kolom overgeslagen: {result.skipped_metrics}")
    return result
