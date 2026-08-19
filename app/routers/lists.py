from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..audit import log_action
from ..database import SessionLocal, get_db
from ..models import CompanyList, CompanyListItem, User
from ..security import get_current_user
from ..templating import templates

router = APIRouter()


def _get_list(db: Session, list_id: int, user: User) -> CompanyList:
    company_list = db.get(CompanyList, list_id)
    if not company_list:
        raise HTTPException(404)
    if user.role != "admin" and company_list.owner_id != user.id:
        raise HTTPException(403)
    return company_list


@router.get("/lists")
def lists_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(CompanyList)
    if user.role != "admin":
        query = query.filter(CompanyList.owner_id == user.id)
    lists = query.order_by(CompanyList.created_at.desc()).all()
    return templates.TemplateResponse(request, "lists.html", {"user": user, "lists": lists})


@router.get("/lists/{list_id}")
def list_detail(
    list_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    company_list = _get_list(db, list_id, user)

    # werkelijk aantal opgehaalde NBB-documenten per bedrijf (live geteld —
    # de nbb_status van een item kan achterlopen als documenten via een
    # individuele screening binnenkwamen)
    from sqlalchemy import func

    from ..models import NbbDeposit

    def _norm(number: str) -> str:
        return (number or "").replace(".", "").replace(" ", "")

    numbers = {_norm(item.enterprise_number) for item in company_list.items}
    counts: dict[str, int] = {}
    if numbers:
        for number, count in (
            db.query(NbbDeposit.enterprise_number, func.count(NbbDeposit.id))
            .filter(NbbDeposit.enterprise_number.in_(numbers))
            .group_by(NbbDeposit.enterprise_number)
        ):
            counts[number] = count
    nbb_counts = {item.id: counts.get(_norm(item.enterprise_number), 0)
                  for item in company_list.items}
    error_summary = _error_summary(company_list.items)
    return templates.TemplateResponse(
        request, "list_detail.html",
        {"user": user, "l": company_list, "nbb_counts": nbb_counts,
         "check_status": _get_list_status(list_id),
         "error_summary": error_summary}
    )


def _error_reason(err: str) -> str:
    """Herleid een per-bedrijf foutmelding tot de kern, zodat identieke
    oorzaken gegroepeerd kunnen worden (referentienummers en aantallen eruit)."""
    import re

    m = re.search(r"— bv\. [^:]+: (.+)$", err)
    reason = m.group(1) if m else err
    reason = re.sub(r"/deposit/[^/]+/", "/deposit/…/", reason)   # referenties
    reason = re.sub(r"[0-9a-fA-F-]{8,}", "…", reason)   # request-ids e.d.
    reason = re.sub(r"\d{1,2}:\d{2}(:\d{2})?", "…", reason)   # tijdstempels
    return " ".join(reason.split())[:300]


def _error_summary(items) -> list[tuple[str, int]]:
    """Gegroepeerde foutredenen over alle bedrijven van de lijst, meest
    voorkomende eerst."""
    from collections import Counter

    counter = Counter(
        _error_reason(item.nbb_error) for item in items if item.nbb_error
    )
    return counter.most_common()


@router.get("/lists/{list_id}/errors.csv")
def errors_csv(
    list_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alle bedrijven met een resterend NBB-probleem, met de volledige
    foutreden — handig om te delen of verder te analyseren."""
    import csv
    import io

    from fastapi.responses import Response

    company_list = _get_list(db, list_id, user)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["ondernemingsnummer", "naam", "nbb_status", "foutreden"])
    for item in company_list.items:
        if item.nbb_error:
            writer.writerow([item.enterprise_number, item.name,
                             item.nbb_status, item.nbb_error])
    return Response(
        buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="lijst_{list_id}_nbb_fouten.csv"'},
    )


def _set_list_status(list_id: int, text: str) -> None:
    """Voortgang van de lijst-controle, schrijfbaar vanuit een achtergrondthread
    (zelfde patroon als de screening-status)."""
    import sqlite3

    from ..config import DATABASE_PATH

    with sqlite3.connect(DATABASE_PATH, timeout=60) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT DEFAULT '')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (f"nbb_check_{list_id}", text),
        )
        conn.commit()


def _get_list_status(list_id: int) -> str:
    import sqlite3

    from ..config import DATABASE_PATH

    try:
        with sqlite3.connect(DATABASE_PATH, timeout=60) as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (f"nbb_check_{list_id}",)
            ).fetchone()
        return row[0] if row else ""
    except sqlite3.OperationalError:
        return ""


def _verify_list_nbb(list_id: int) -> None:
    """Achtergrondtaak: vergelijk per bedrijf de NBB-referentielijst met wat
    lokaal echt op de schijf staat en haal alléén het ontbrekende of kapotte
    opnieuw op (delta). Herstelt bedrijven die als 'opgehaald' staan terwijl
    er documenten ontbreken."""
    import logging
    import time

    import httpx

    from ..services.cbso_client import verify_and_repair

    log = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        items = (
            db.query(CompanyListItem)
            .filter(CompanyListItem.list_id == list_id)
            .all()
        )
        total = len(items)
        added = repaired = gaps = unavailable = 0
        for i, item in enumerate(items, 1):
            _set_list_status(
                list_id,
                f"bezig: controle {i}/{total} — "
                f"{item.name or item.enterprise_number}",
            )
            for attempt in (1, 2):
                try:
                    result = verify_and_repair(db, item.enterprise_number)
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status in (429, 500, 502, 503, 504) and attempt == 1:
                        # tempolimiet of tijdelijke NBB-storing:
                        # even wachten en opnieuw
                        time.sleep(30 if status == 429 else 10)
                        continue
                    item.nbb_status = "error"
                    item.nbb_error = f"HTTP {status} van de NBB bij de referentielijst"
                    gaps += 1
                except Exception as exc:
                    item.nbb_status = "error"
                    item.nbb_error = f"{type(exc).__name__}: {exc}"[:300]
                    gaps += 1
                else:
                    added += result["added"]
                    repaired += result["repaired"]
                    have = result["ok"] + result["added"] + result["repaired"]
                    un = result.get("unavailable", [])
                    unavailable += len(un)
                    if result["references"] == 0:
                        item.nbb_status = "none"
                        item.nbb_error = ""
                    elif result["failed"]:
                        ref, reason = result["failed"][0]
                        item.nbb_status = "fetched" if have else "error"
                        item.nbb_error = (
                            f"{len(result['failed'])} van {result['references']} "
                            f"referenties niet ophaalbaar — bv. {ref}: {reason}"
                        )[:500]
                        gaps += 1
                    else:
                        item.nbb_status = "fetched" if have else "none"
                        item.nbb_error = ""
                    # géén fout maar een feit over de bron: eerlijk tonen
                    # zonder het als probleem te tellen
                    item.nbb_note = (
                        f"{len(un)} referentie(s) zonder opvraagbaar document "
                        f"bij de NBB — bv. {un[0][0]}: {un[0][1]}"
                    )[:500] if un else ""
                if item.nbb_error:
                    log.warning("NBB-controle %s: %s", item.enterprise_number,
                                item.nbb_error)
                break
            item.nbb_fetched_at = datetime.utcnow()
            db.commit()
            time.sleep(1.0)   # rustig tempo richting de NBB-API
        _set_list_status(
            list_id,
            f"klaar: {total} bedrijven gecontroleerd — {added} document(en) "
            f"toegevoegd, {repaired} hersteld, {unavailable} referentie(s) "
            "zonder document bij de NBB (geen fout), "
            f"{gaps} bedrijf(ven) met resterende problemen",
        )
    except Exception as exc:
        _set_list_status(list_id, f"fout: {type(exc).__name__}: {exc}"[:300])
        raise
    finally:
        db.close()


def _fetch_list_from_nbb(list_id: int) -> None:
    """Achtergrondtaak: haal voor elk bedrijf in de lijst de NBB-documenten
    op. Rustig tempo (throttle) en één herkansing bij een tijdslimiet-fout
    (HTTP 429); de foutreden wordt per bedrijf bewaard en getoond."""
    import logging
    import time

    import httpx

    from ..services.cbso_client import fetch_and_store

    log = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        items = (
            db.query(CompanyListItem)
            .filter(
                CompanyListItem.list_id == list_id,
                CompanyListItem.nbb_status.in_(["pending", "error"]),
            )
            .all()
        )
        for item in items:
            for attempt in (1, 2):
                try:
                    deposits = fetch_and_store(db, item.enterprise_number)
                    item.nbb_status = "fetched" if deposits else "none"
                    item.nbb_error = ""
                    break
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status in (429, 500, 502, 503, 504) and attempt == 1:
                        # tempolimiet of tijdelijke NBB-storing:
                        # even wachten en opnieuw
                        time.sleep(30 if status == 429 else 10)
                        continue
                    body = " ".join(exc.response.text[:200].split())
                    item.nbb_status = "error"
                    item.nbb_error = f"HTTP {status} van de NBB" + \
                        (f" — {body}" if body else "")
                except Exception as exc:
                    item.nbb_status = "error"
                    item.nbb_error = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("NBB-ophaling %s: %s", item.enterprise_number,
                            item.nbb_error)
                break
            item.nbb_fetched_at = datetime.utcnow()
            db.commit()
            time.sleep(1.0)   # rustig tempo richting de NBB-API
    finally:
        db.close()


@router.post("/lists/{list_id}/fetch-nbb")
async def fetch_nbb(
    list_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_list(db, list_id, user)
    background_tasks.add_task(_fetch_list_from_nbb, list_id)
    log_action(db, user.id, "fetch_nbb_list", str(list_id))
    form = await request.form()
    next_url = str(form.get("next", ""))
    if not next_url.startswith("/"):
        next_url = f"/lists/{list_id}"
    return RedirectResponse(next_url, status_code=303)


@router.post("/lists/{list_id}/check-nbb")
def check_nbb(
    list_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_list(db, list_id, user)
    background_tasks.add_task(_verify_list_nbb, list_id)
    log_action(db, user.id, "check_nbb_list", str(list_id))
    return RedirectResponse(f"/lists/{list_id}", status_code=303)


@router.post("/lists/{list_id}/delete")
def delete_list(
    list_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    company_list = _get_list(db, list_id, user)
    db.delete(company_list)
    db.commit()
    log_action(db, user.id, "delete_list", str(list_id))
    return RedirectResponse("/lists", status_code=303)
