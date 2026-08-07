"""Ingest van NBB-neerleggingen naar data/raw/nbb/.

Afspraak (docs/PLAN.md, 2026-08-07): zolang er geen CBSO subscription key is,
draait deze bron in fixture-modus — representatieve voorbeeldneerleggingen
worden als raw geregistreerd zodat parse (fase 2) en normalize (fase 3)
volledig gebouwd en getest kunnen worden. De echte API-koppeling volgt zodra
de key er is; de interface hieronder verandert daardoor niet.
"""

from pathlib import Path

from ..config import NBB_CBSO_SUBSCRIPTION_KEY, RAW_DIR
from . import manifest

NBB_RAW_DIR = RAW_DIR / "nbb"


class NbbIngestError(Exception):
    pass


def ingest_fixtures(fixtures_dir: str | Path, progress=print) -> list[Path]:
    """Registreer voorbeeldneerleggingen (JSON-XBRL/XBRL) als raw input."""
    fixtures_dir = Path(fixtures_dir)
    if not fixtures_dir.exists():
        raise NbbIngestError(f"Fixture-map niet gevonden: {fixtures_dir}")
    NBB_RAW_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for src in sorted(fixtures_dir.glob("*")):
        if src.suffix.lower() not in (".json", ".xbrl", ".xml"):
            continue
        target = NBB_RAW_DIR / src.name
        target.write_bytes(src.read_bytes())
        manifest.register(target, source="nbb-fixture", source_url=f"fixture:{src.name}")
        progress(f"  → {src.name} (fixture)")
        results.append(target)
    return results


def ingest_live(progress=print) -> list[Path]:
    """Echte afname via de CBSO API — wordt gebouwd zodra de key er is (fase 2+)."""
    if not NBB_CBSO_SUBSCRIPTION_KEY:
        raise NbbIngestError(
            "NBB_CBSO_SUBSCRIPTION_KEY ontbreekt — vraag het (gratis) abonnement aan "
            "via het CBSO-portaal (zie docs/SOURCES.md) of gebruik --fixtures"
        )
    raise NbbIngestError(
        "Live CBSO-ingest wordt in fase 2 gebouwd zodra de key beschikbaar is; "
        "gebruik voorlopig --fixtures (afspraak in docs/PLAN.md)"
    )
