"""Ingest van KBO Open Data (Full- en Update-zips) naar data/raw/kbo/.

Loginflow conform de officiële Fedict-downloadtool (zie docs/SOURCES.md):
loginpagina ophalen -> form-action lezen -> j_username/j_password posten.
Idempotent: reeds gedownloade en geregistreerde bestanden worden overgeslagen.
"""

import re
from pathlib import Path

import httpx

from ..config import KBO_PASSWORD, KBO_USERNAME, RAW_DIR
from . import manifest

BASE_SITE = "https://kbopub.economie.fgov.be"
PORTAL_URL = f"{BASE_SITE}/kbo-open-data"
FILES_URL = f"{PORTAL_URL}/affiliation/xml/?files"
KBO_RAW_DIR = RAW_DIR / "kbo"


class KboIngestError(Exception):
    pass


def login_session(client: httpx.Client | None = None) -> httpx.Client:
    """Ingelogde sessie op het portaal. `client` injecteerbaar voor tests."""
    client = client or httpx.Client(follow_redirects=True, timeout=120)
    if not (KBO_USERNAME and KBO_PASSWORD):
        raise KboIngestError(
            "KBO_USERNAME/KBO_PASSWORD ontbreken in .env — registreer (gratis) op "
            f"{PORTAL_URL} en vul de gegevens in"
        )
    resp = client.get(f"{PORTAL_URL}/login")
    resp.raise_for_status()
    m = re.search(r"<form[^>]+action=[\"']([^\"']+)[\"']", resp.text)
    action = m.group(1) if m else f"{PORTAL_URL}/login"
    if not action.startswith("http"):
        action = BASE_SITE + (action if action.startswith("/") else "/" + action)
    client.post(action, data={"j_username": KBO_USERNAME, "j_password": KBO_PASSWORD})
    check = client.get(FILES_URL)
    if "KboOpenData" not in check.text:
        raise KboIngestError(
            "Login op het KBO Open Data-portaal geweigerd — test de gegevens "
            f"handmatig op {PORTAL_URL}/login"
        )
    return client


def parse_file_links(html: str) -> dict[str, str]:
    """Map bestandsnaam -> href uit de bestandenpagina."""
    links: dict[str, str] = {}
    for href in re.findall(r"href=[\"']([^\"']*KboOpenData[^\"']*\.zip)[\"']", html):
        links[href.rsplit("/", 1)[-1]] = href
    return links


def resolve_href(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return BASE_SITE + href
    return f"{PORTAL_URL}/affiliation/xml/" + href


def extract_number(filename: str) -> int:
    m = re.match(r"KboOpenData_(\d+)_", filename)
    return int(m.group(1)) if m else 0


def download(client: httpx.Client, filename: str, href: str) -> Path:
    KBO_RAW_DIR.mkdir(parents=True, exist_ok=True)
    target = KBO_RAW_DIR / filename
    if target.exists() and manifest.is_registered(target):
        return target
    tmp = target.with_suffix(".part")
    with client.stream("GET", resolve_href(href)) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
    tmp.rename(target)
    manifest.register(target, source="kbo", source_url=resolve_href(href))
    return target


def ingest(full: bool = True, updates: bool = True, client: httpx.Client | None = None,
           progress=print) -> list[Path]:
    """Download de nieuwste Full-zip en/of alle Update-zips (idempotent)."""
    downloaded: list[Path] = []
    session = login_session(client)
    try:
        resp = session.get(FILES_URL)
        links = parse_file_links(resp.text)
        if not links:
            raise KboIngestError("Geen KboOpenData-bestanden gevonden op de bestandenpagina")

        wanted: list[str] = []
        if full:
            fulls = sorted((n for n in links if n.endswith("_Full.zip")), key=extract_number)
            if fulls:
                wanted.append(fulls[-1])
        if updates:
            wanted.extend(sorted((n for n in links if n.endswith("_Update.zip")),
                                 key=extract_number))
        for name in wanted:
            progress(f"  → {name}")
            downloaded.append(download(session, name, links[name]))
    finally:
        session.close()
    return downloaded
