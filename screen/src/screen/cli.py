"""CLI: screen ingest | build | rank | report <ondernemingsnummer>.

Fasestatus (docs/PLAN.md): ingest = fase 1 (af), build/rank/report volgen in
fase 2-6 en melden dat nu ook eerlijk in plaats van half werk te doen.
"""

from pathlib import Path

import typer

from . import config

app = typer.Typer(help="Screening-pipeline voor Belgische overnamedoelwitten", no_args_is_help=True)

_state: dict = {"thesis_path": None}


@app.callback()
def main(
    thesis: Path = typer.Option(
        None, "--thesis", help="Pad naar thesis.yaml (zoekcriteria van dit dossier)"
    ),
):
    _state["thesis_path"] = thesis


def _thesis() -> config.Thesis:
    return config.load_thesis(_state["thesis_path"])


@app.command()
def ingest(
    source: str = typer.Argument("all", help="kbo | statbel | nbb | all"),
    no_full: bool = typer.Option(False, help="KBO: sla de Full-zip over (alleen updates)"),
    fixtures: Path = typer.Option(
        None, help="NBB: map met voorbeeldneerleggingen (zolang er geen CBSO-key is)"
    ),
    statbel_url: list[str] = typer.Option(
        None, help="Statbel: extra/vervangende download in de vorm naam=url"
    ),
):
    """Download en versioneer ruwe bronbestanden (idempotent, met manifest)."""
    config.ensure_data_dirs()
    thesis = _thesis()
    typer.echo(f"Thesis: {thesis.name} ({len(thesis.nace_codes)} NACE-codes)")

    if source in ("kbo", "all"):
        from .ingest import kbo

        typer.echo("KBO Open Data:")
        try:
            files = kbo.ingest(full=not no_full, progress=typer.echo)
            typer.echo(f"  {len(files)} bestand(en) in data/raw/kbo/")
        except kbo.KboIngestError as exc:
            typer.secho(f"  overgeslagen: {exc}", fg="yellow")

    if source in ("statbel", "all"):
        from .ingest import statbel

        typer.echo("Statbel:")
        urls = dict(statbel.DEFAULT_URLS)
        for spec in statbel_url or []:
            name, _, url = spec.partition("=")
            urls[name] = url
        try:
            statbel.ingest(urls, progress=typer.echo)
        except Exception as exc:
            typer.secho(
                f"  overgeslagen: {exc}\n  (URL's zijn kandidaten — zie docs/SOURCES.md; "
                "download desnoods handmatig en registreer via --statbel-url of "
                "register_local)", fg="yellow",
            )

    if source in ("nbb", "all"):
        from .ingest import nbb_cbso

        typer.echo("NBB Balanscentrale:")
        try:
            if fixtures:
                nbb_cbso.ingest_fixtures(fixtures, progress=typer.echo)
            else:
                nbb_cbso.ingest_live(progress=typer.echo)
        except nbb_cbso.NbbIngestError as exc:
            typer.secho(f"  overgeslagen: {exc}", fg="yellow")


@app.command()
def build(
    strict_taxonomy: bool = typer.Option(
        False, help="Faal hard als een rubriekcode niet in de officiële taxonomie zit"
    ),
    revenue_ratio: float = typer.Option(
        None,
        help="Expliciete omzet/brutomarge-ratio voor de omzetproxy (overschrijft "
             "de peer-mediaan; zonder ratio én zonder peers wordt NIET geschat)",
    ),
    mva_min: float = typer.Option(
        0.0, help="Vervuilingsfilter: minimum materiële vaste activa voor peers"
    ),
    manual_signals: Path = typer.Option(
        None, help="YAML met handmatige/externe signalen (elk met source en as_of); "
                   "zie screen/manual_signals.example.yaml"
    ),
):
    """Parse + normalize + peer-set + benchmark + signals (fase 2-5).

    Volgorde: KBO -> facts -> universe -> peer-omzetratio -> metrics ->
    peers -> kwartielen -> signalen. Elke stap slaat netjes over als zijn
    input ontbreekt. Score/report (fase 6) volgen.
    """
    config.ensure_data_dirs()
    from . import build as build_mod
    from .normalize import build_metrics as norm
    from .peers import benchmark as bench
    from .peers import nace_bridge, peer_set

    thesis = _thesis()

    typer.echo("KBO -> parquet:")
    build_mod.build_kbo(progress=typer.echo)

    typer.echo("NBB-neerleggingen -> facts.parquet:")
    result = build_mod.build_facts(strict_taxonomy=strict_taxonomy, progress=typer.echo)
    if result.skipped:
        typer.secho(
            f"  {len(result.skipped)} neerlegging(en) overgeslagen (run liep door):",
            fg="yellow",
        )
        for name, reason in result.skipped:
            typer.echo(f"    - {name}: {reason}")

    typer.echo("Peer-universe (KBO-selectie op de thesis):")
    universe = None
    try:
        bridge = nace_bridge.load_bridge()
        if bridge is None:
            typer.echo("  Statbel-conversietabel niet geladen — 1-op-veel-vlag blijft null")
        universe = peer_set.build_universe(
            thesis, overrides=config.load_overrides(), bridge=bridge,
            progress=typer.echo,
        )
    except peer_set.PeerSetError as exc:
        typer.secho(f"  overgeslagen: {exc}", fg="yellow")

    ratio_note = ""
    if revenue_ratio is not None:
        ratio_note = "handmatige aanname via --revenue-ratio"
    elif universe is not None:
        revenue_ratio, ratio_note = peer_set.compute_revenue_ratio(universe)
        typer.echo(f"  omzet/brutomarge-ratio: "
                   f"{revenue_ratio if revenue_ratio else 'niet beschikbaar'} ({ratio_note})")

    typer.echo("Normalize -> metrics.parquet:")
    norm.build_metrics(revenue_ratio=revenue_ratio, revenue_ratio_note=ratio_note,
                       progress=typer.echo)

    if universe is not None:
        typer.echo("Peers + benchmark:")
        peer_result = peer_set.build_peers(universe, thesis, mva_min=mva_min,
                                           progress=typer.echo)
        if peer_result.peer_count:
            bench.build_benchmark(progress=typer.echo)

    typer.echo("Signalen -> signals.parquet:")
    from .signals import build_signals as sig
    from .signals.manual_input import ManualSignalError

    try:
        sig.build_signals(manual_path=manual_signals, progress=typer.echo)
    except ManualSignalError as exc:
        typer.secho(f"  handmatige signalen geweigerd: {exc}", fg="red")
        raise typer.Exit(1)

    typer.secho(
        "Build klaar (parse -> normalize -> peers -> benchmark -> signals). "
        "Rank de longlist met 'screen rank'; one-pager via 'screen report <nr>'.",
        fg="green",
    )


@app.command()
def rank(
    top: int = typer.Option(25, help="Aantal rijen dat in de terminal getoond wordt"),
):
    """Scorekaart toepassen: marts/longlist.parquet bouwen en tonen (gerankt)."""
    config.ensure_data_dirs()
    from .score import build_longlist as ll

    thesis = _thesis()
    typer.echo(f"Longlist voor thesis '{thesis.name}':")
    result = ll.build_longlist(thesis, progress=typer.echo)
    if not result.longlist_path:
        raise typer.Exit(1)

    import polars as pl

    df = pl.read_parquet(result.longlist_path).head(top)
    typer.echo("")
    typer.echo(f"{'#':>3} {'score':>6} {'nummer':<12} {'klasse':<10} "
               f"{'EBITDA':>12} {'omzet':>14} naam")
    for i, row in enumerate(df.iter_rows(named=True), 1):
        score = f"{row['score_total']:.1f}" if row["score_total"] is not None else "—"
        ebitda = f"{row['ebitda_proxy']:,.0f}" if row["ebitda_proxy"] is not None else "—"
        revenue = f"{row['revenue']:,.0f}" if row["revenue"] is not None else "—"
        if row.get("revenue_source") == "estimate" and row["revenue"] is not None:
            revenue += "*"
        typer.echo(f"{i:>3} {score:>6} {row['enterprise_number']:<12} "
                   f"{(row['target_class'] or '—'):<10} {ebitda:>12} {revenue:>14} "
                   f"{row['name'] or '—'}")
    typer.echo("\n* = geschatte omzet (source='estimate') — nooit een gepubliceerd feit")


@app.command()
def report(enterprise_number: str = typer.Argument(..., help="Ondernemingsnummer")):
    """Markdown one-pager voor één target -> data/reports/<nummer>.md."""
    config.ensure_data_dirs()
    from .report import onepager

    try:
        path = onepager.write_report(enterprise_number)
    except onepager.ReportError as exc:
        typer.secho(str(exc), fg="red")
        raise typer.Exit(1)
    typer.echo(path.read_text())
    typer.secho(f"\nOpgeslagen als {path}", fg="green")


if __name__ == "__main__":
    app()
