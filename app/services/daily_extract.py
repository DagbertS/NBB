"""Authentic Data Daily Extract — dagelijkse zip met alle gepubliceerde neerleggingen.

Hiermee bouwen we structureel een eigen lokale database op: elke dag halen we
de extract-zip op, registreren de neerleggingen in nbb_deposits en bewaren de
bestanden in DOCUMENT_STORE/daily/<datum>/. De prompt-analyses draaien daarna
over de neerleggingen van die dag.
"""

import json
import logging
import uuid
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from ..config import DOCUMENT_STORE, NBB_CBSO_BASE_URL, NBB_CBSO_SUBSCRIPTION_KEY
from ..models import NbbDeposit, Setting

log = logging.getLogger(__name__)

EXTRACT_BASE_URL = NBB_CBSO_BASE_URL.replace("/authentic", "/extract")


def _headers() -> dict:
    return {
        "NBB-CBSO-Subscription-Key": NBB_CBSO_SUBSCRIPTION_KEY,
        "X-Request-Id": str(uuid.uuid4()),
    }


def download_daily_extract(extract_date: date) -> Path | None:
    """Download de daily-extract-zip voor een datum. None als (nog) niet beschikbaar."""
    if not NBB_CBSO_SUBSCRIPTION_KEY:
        log.warning("Geen NBB_CBSO_SUBSCRIPTION_KEY — daily extract overgeslagen")
        return None
    target_dir = Path(DOCUMENT_STORE) / "daily"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"extract_{extract_date.isoformat()}.zip"
    if target.exists():
        return target

    url = f"{EXTRACT_BASE_URL}/dailyExtract"
    with httpx.Client(timeout=600) as client:
        resp = client.get(url, headers=_headers(), params={"date": extract_date.isoformat()})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        target.write_bytes(resp.content)
    return target


def process_daily_extract(db: Session, zip_path: Path, extract_date: date) -> int:
    """Registreer alle neerleggingen uit de daily-extract-zip in nbb_deposits."""
    out_dir = Path(DOCUMENT_STORE) / "daily" / extract_date.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            name = Path(member).name
            if not name:
                continue
            reference = Path(name).stem
            if db.query(NbbDeposit).filter_by(reference=reference).first():
                continue
            data = zf.read(member)
            file_path = out_dir / name
            file_path.write_bytes(data)

            enterprise_number = ""
            if name.endswith(".json"):
                try:
                    parsed = json.loads(data)
                    enterprise_number = str(
                        parsed.get("EnterpriseNumber")
                        or parsed.get("enterpriseNumber")
                        or ""
                    ).replace(".", "")
                except json.JSONDecodeError:
                    pass

            db.add(
                NbbDeposit(
                    enterprise_number=enterprise_number,
                    reference=reference,
                    deposit_date=extract_date.isoformat(),
                    data_format="JSON" if name.endswith(".json") else name.split(".")[-1].upper(),
                    file_path=str(file_path),
                    fetched_at=datetime.utcnow(),
                )
            )
            count += 1
    db.commit()
    return count


def run_daily_sync(db: Session) -> int:
    """Geplande taak: extracts ophalen sinds de laatst verwerkte dag."""
    setting = db.get(Setting, "daily_extract_last_date")
    last = date.fromisoformat(setting.value) if setting and setting.value else date.today() - timedelta(days=1)

    total = 0
    day = last + timedelta(days=1)
    while day <= date.today():
        try:
            zip_path = download_daily_extract(day)
        except Exception:
            log.exception("Daily extract %s mislukt", day)
            break
        if zip_path:
            total += process_daily_extract(db, zip_path, day)
        if not setting:
            setting = Setting(key="daily_extract_last_date", value=day.isoformat())
            db.add(setting)
        else:
            setting.value = day.isoformat()
        db.commit()
        day += timedelta(days=1)
    return total
