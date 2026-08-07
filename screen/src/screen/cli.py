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
    source: str = typer.Argument("all", help="kbo | statbel | nbb | taxonomy | all"),
    no_full: bool = typer.Option(False, help="KBO: sla de Full-zip over (alleen updates)"),
    fixtures: Path = typer.Option(
        None, help="NBB: map met voorbeeldneerleggingen (zolang er geen CBSO-key is)"
    ),
    statbel_url: list[str] = typer.Option(
        None, help="Statbel: extra/vervangende download in de vorm naam=url"
    ),
    local: list[Path] = typer.Option(
        None, help="taxonomy: lokaal NBB-bestand (model-PDF, taxonomie-zip, xlsx "
                   "of csv); herhaal de optie om meerdere modellen samen te voegen"
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

    if source == "taxonomy":
        from .ingest import taxonomy_import

        if not local:
            typer.secho(
                "Geef de gedownloade NBB-bestand(en) mee, bv.: screen ingest taxonomy "
                "--local ~/Downloads/volledigmodel.pdf --local ~/Downloads/"
                "verkortmodel.pdf (zie docs/SOURCES.md)",
                fg="red",
            )
            raise typer.Exit(1)
        try:
            path, count = taxonomy_import.import_taxonomy(list(local))
        except taxonomy_import.TaxonomyImportError as exc:
            typer.secho(str(exc), fg="red")
            raise typer.Exit(1)
        typer.echo(f"  → {path}: {count} rubriekcodes")
        typer.echo("  Draai nu 'python -m pytest screen/tests -q' — de poortwachter-test "
                   "valideert de gebruikte codes tegen deze lijst.")

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
    from . import orchestrate
    from .signals.manual_input import ManualSignalError

    thesis = _thesis()
    try:
        orchestrate.run_build(
            thesis,
            strict_taxonomy=strict_taxonomy,
            revenue_ratio=revenue_ratio,
            mva_min=mva_min,
            manual_signals=manual_signals,
            progress=typer.echo,
        )
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
