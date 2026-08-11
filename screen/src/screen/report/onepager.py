"""Markdown one-pager per target (spec §8): identiteit, 5 jaar
genormaliseerde cijfers (schattingen expliciet gemarkeerd), positie t.o.v.
peer-kwartielen, signalen, scoreopbouw en openstaande dataroom-vragen."""

from pathlib import Path

import polars as pl

from ..config import INTERIM_DIR, MARTS_DIR, REPORTS_DIR

REPORT_METRICS = [
    ("revenue", "Omzet"),
    ("ebitda_proxy", "EBITDA-proxy"),
    ("ebit", "EBIT"),
    ("equity", "Eigen vermogen"),
    ("balance_total", "Balanstotaal"),
    ("net_financial_debt", "Netto fin. schuld"),
    ("fte", "VTE"),
    ("cost_per_fte", "Personeelskost/VTE"),
    ("dso_days", "Klantendagen"),
    ("capex_intensity", "Capex-intensiteit"),
]

BENCH_METRICS = ("revenue", "ebitda_proxy", "equity", "net_financial_debt")


class ReportError(Exception):
    pass


def _fmt(value, metric: str = "") -> str:
    if value is None:
        return "—"
    if metric in ("dso_days", "dpo_days", "fte"):
        return f"{value:,.1f}"
    if metric == "capex_intensity":
        return f"{value:.2f}"
    return f"{value:,.0f}"


def _norm(number: str) -> str:
    return str(number).replace(".", "").replace(" ", "").strip()


def _load(path: Path) -> pl.DataFrame | None:
    return pl.read_parquet(path) if path.exists() else None


def generate_report(enterprise_number: str,
                    marts_dir: Path | None = None) -> str:
    """One-pager; marts_dir wijst optioneel naar de map van één specifieke
    pipeline (longlist/benchmark/posities). Metrics en signalen zijn
    bedrijfsniveau en blijven gedeeld."""
    marts = Path(marts_dir) if marts_dir else MARTS_DIR
    num = _norm(enterprise_number)
    longlist = _load(marts / "longlist.parquet")
    metrics = _load(INTERIM_DIR / "metrics.parquet")
    benchmark = _load(marts / "benchmark.parquet")
    positions = _load(marts / "peer_positions.parquet")
    signals = _load(MARTS_DIR / "signals.parquet")

    if longlist is None:
        raise ReportError("marts/longlist.parquet ontbreekt — draai eerst screen rank")
    hit = longlist.filter(pl.col("enterprise_number") == num)
    if hit.height == 0:
        raise ReportError(f"{num} staat niet in de longlist (buiten de thesis-criteria?)")
    row = hit.row(0, named=True)

    lines: list[str] = []
    add = lines.append

    # ── identiteit ────────────────────────────────────────────────────
    add(f"# {row.get('name') or 'Naam onbekend'} — {num}")
    add("")
    add(f"| | |\n|---|---|")
    add(f"| Ondernemingsnummer | {num} |")
    add(f"| NACE (effectief) | {row.get('nace') or '—'} |")
    add(f"| Provincie | {row.get('province') or '—'} |")
    add(f"| Laatste boekjaar | {row.get('fiscal_year') or '—'} ({row.get('schema_type') or '?'} schema) |")
    add(f"| Classificatie | {row.get('target_class') or 'onbekend (geen VTE-data)'} |")
    add(f"| Binnen groottevork | {'ja' if row.get('in_size_range') else 'nee' if row.get('in_size_range') is False else 'onbekend'} |")
    if row.get("nace_conversion_ambiguous"):
        add(f"| ⚠ NACE-conversie | 2008-code had meerdere 2025-tegenhangers — activiteit verifiëren |")
    add("")

    # ── 5 jaar cijfers ────────────────────────────────────────────────
    add("## Genormaliseerde cijfers (max. 5 boekjaren)")
    add("")
    company_metrics = (
        metrics.filter(pl.col("enterprise_number") == num).sort("fiscal_year")
        if metrics is not None else pl.DataFrame()
    )
    if company_metrics.is_empty():
        add("_Geen financiële gegevens lokaal beschikbaar._")
    else:
        years = company_metrics["fiscal_year"].to_list()[-5:]
        sub = company_metrics.filter(pl.col("fiscal_year").is_in(years))
        add("| Metric | " + " | ".join(str(y) for y in years) + " |")
        add("|---" * (len(years) + 1) + "|")
        estimates_used = False
        for metric, label in REPORT_METRICS:
            if metric not in sub.columns:
                continue
            cells = []
            for y in years:
                r = sub.filter(pl.col("fiscal_year") == y)
                if r.height == 0:
                    cells.append("—")
                    continue
                value = r[metric][0]
                source = r[f"{metric}_source"][0] if f"{metric}_source" in r.columns else None
                cell = _fmt(value, metric)
                if source == "estimate" and value is not None:
                    cell += " *(schatting)*"
                    estimates_used = True
                cells.append(cell)
            add(f"| {label} | " + " | ".join(cells) + " |")
        add("")
        if estimates_used:
            add("> *(schatting)* = afgeleide waarde (bv. omzetproxy uit brutomarge), "
                "géén gepubliceerd feit — zie docs/METHODOLOGY.md.")
            add("")

    # ── peer-positie ──────────────────────────────────────────────────
    add("## Positie t.o.v. peer-kwartielen")
    add("")
    year = row.get("fiscal_year")
    if benchmark is None or year is None:
        add("_Geen peer-benchmark beschikbaar (te weinig peers of nog niet gebouwd)._")
    else:
        bench_year = benchmark.filter(pl.col("fiscal_year") == year)
        if bench_year.is_empty():
            add(f"_Geen benchmark voor boekjaar {year}._")
        else:
            add("| Metric | Q1 | Mediaan | Q3 | Target | Percentiel |")
            add("|---|---|---|---|---|---|")
            for metric in BENCH_METRICS:
                b = bench_year.filter(pl.col("metric") == metric)
                if b.height == 0:
                    continue
                b = b.row(0, named=True)
                pos = ""
                if positions is not None:
                    p = positions.filter(
                        (pl.col("enterprise_number") == num)
                        & (pl.col("metric") == metric) & (pl.col("fiscal_year") == year)
                    )
                    pos = f"p{p['percentile'][0]:.2f}" if p.height else "buiten peers"
                add(f"| {metric} (n={b['n']}) | {_fmt(b['q1'])} | {_fmt(b['median'])} "
                    f"| {_fmt(b['q3'])} | {_fmt(row.get(metric))} | {pos} |")
    add("")

    # ── signalen ──────────────────────────────────────────────────────
    add("## Signalen")
    add("")
    company_signals = (
        signals.filter(pl.col("enterprise_number") == num).sort("as_of")
        if signals is not None else pl.DataFrame()
    )
    if company_signals.is_empty():
        add("_Geen signalen geregistreerd._")
    else:
        add("| Signaal | Waarde | Bron | Datum |")
        add("|---|---|---|---|")
        for s in company_signals.iter_rows(named=True):
            add(f"| {s['signal']} | {s['value']} | {s['source']} | {s['as_of']} |")
    add("")

    # ── score ─────────────────────────────────────────────────────────
    add("## Score-opbouw (transparant)")
    add("")
    add(f"**Totaal: {row.get('score_total') if row.get('score_total') is not None else '—'}** — {row.get('score_total_basis')}")
    add("")
    for comp in ("financial", "growth", "signals"):
        add(f"- {comp}: {row.get(f'score_{comp}') if row.get(f'score_{comp}') is not None else '—'} "
            f"— {row.get(f'score_{comp}_basis')}")
    add("")

    # ── dataroom-vragen ───────────────────────────────────────────────
    add("## Openstaande vragen voor de dataroom")
    add("")
    for q in dataroom_questions(row, company_signals):
        add(f"- {q}")
    add("")
    add("---")
    add("_Gegenereerd door de screen-pipeline; methodologie en aannames: "
        "docs/METHODOLOGY.md. Geschatte waarden zijn nooit feiten._")
    return "\n".join(lines)


def dataroom_questions(row: dict, signals: pl.DataFrame) -> list[str]:
    q: list[str] = []
    if row.get("revenue") is None or row.get("revenue_source") == "estimate":
        q.append("Werkelijke omzetcijfers en omzetsplitsing opvragen "
                 "(niet gepubliceerd in verkort/micro-schema).")
    q.append("EBITDA-brug: management fees, huur aan verbonden patrimonium-"
             "vennootschap, bestuurdersbezoldiging en rekening-courant zaakvoerder "
             "opvragen (owner-adjustments nog niet beoordeeld).")
    if row.get("nace_conversion_ambiguous"):
        q.append("Werkelijke hoofdactiviteit verifiëren: de NACE-2008-code had "
                 "meerdere 2025-tegenhangers.")
    if row.get("fte") is None:
        q.append("Sociale balans / personeelsdetail opvragen (VTE onbekend).")
    if row.get("net_financial_debt") is None:
        q.append("Schuldenoverzicht en leasingverplichtingen opvragen.")
    signal_types = set(signals["signal"].to_list()) if signals.height else set()
    if {"late_filing", "chronic_late_filing"} & signal_types:
        q.append("Verklaring vragen voor laattijdige neerlegging(en).")
    if "model_switch_full_to_abbreviated" in signal_types:
        q.append("Reden van de modelwissel volledig → verkort bevragen.")
    if "correction_filing" in signal_types:
        q.append("Toelichting bij de verbeterende neerlegging(en) opvragen.")
    return q


def write_report(enterprise_number: str) -> Path:
    num = _norm(enterprise_number)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{num}.md"
    path.write_text(generate_report(num))
    return path
