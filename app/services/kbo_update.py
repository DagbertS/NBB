"""Automatisch ophalen en toepassen van KBO Open Data update-zips.

De KBO publiceert maandelijks een Full-zip en een Update-zip
(KboOpenData_<nr>_<jaar>_<maand>_Update.zip) op
https://kbopub.economie.fgov.be/kbo-open-data/affiliation/xml/?files
(login vereist). De update-zip bevat *_insert.csv en *_delete.csv bestanden
per tabel; wij passen die toe op de kbo_*-tabellen en herbouwen de
betrokken rijen in kbo_company.
"""

import csv
import io
import logging
import re
import sqlite3
import zipfile
from pathlib import Path

import httpx

from ..config import DATABASE_PATH, KBO_OPEN_DATA_URL, KBO_PASSWORD, KBO_USERNAME
from ..geo import province_for_zipcode
from .kbo_import import FILES, _open_conn

log = logging.getLogger(__name__)

DOWNLOAD_DIR = DATABASE_PATH.parent / "kbo_updates"

KEY_COLUMNS = {
    "kbo_enterprise": "EnterpriseNumber",
    "kbo_activity": "EntityNumber",
    "kbo_address": "EntityNumber",
    "kbo_denomination": "EntityNumber",
    "kbo_establishment": "EstablishmentNumber",
    "kbo_code": "Code",
}


BASE_SITE = "https://kbopub.economie.fgov.be"
FILES_URL = f"{KBO_OPEN_DATA_URL}/affiliation/xml/?files"


class KboLoginError(Exception):
    pass


def _login_session() -> httpx.Client:
    """Ingelogde sessie op het KBO Open Data-portaal.

    Zelfde flow als de officiële Fedict-downloadtool: loginpagina ophalen,
    de action-URL uit het formulier lezen en daar j_username/j_password
    naartoe posten.
    """
    client = httpx.Client(follow_redirects=True, timeout=120)
    if not (KBO_USERNAME and KBO_PASSWORD):
        raise KboLoginError("KBO_USERNAME en/of KBO_PASSWORD zijn niet ingesteld")

    resp = client.get(f"{KBO_OPEN_DATA_URL}/login")
    resp.raise_for_status()
    m = re.search(r"<form[^>]+action=[\"']([^\"']+)[\"']", resp.text)
    action = m.group(1) if m else f"{KBO_OPEN_DATA_URL}/login"
    if not action.startswith("http"):
        action = BASE_SITE + (action if action.startswith("/") else "/" + action)

    resp = client.post(
        action, data={"j_username": KBO_USERNAME, "j_password": KBO_PASSWORD}
    )
    # Controle: na een geslaagde login toont de bestandenpagina de zips.
    check = client.get(FILES_URL)
    if "KboOpenData" not in check.text:
        client.close()
        raise KboLoginError(
            "Inloggen op het KBO Open Data-portaal is geweigerd — controleer "
            "KBO_USERNAME/KBO_PASSWORD (test ze handmatig op "
            "kbopub.economie.fgov.be/kbo-open-data/login)"
        )
    return client


def _file_links(html: str) -> dict[str, str]:
    """Map bestandsnaam -> href uit de bestandenpagina."""
    links: dict[str, str] = {}
    for href in re.findall(r"href=[\"']([^\"']*KboOpenData[^\"']*\.zip)[\"']", html):
        name = href.rsplit("/", 1)[-1]
        links[name] = href
    return links


def _resolve_href(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return BASE_SITE + href
    return f"{KBO_OPEN_DATA_URL}/affiliation/xml/" + href


def list_available_files() -> list[str]:
    """Bestandsnamen die het portaal aanbiedt (Full- en Update-zips)."""
    with _login_session() as client:
        resp = client.get(FILES_URL)
        resp.raise_for_status()
        return sorted(_file_links(resp.text))


def _extract_number(filename: str) -> int:
    m = re.match(r"KboOpenData_(\d+)_", filename)
    return int(m.group(1)) if m else 0


def download_file(filename: str) -> Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOAD_DIR / filename
    if target.exists():
        return target
    with _login_session() as client:
        resp = client.get(FILES_URL)
        links = _file_links(resp.text)
        href = links.get(filename)
        if not href:
            raise KboLoginError(f"{filename} niet gevonden op de bestandenpagina")
        tmp = target.with_suffix(".part")
        with client.stream("GET", _resolve_href(href)) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        tmp.rename(target)
    return target


def _refresh_company_rows(conn: sqlite3.Connection, enterprise_numbers: set[str]) -> None:
    """Herbouw kbo_company-rijen voor gewijzigde ondernemingen."""
    for num in enterprise_numbers:
        conn.execute("DELETE FROM kbo_company WHERE enterprise_number = ?", (num,))
        row = conn.execute(
            """
            SELECT e.EnterpriseNumber, e.Status, e.JuridicalForm, e.StartDate,
                   (SELECT d.Denomination FROM kbo_denomination d
                     WHERE d.EntityNumber = e.EnterpriseNumber
                     ORDER BY CASE d.TypeOfDenomination WHEN '001' THEN 0 ELSE 1 END LIMIT 1),
                   (SELECT a.Zipcode FROM kbo_address a
                     WHERE a.EntityNumber = e.EnterpriseNumber LIMIT 1),
                   (SELECT COALESCE(NULLIF(a.MunicipalityNL, ''), a.MunicipalityFR)
                      FROM kbo_address a WHERE a.EntityNumber = e.EnterpriseNumber LIMIT 1),
                   (SELECT act.NaceCode FROM kbo_activity act
                     WHERE act.EntityNumber = e.EnterpriseNumber AND act.Classification = 'MAIN'
                     ORDER BY act.NaceVersion DESC LIMIT 1)
            FROM kbo_enterprise e WHERE e.EnterpriseNumber = ?
            """,
            (num,),
        ).fetchone()
        if row:
            zipcode = row[5] or ""
            conn.execute(
                "INSERT INTO kbo_company VALUES (?,?,?,?,?,?,?,?,?)",
                (row[0], row[1], row[2], row[3], row[4] or "", zipcode, row[6] or "",
                 row[7] or "", province_for_zipcode(zipcode)),
            )


def apply_update_zip(zip_path: str | Path) -> dict:
    """Pas een Update-zip toe (delete- en insert-bestanden per tabel)."""
    zip_path = Path(zip_path)
    conn = _open_conn()
    changed: set[str] = set()
    counts: dict[str, int] = {}
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            for base, (table, columns) in FILES.items():
                stem = base.replace(".csv", "")
                key = KEY_COLUMNS[table]
                for op in ("delete", "insert"):
                    member = next(
                        (n for n in names if Path(n).name.lower() == f"{stem}_{op}.csv"), None
                    )
                    if not member:
                        continue
                    n = 0
                    with zf.open(member) as raw:
                        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
                        for row in reader:
                            key_val = row.get(key, "")
                            if op == "delete":
                                conn.execute(
                                    f'DELETE FROM {table} WHERE "{key}" = ?', (key_val,)
                                )
                            else:
                                conn.execute(
                                    f"INSERT INTO {table} VALUES ({', '.join('?' for _ in columns)})",
                                    tuple(row.get(c, "") or "" for c in columns),
                                )
                            n += 1
                            if table != "kbo_code" and key_val:
                                changed.add(key_val.split(".")[0] if "." in key_val else key_val)
                    counts[f"{table}_{op}"] = n
        _refresh_company_rows(conn, changed)
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('kbo_last_update', ?)",
            (zip_path.name,),
        )
        conn.commit()
    finally:
        conn.close()
    return counts


def check_and_apply_updates() -> list[str]:
    """Geplande taak: nieuwe Update-zips ophalen en toepassen."""
    conn = _open_conn()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key IN ('kbo_last_update','kbo_last_full_import') "
            "ORDER BY key DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    last_nr = _extract_number(row[0]) if row else 0

    applied = []
    try:
        available = list_available_files()
    except Exception as exc:  # netwerkfout of loginprobleem — loggen, niet crashen
        log.warning("KBO-portaal niet bereikbaar: %s", exc)
        return applied

    updates = sorted(
        {f for f in available if f.endswith("_Update.zip") and _extract_number(f) > last_nr},
        key=_extract_number,
    )
    for filename in updates:
        try:
            path = download_file(filename)
            apply_update_zip(path)
            applied.append(filename)
            log.info("KBO-update toegepast: %s", filename)
        except Exception:
            log.exception("KBO-update mislukt: %s", filename)
            break
    return applied
