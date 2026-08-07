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

from ..config import DOCUMENT_STORE, NBB_CBSO_BASE_URL, NBB_CBSO_SUBSCRIPTION_KEY
from ..models import NbbDeposit

log = logging.getLogger(__name__)


class CbsoError(Exception):
    pass


def _headers(accept: str = "application/json") -> dict:
    if not NBB_CBSO_SUBSCRIPTION_KEY:
        raise CbsoError(
            "NBB_CBSO_SUBSCRIPTION_KEY ontbreekt in .env — maak een subscription key "
            "aan op het CBSO-portaal van de NBB."
        )
    return {
        "NBB-CBSO-Subscription-Key": NBB_CBSO_SUBSCRIPTION_KEY,
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


def get_accounting_data(reference: str) -> dict | bytes:
    """Jaarrekeninggegevens voor één referentie (JSON indien beschikbaar, anders PDF)."""
    url = f"{NBB_CBSO_BASE_URL}/deposit/{reference}/accountingData"
    with httpx.Client(timeout=120) as client:
        resp = client.get(url, headers=_headers("application/x.jsonxbrl"))
        if resp.status_code == 406:
            # geen gestructureerde data — probeer PDF
            resp = client.get(url, headers=_headers("application/pdf"))
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            return resp.json()
        return resp.content


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
        except httpx.HTTPStatusError as exc:
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
