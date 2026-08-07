"""Ingest van Statbel-nomenclatuur (NACE-BEL 2025) en de conversietabel
2008↔2025 naar data/raw/statbel/.

De standaard-URL's zijn kandidaten uit docs/SOURCES.md (status: unverified —
deze bouw-omgeving kan *.fgov.be niet bereiken). Ze zijn overschrijfbaar via
CLI-opties, en een handmatig gedownload bestand kan met `register_local`
in het manifest opgenomen worden.
"""

from pathlib import Path

import httpx

from ..config import RAW_DIR
from . import manifest

STATBEL_RAW_DIR = RAW_DIR / "statbel"

# Kandidaat-URL's — zie docs/SOURCES.md, gevonden 2026-08-07, unverified.
DEFAULT_URLS = {
    "conversion_2008_2025.xlsx": (
        "https://economie.fgov.be/sites/default/files/Files/Entreprises/KBO/"
        "conversion-NACEBEL-2008-2025.xlsx"
    ),
    # Exacte bestands-URL van de NACE-BEL 2025-nomenclatuur is nog niet
    # geverifieerd; gebruik zolang `register_local` of een --url-optie.
}


class StatbelIngestError(Exception):
    pass


def download(name: str, url: str, client: httpx.Client | None = None) -> Path:
    STATBEL_RAW_DIR.mkdir(parents=True, exist_ok=True)
    target = STATBEL_RAW_DIR / name
    if target.exists() and manifest.is_registered(target):
        return target
    own_client = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=300)
    try:
        tmp = target.with_suffix(".part")
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        tmp.rename(target)
    finally:
        if own_client:
            client.close()
    manifest.register(target, source="statbel", source_url=url)
    return target


def register_local(path: str | Path) -> Path:
    """Neem een handmatig gedownload Statbel-bestand op in raw + manifest."""
    src = Path(path)
    if not src.exists():
        raise StatbelIngestError(f"Bestand niet gevonden: {src}")
    STATBEL_RAW_DIR.mkdir(parents=True, exist_ok=True)
    target = STATBEL_RAW_DIR / src.name
    if src.resolve() != target.resolve():
        target.write_bytes(src.read_bytes())
    manifest.register(target, source="statbel", source_url=f"manual:{src.name}")
    return target


def ingest(urls: dict[str, str] | None = None, client: httpx.Client | None = None,
           progress=print) -> list[Path]:
    results = []
    for name, url in (urls or DEFAULT_URLS).items():
        progress(f"  → {name}")
        results.append(download(name, url, client=client))
    return results
