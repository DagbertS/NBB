"""facts.parquet -> metrics.parquet: één rij per (onderneming, boekjaar),
elke metric met value/source/confidence/note-kolommen.

Owner-adjustment-vlaggen (spec §5) zijn hier bewust nog leeg (null) met een
reason: ze vereisen peer-benchmarks (fase 4) of toelichtingsrubrieken die
eerst tegen de taxonomie gevalideerd moeten worden. Er wordt niets
gedetecteerd dat we niet kunnen onderbouwen.
"""

from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from ..config import INTERIM_DIR
from . import metrics as M
from . import rubrics as R

METRICS_PATH = INTERIM_DIR / "metrics.parquet"

OWNER_FLAG_REASON = (
    "vereist peer-benchmark (fase 4) of gevalideerde toelichtingsrubrieken — "
    "nog niet beoordeeld"
)

METRIC_BUILDERS = {
    "equity": lambda rub, ctx: M.reported(rub, R.EIGEN_VERMOGEN),
    "balance_total": lambda rub, ctx: M.reported(rub, R.BALANSTOTAAL),
    "ebit": lambda rub, ctx: M.reported(rub, R.EBIT),
    "fte": lambda rub, ctx: M.reported(rub, R.VTE_GEMIDDELD),
    "ebitda_proxy": lambda rub, ctx: M.ebitda_proxy(rub),
    "net_financial_debt": lambda rub, ctx: M.net_financial_debt(rub),
    "revenue": lambda rub, ctx: M.revenue(rub, ctx.get("revenue_ratio"),
                                          ctx.get("revenue_ratio_note", "")),
    "value_added": lambda rub, ctx: M.value_added_proxy(rub),
    "dso_days": lambda rub, ctx: M.dso_days(rub, ctx["_revenue_metric"]),
    "dpo_days": lambda rub, ctx: M.dpo_days(rub),
    "inventory_days": lambda rub, ctx: M.inventory_days(rub),
    "capex_intensity": lambda rub, ctx: M.capex_intensity(rub),
    "cost_per_fte": lambda rub, ctx: M.cost_per_fte(rub),
}

OWNER_FLAGS = (
    "flag_management_fees",
    "flag_related_party_rent",
    "flag_director_pay_above_market",
    "flag_shareholder_current_account",
)


@dataclass
class NormalizeResult:
    metrics_path: Path | None = None
    row_count: int = 0
    estimated_revenue_count: int = 0
    notes: list[str] = field(default_factory=list)


def _rubric_maps(facts: pl.DataFrame) -> list[tuple[str, int, str, dict]]:
    """[(enterprise, jaar, schema, {rubriek: waarde})] uit de laatste
    neerlegging (is_latest) voor periode N."""
    current = facts.filter(pl.col("is_latest") & (pl.col("period") == "N"))
    groups = []
    for (enterprise, year), sub in sorted(
        current.group_by(["enterprise_number", "fiscal_year"]),
        key=lambda kv: (kv[0][0], kv[0][1]),
    ):
        rub = dict(zip(sub["rubric_code"].to_list(), sub["value"].to_list()))
        schema = sub["schema_type"][0]
        groups.append((enterprise, int(year), schema, rub))
    return groups


def build_metrics(facts_path: Path | None = None,
                  revenue_ratio: float | None = None,
                  revenue_ratio_note: str = "",
                  progress=print) -> NormalizeResult:
    facts_path = facts_path or (INTERIM_DIR / "facts.parquet")
    result = NormalizeResult()
    if not Path(facts_path).exists():
        progress("  geen facts.parquet — draai eerst screen build (parse)")
        return result

    facts = pl.read_parquet(facts_path)
    if facts.is_empty():
        progress("  facts.parquet is leeg")
        return result

    ctx_base = {"revenue_ratio": revenue_ratio, "revenue_ratio_note": revenue_ratio_note}
    rows = []
    for enterprise, year, schema, rub in _rubric_maps(facts):
        ctx = dict(ctx_base)
        ctx["_revenue_metric"] = M.revenue(rub, revenue_ratio, revenue_ratio_note)
        row: dict = {
            "enterprise_number": enterprise,
            "fiscal_year": year,
            "schema_type": schema,
        }
        for name, builder in METRIC_BUILDERS.items():
            metric = builder(rub, ctx)
            row[name] = metric.value
            row[f"{name}_source"] = metric.source
            row[f"{name}_confidence"] = metric.confidence
            row[f"{name}_note"] = metric.note or None
        for flag in OWNER_FLAGS:
            row[flag] = None
            row[f"{flag}_note"] = OWNER_FLAG_REASON
        rows.append(row)
        if row.get("revenue_source") == "estimate":
            result.estimated_revenue_count += 1

    df = pl.DataFrame(rows)
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(METRICS_PATH)
    result.metrics_path = METRICS_PATH
    result.row_count = len(df)
    progress(f"  → {METRICS_PATH.name}: {result.row_count} rijen (onderneming × boekjaar)")
    if result.estimated_revenue_count:
        progress(f"  {result.estimated_revenue_count} rij(en) met GESCHATTE omzet "
                 "(source='estimate', confidence='low')")
    return result
