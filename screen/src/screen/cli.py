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
        help="Expliciete omzet/brutomarge-ratio voor de omzetproxy bij verkort/"
             "micro-schema (bewuste aanname; zonder deze optie wordt NIET geschat)",
    ),
):
    """Parse + normalize (fase 2-3): KBO-parquets, facts.parquet, metrics.parquet.

    Peer-set/benchmark/signals volgen in fase 4-5.
    """
    config.ensure_data_dirs()
    from . import build as build_mod
    from .normalize import build_metrics as norm

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

    typer.echo("Normalize -> metrics.parquet:")
    norm.build_metrics(
        revenue_ratio=revenue_ratio,
        revenue_ratio_note="handmatige aanname via --revenue-ratio" if revenue_ratio else "",
        progress=typer.echo,
    )

    typer.secho(
        "Klaar t/m normalize. Peer-set + benchmark (fase 4) volgen — zie docs/PLAN.md.",
        fg="green",
    )


@app.command()
def rank():
    """Scorekaart toepassen en longlist ranken (fase 6)."""
    typer.secho("Nog niet beschikbaar: 'rank' volgt in fase 6 (zie docs/PLAN.md).", fg="yellow")
    raise typer.Exit(1)


@app.command()
def report(enterprise_number: str = typer.Argument(..., help="Ondernemingsnummer")):
    """Markdown one-pager voor één target (fase 6)."""
    typer.secho("Nog niet beschikbaar: 'report' volgt in fase 6 (zie docs/PLAN.md).", fg="yellow")
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
