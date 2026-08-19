"""Client voor de NBB CBSO API (Balanscentrale) — officiële neerleggingen.

De CBSO-webservices ('Consult authentic financial statements data') geven per
ondernemingsnummer alle gepubliceerde neerleggingen (referenties) en per
referentie de jaarrekeninggegevens (XBRL/JSON of PDF).

Authenticatie: subscription key uit het CBSO-portaal (NBB_CBSO_SUBSCRIPTION_KEY
in .env). Elke request krijgt een unieke X-Request-Id mee.
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from ..config import (
    DOCUMENT_STORE,
    NBB_CBSO_ARCHIVE_KEY,
    NBB_CBSO_ARCHIVE_URL,
    NBB_CBSO_BASE_URL,
    NBB_CBSO_SUBSCRIPTION_KEY,
)
from ..models import NbbDeposit

log = logging.getLogger(__name__)


class CbsoError(Exception):
    pass


def _headers(accept: str = "application/json", key: str | None = None) -> dict:
    key = key or NBB_CBSO_SUBSCRIPTION_KEY
    if not key:
        raise CbsoError(
            "NBB_CBSO_SUBSCRIPTION_KEY ontbreekt in .env — maak een subscription key "
            "aan op het CBSO-portaal van de NBB."
        )
    return {
        "NBB-CBSO-Subscription-Key": key,
        "X-Request-Id": str(uuid.uuid4()),
        "Accept": accept,
    }


def _normalize_number(enterprise_number: str) -> str:
    return enterprise_number.replace(".", "").replace(" ", "")


def get_references(enterprise_number: str) -> list[dict]:
    """Alle gepubliceerde neerleggingen (referenties) van een onderneming."""
    num = _normalize_number(enterprise_number)
    url = f"{NBB_CBSO_BASE_URL}/legalEntity/{num}/references"
    with httpx.Client(timeout=60) as client:
        resp = client.get(url, headers=_headers())
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
    if isinstance(data, dict):
        data = data.get("references", data.get("items", []))
    return data or []


# Kandidaat-adressen voor de archief-service ("Authentic Archive Data").
# Het juiste pad staat niet in openbaar bereikbare documentatie; de knop
# "Test archief-verbinding" probeert ze op een echte archief-referentie en
# onthoudt wat werkt (setting cbso_archive_url wint dan van de env-variabele).
ARCHIVE_URL_CANDIDATES = [
    "https://ws.cbso.nbb.be/authentic-archive",
    "https://ws.cbso.nbb.be/archive",
    "https://ws.cbso.nbb.be/archives",
    "https://ws.cbso.nbb.be/authentic/archive",
    "https://ws.cbso.nbb.be/authentic-archive-data",
    "https://ws.cbso.nbb.be/archived",
    "https://ws.cbso.nbb.be/authentic-archived",
    "https://ws.cbso.nbb.be/authenticarchive",
    "https://ws.cbso.nbb.be/archive-authentic",
    "https://ws.cbso.nbb.be/historical",
    # afgeleid van de interne productnaam "AuthenticArchiveData"
    "https://ws.cbso.nbb.be/authenticarchivedata",
    "https://ws.cbso.nbb.be/AuthenticArchiveData",
    "https://ws.cbso.nbb.be/archivedata",
    "https://ws.cbso.nbb.be/archive-data",
]

ARCHIVE_URL_SETTING = "cbso_archive_url"


def _archive_base_url() -> str:
    """Basisadres van de archief-service: een door de verbindingstest
    gevonden adres wint van de omgevingsvariabele."""
    import sqlite3

    from ..config import DATABASE_PATH

    try:
        with sqlite3.connect(DATABASE_PATH, timeout=10) as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (ARCHIVE_URL_SETTING,)
            ).fetchone()
        if row and row[0]:
            return row[0]
    except sqlite3.OperationalError:
        pass
    return NBB_CBSO_ARCHIVE_URL


def save_archive_url(base_url: str) -> None:
    import sqlite3

    from ..config import DATABASE_PATH

    with sqlite3.connect(DATABASE_PATH, timeout=30) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT DEFAULT '')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (ARCHIVE_URL_SETTING, base_url),
        )
        conn.commit()


def find_archived_reference(db: Session) -> tuple[str, str, dict]:
    """Zoek een echte archief-referentie (neergelegd vóór april 2022) om de
    verbindingstest op te draaien. Kijkt bij bedrijven met een resterende
    fout en geeft (ondernemingsnummer, referentie, ruw referentie-record)
    terug — dat ruwe record toont hoe oude referenties eruitzien en of er
    bv. een rechtstreekse document-URL in staat."""
    from ..models import CompanyListItem

    items = (
        db.query(CompanyListItem)
        .filter(CompanyListItem.nbb_error != "")
        .limit(25)
        .all()
    )
    for item in items:
        num = _normalize_number(item.enterprise_number)
        try:
            references = get_references(num)
        except httpx.HTTPError:
            continue
        for ref in references:
            reference = str(ref.get("ReferenceNumber") or ref.get("reference")
                            or ref.get("id") or "")
            deposit_date, _, _ = _ref_fields(ref)
            if reference and deposit_date[:7] and deposit_date[:7] < "2022-04":
                return num, reference, ref
    raise CbsoError(
        "geen archief-referentie gevonden om mee te testen — geen bedrijf "
        "met een resterende fout heeft een neerlegging van vóór april 2022"
    )


def probe_archive_candidates(reference: str) -> list[dict]:
    """Probeer alle kandidaat-adressen van de archief-service op één
    archief-referentie en rapporteer letterlijk wat elk adres antwoordt."""
    if not NBB_CBSO_ARCHIVE_KEY:
        raise CbsoError("NBB_CBSO_ARCHIVE_KEY ontbreekt — zonder key valt er "
                        "niets te testen")
    candidates: list[tuple[str, str, str]] = []   # (label, base, key)
    seen = set()
    for base in [_archive_base_url(), *ARCHIVE_URL_CANDIDATES]:
        if base not in seen:
            seen.add(base)
            candidates.append((base, base, NBB_CBSO_ARCHIVE_KEY))
    # misschien is het pad gewoon dat van de hoofdservice, met de archief-key
    candidates.append((f"{NBB_CBSO_BASE_URL} (met archief-key)",
                       NBB_CBSO_BASE_URL, NBB_CBSO_ARCHIVE_KEY))
    # (label, url, key, accept, kind) — kind bepaalt wat een succes betekent:
    # 'archive-base' → dit basisadres bewaren; 'operation' → deze operatie
    # wordt automatisch gebruikt bij het ophalen
    probes: list[tuple[str, str, str, str, str]] = [
        (label, f"{base}/deposit/{reference}/accountingData", key,
         "application/x.jsonxbrl", "archive-base")
        for label, base, key in candidates
    ]
    # de hoofdservice met de gewone key: het vólledige antwoord (incl. de
    # validatie-boodschap) vertelt waaróm een oude referentie daar 404 geeft
    probes.append((f"{NBB_CBSO_BASE_URL} (met hoofd-key)",
                   f"{NBB_CBSO_BASE_URL}/deposit/{reference}/accountingData",
                   NBB_CBSO_SUBSCRIPTION_KEY, "application/x.jsonxbrl",
                   "diagnostic"))
    # de technische gids noemt naast accountingData een aparte operatie
    # "PDF representation" — oude neerleggingen (PDF-beelden sinds 1999)
    # bestaan mogelijk alléén daar
    probes += [
        (f"{NBB_CBSO_BASE_URL}/deposit/…/pdf (met hoofd-key)",
         f"{NBB_CBSO_BASE_URL}/deposit/{reference}/pdf",
         NBB_CBSO_SUBSCRIPTION_KEY, "application/pdf", "operation"),
        (f"{NBB_CBSO_BASE_URL}/deposit/…/document (met hoofd-key)",
         f"{NBB_CBSO_BASE_URL}/deposit/{reference}/document",
         NBB_CBSO_SUBSCRIPTION_KEY, "application/pdf", "operation"),
        (f"{NBB_CBSO_BASE_URL}/deposit/…/accountingData met Accept: "
         "application/pdf (met hoofd-key)",
         f"{NBB_CBSO_BASE_URL}/deposit/{reference}/accountingData",
         NBB_CBSO_SUBSCRIPTION_KEY, "application/pdf", "operation"),
    ]

    results = []
    with httpx.Client(timeout=30) as client:
        for label, url, key, accept, kind in probes:
            base = url.split("/deposit/")[0]
            try:
                resp = client.get(url, headers=_headers(accept, key))
                content_type = resp.headers.get("content-type", "")
                snippet = "" if resp.status_code == 200 \
                    else " ".join(resp.text[:400].split())
                results.append({
                    "label": label, "base": base, "kind": kind,
                    "status": resp.status_code, "content_type": content_type,
                    "snippet": snippet,
                    # 200 = data; 406 = bestaat, maar ander formaat (bv. enkel
                    # PDF) — beide bewijzen dat het adres klopt
                    "works": resp.status_code in (200, 406),
                })
            except httpx.HTTPError as exc:
                results.append({"label": label, "base": base, "kind": kind,
                                "status": None, "content_type": "",
                                "snippet": f"{type(exc).__name__}: {exc}",
                                "works": False})
    return results


def _get_accounting_from(base_url: str, key: str | None,
                         reference: str) -> dict | bytes:
    url = f"{base_url}/deposit/{reference}/accountingData"
    with httpx.Client(timeout=120) as client:
        resp = client.get(url, headers=_headers("application/x.jsonxbrl", key))
        if resp.status_code in (406, 404):
            # geen gestructureerde data — probeer PDF. Oude neerleggingen
            # (vóór het CBSO-platform van april 2022) antwoorden op de
            # JSON-vraag zelfs 404 ("Reference Number not found") maar
            # bestaan wél als PDF-beeld — live vastgesteld 2026-08-19 op
            # referentie 2017-05200202.
            resp = client.get(url, headers=_headers("application/pdf", key))
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            return resp.json()
        if "pdf" in content_type or "octet-stream" in content_type:
            return resp.content
        raise CbsoError(
            f"onverwacht antwoordformaat van de NBB voor {reference}: "
            f"content-type {content_type or '?'}"
        )


def _extract_error_detail(resp: httpx.Response) -> str:
    """Compacte, vólledige foutuitleg uit een NBB-antwoord: de
    violation-boodschappen als die er zijn, anders message/title, anders een
    stukje ruwe tekst."""
    try:
        data = resp.json()
    except ValueError:
        return " ".join(resp.text[:200].split())
    if isinstance(data, dict):
        msgs = []
        for v in data.get("violations") or []:
            if isinstance(v, dict):
                msg = v.get("defaultMessage") or v.get("message") or v.get("field")
                if msg:
                    msgs.append(str(msg))
        if msgs:
            return "; ".join(msgs)
        return str(data.get("message") or data.get("title")
                   or " ".join(resp.text[:200].split()))
    return " ".join(resp.text[:200].split())


def _get_pdf_operation(base_url: str, key: str | None,
                       reference: str) -> bytes:
    """Aparte 'PDF representation'-operatie uit de technische gids — oudere
    neerleggingen (PDF-beelden sinds 1999) bestaan mogelijk alléén hier."""
    with httpx.Client(timeout=120) as client:
        resp = None
        for suffix in ("pdf", "document"):
            url = f"{base_url}/deposit/{reference}/{suffix}"
            resp = client.get(url, headers=_headers("application/pdf", key))
            if resp.status_code == 200:
                break
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "pdf" not in content_type and "octet-stream" not in content_type:
            raise CbsoError(
                f"pdf-operatie gaf geen PDF voor {reference} "
                f"(content-type: {content_type or '?'})"
            )
        return resp.content


def get_accounting_data(reference: str) -> dict | bytes:
    """Jaarrekeninggegevens voor één referentie (JSON indien beschikbaar,
    anders PDF).

    Oudere neerleggingen geven op accountingData een HTTP 404; daarvoor
    proberen we achtereenvolgens de aparte PDF-operatie van dezelfde service
    en (als er een archief-key is) de archief-service.
    """
    try:
        return _get_accounting_from(NBB_CBSO_BASE_URL,
                                    NBB_CBSO_SUBSCRIPTION_KEY, reference)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        detail = _extract_error_detail(exc.response)

        try:
            return _get_pdf_operation(NBB_CBSO_BASE_URL,
                                      NBB_CBSO_SUBSCRIPTION_KEY, reference)
        except (httpx.HTTPError, CbsoError):
            pass

        if NBB_CBSO_ARCHIVE_KEY:
            try:
                return _get_accounting_from(_archive_base_url(),
                                            NBB_CBSO_ARCHIVE_KEY, reference)
            except (httpx.HTTPError, CbsoError):
                pass

        raise CbsoError(
            f"document niet beschikbaar bij de NBB — de NBB zegt: "
            f"\"{detail or 'HTTP 404 zonder detail'}\" (ook als PDF en via "
            "het archief niet verkrijgbaar)"
        ) from exc


def fetch_pdf(deposit) -> Path:
    """Officiële PDF-versie van een neerlegging ophalen bij de NBB (en lokaal
    cachen naast het JSON-bestand). Elke neerlegging bestaat ook als PDF —
    dezelfde accountingData-endpoint met Accept: application/pdf."""
    base_dir = Path(deposit.file_path).parent if deposit.file_path \
        else Path(DOCUMENT_STORE) / _normalize_number(deposit.enterprise_number)
    target = base_dir / f"{deposit.reference}.pdf"
    if target.exists():
        return target

    ref = deposit.reference
    urls = [
        (f"{NBB_CBSO_BASE_URL}/deposit/{ref}/accountingData",
         NBB_CBSO_SUBSCRIPTION_KEY),
        (f"{NBB_CBSO_BASE_URL}/deposit/{ref}/pdf", NBB_CBSO_SUBSCRIPTION_KEY),
    ]
    if NBB_CBSO_ARCHIVE_KEY:
        urls.append((f"{_archive_base_url()}/deposit/{ref}/accountingData",
                     NBB_CBSO_ARCHIVE_KEY))
    with httpx.Client(timeout=120) as client:
        resp = None
        for url, key in urls:
            resp = client.get(url, headers=_headers("application/pdf", key))
            if resp.status_code == 200:
                break
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "pdf" not in content_type:
            raise CbsoError(
                f"NBB gaf geen PDF terug voor {deposit.reference} "
                f"(content-type: {content_type})"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(resp.content)
    return target


def probe_accounting_data(reference: str) -> str:
    """Diagnose: wat antwoordt de API letterlijk op een JSON-XBRL-vraag voor
    deze referentie? Gebruikt om in het rapport te tonen waarom een
    neerlegging alleen als PDF binnenkwam."""
    url = f"{NBB_CBSO_BASE_URL}/deposit/{reference}/accountingData"
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(url, headers=_headers("application/x.jsonxbrl"))
        content_type = resp.headers.get("content-type", "?")
        detail = f"HTTP {resp.status_code}, content-type {content_type}"
        if resp.status_code != 200:
            snippet = " ".join(resp.text[:200].split())
            if snippet:
                detail += f" — antwoord: {snippet}"
        return detail
    except CbsoError as exc:
        return f"geen key: {exc}"
    except Exception as exc:
        return f"probe mislukt: {type(exc).__name__}: {exc}"


def _ref_fields(ref: dict) -> tuple[str, str, str]:
    """(deposit_date, exercise_end, model_type) uit een referentie-record."""
    deposit_date = str(ref.get("DepositDate") or ref.get("depositDate") or "")
    exercise_end = str(
        ref.get("ExerciseDates", {}).get("endDate", "")
        if isinstance(ref.get("ExerciseDates"), dict)
        else ref.get("exerciseEndDate", "")
    )
    model_type = str(ref.get("ModelType") or ref.get("modelType") or "")
    return deposit_date, exercise_end, model_type


def _file_ok(deposit: NbbDeposit) -> bool:
    """Staat het document van deze neerlegging echt bruikbaar op de schijf?"""
    if not deposit.file_path:
        return False
    path = Path(deposit.file_path)
    try:
        if not path.exists() or path.stat().st_size == 0:
            return False
        if path.suffix.lower() == ".json":
            json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return True


def verify_and_repair(db: Session, enterprise_number: str) -> dict:
    """Vergelijk de NBB-referentielijst met wat lokaal aanwezig is en haal
    alléén het ontbrekende of kapotte opnieuw op (delta).

    Herstelt de gevallen die bij een eerste ophaling stil konden mislukken:
    een referentie zonder databaserij, een rij waarvan het bestand van de
    schijf verdween, of een leeg/corrupt JSON-bestand. Mislukkingen worden
    per referentie met reden teruggegeven — nooit stilzwijgend overgeslagen.
    """
    import time

    num = _normalize_number(enterprise_number)
    references = get_references(num)
    target_dir = Path(DOCUMENT_STORE) / num
    result: dict = {"references": 0, "ok": 0, "added": 0, "repaired": 0,
                    "failed": [], "unavailable": []}

    for ref in references:
        reference = str(ref.get("ReferenceNumber") or ref.get("reference")
                        or ref.get("id") or "")
        if not reference:
            continue
        result["references"] += 1
        row = db.query(NbbDeposit).filter_by(reference=reference).first()
        if row is not None and _file_ok(row):
            result["ok"] += 1
            continue

        data = None
        for attempt in (1, 2):
            try:
                data = get_accounting_data(reference)
                break
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 429 and attempt == 1:
                    time.sleep(30)   # NBB-tempolimiet: even wachten en opnieuw
                    continue
                body = _extract_error_detail(exc.response)
                result["failed"].append(
                    (reference, f"HTTP {status}" + (f" — {body}" if body else ""))
                )
                break
            except CbsoError as exc:
                msg = str(exc)
                # "No deposit found" e.d.: het document bestáát niet bij de
                # NBB (vaak vervangen of geannuleerde neerleggingen) — dat is
                # informatie over de bron, geen op te lossen fout
                bucket = "unavailable" \
                    if msg.startswith("document niet beschikbaar") else "failed"
                result[bucket].append((reference, msg))
                break
            except httpx.HTTPError as exc:
                result["failed"].append((reference, f"{type(exc).__name__}: {exc}"))
                break
        if data is None:
            continue

        target_dir.mkdir(parents=True, exist_ok=True)
        if isinstance(data, (dict, list)):
            file_path = target_dir / f"{reference}.json"
            file_path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
            data_format = "JSON"
        else:
            file_path = target_dir / f"{reference}.pdf"
            file_path.write_bytes(data)
            data_format = "PDF"

        deposit_date, exercise_end, model_type = _ref_fields(ref)
        if row is None:
            row = NbbDeposit(enterprise_number=num, reference=reference)
            db.add(row)
            result["added"] += 1
        else:
            result["repaired"] += 1
        row.deposit_date = deposit_date
        row.exercise_end = exercise_end
        row.model_type = model_type
        row.data_format = data_format
        row.file_path = str(file_path)
        row.raw_json = json.dumps(ref, ensure_ascii=False)
        row.fetched_at = datetime.utcnow()
        db.commit()
        time.sleep(0.4)   # rustig tempo richting de NBB-API
    return result


def fetch_and_store(db: Session, enterprise_number: str) -> list[NbbDeposit]:
    """Haal alle neerleggingen van een onderneming op en sla ze lokaal op.

    Documenten komen in DOCUMENT_STORE/<ondernemingsnummer>/, metadata in de
    tabel nbb_deposits. Reeds opgehaalde referenties worden overgeslagen.
    """
    num = _normalize_number(enterprise_number)
    references = get_references(num)
    stored: list[NbbDeposit] = []
    target_dir = Path(DOCUMENT_STORE) / num
    target_dir.mkdir(parents=True, exist_ok=True)

    for ref in references:
        reference = str(ref.get("ReferenceNumber") or ref.get("reference") or ref.get("id") or "")
        if not reference:
            continue
        existing = db.query(NbbDeposit).filter_by(reference=reference).first()
        if existing:
            stored.append(existing)
            continue
        try:
            data = get_accounting_data(reference)
        except (httpx.HTTPStatusError, CbsoError) as exc:
            log.warning("Referentie %s niet opgehaald: %s", reference, exc)
            continue

        if isinstance(data, (dict, list)):
            file_path = target_dir / f"{reference}.json"
            file_path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
            data_format = "JSON"
        else:
            file_path = target_dir / f"{reference}.pdf"
            file_path.write_bytes(data)
            data_format = "PDF"

        deposit = NbbDeposit(
            enterprise_number=num,
            reference=reference,
            deposit_date=str(ref.get("DepositDate") or ref.get("depositDate") or ""),
            exercise_end=str(ref.get("ExerciseDates", {}).get("endDate", "") if isinstance(ref.get("ExerciseDates"), dict) else ref.get("exerciseEndDate", "")),
            model_type=str(ref.get("ModelType") or ref.get("modelType") or ""),
            data_format=data_format,
            file_path=str(file_path),
            raw_json=json.dumps(ref, ensure_ascii=False),
            fetched_at=datetime.utcnow(),
        )
        db.add(deposit)
        db.commit()
        stored.append(deposit)
    return stored
