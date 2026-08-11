"""Orkestratie van de volledige build-keten (parse -> normalize -> peers ->
benchmark -> signals), gedeeld door de CLI (`screen build`) en de webapp.

Elke stap slaat netjes over als zijn input ontbreekt; alleen geweigerde
handmatige signalen (ManualSignalError) stoppen de run, want die wijzen op
een invoerfout die de gebruiker moet herstellen.
"""

from pathlib import Path

from . import build as build_mod
from . import config


def run_build(
    thesis: config.Thesis,
    *,
    strict_taxonomy: bool = False,
    revenue_ratio: float | None = None,
    mva_min: float = 0.0,
    manual_signals: Path | None = None,
    universe_override=None,
    progress=print,
) -> dict:
    """Draai de volledige build. Geeft een samenvatting terug; laat
    ManualSignalError doorborrelen naar de aanroeper."""
    config.ensure_data_dirs()
    from .normalize import build_metrics as norm
    from .peers import benchmark as bench
    from .peers import nace_bridge, peer_set
    from .signals import build_signals as sig

    summary: dict = {}

    progress("KBO -> parquet:")
    build_mod.build_kbo(progress=progress)

    progress("NBB-neerleggingen -> facts.parquet:")
    facts = build_mod.build_facts(strict_taxonomy=strict_taxonomy, progress=progress)
    if facts.skipped:
        progress(f"  {len(facts.skipped)} neerlegging(en) overgeslagen (run liep door):")
        for name, reason in facts.skipped:
            progress(f"    - {name}: {reason}")
    summary["skipped_deposits"] = facts.skipped

    universe = None
    if universe_override is not None:
        # de selectie komt uit een door de gebruiker gebouwde longlist,
        # niet uit de thesis-criteria
        progress("Peer-universe (geselecteerde longlist):")
        config.INTERIM_DIR.mkdir(parents=True, exist_ok=True)
        universe_override.write_parquet(peer_set.UNIVERSE_PATH)
        universe = universe_override
        progress(f"  → universe.parquet: {universe.height} ondernemingen uit de lijst")
    else:
        progress("Peer-universe (KBO-selectie op de thesis):")
        try:
            bridge = nace_bridge.load_bridge()
            if bridge is None:
                progress("  Statbel-conversietabel niet geladen — 1-op-veel-vlag blijft null")
            universe = peer_set.build_universe(
                thesis, overrides=config.load_overrides(), bridge=bridge,
                progress=progress,
            )
        except peer_set.PeerSetError as exc:
            progress(f"  overgeslagen: {exc}")

    ratio_note = ""
    if revenue_ratio is not None:
        ratio_note = "handmatige aanname via --revenue-ratio"
    elif universe is not None:
        revenue_ratio, ratio_note = peer_set.compute_revenue_ratio(universe)
        progress(f"  omzet/brutomarge-ratio: "
                 f"{revenue_ratio if revenue_ratio else 'niet beschikbaar'} ({ratio_note})")

    progress("Normalize -> metrics.parquet:")
    norm.build_metrics(revenue_ratio=revenue_ratio, revenue_ratio_note=ratio_note,
                       progress=progress)

    if universe is not None:
        progress("Peers + benchmark:")
        peer_result = peer_set.build_peers(universe, thesis, mva_min=mva_min,
                                           progress=progress)
        if peer_result.peer_count:
            bench.build_benchmark(progress=progress)

    progress("Signalen -> signals.parquet:")
    sig.build_signals(manual_path=manual_signals, progress=progress)

    summary["universe_rows"] = 0 if universe is None else universe.height
    return summary
