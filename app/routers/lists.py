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
    return templates.TemplateResponse(
        request, "list_detail.html",
        {"user": user, "l": company_list, "nbb_counts": nbb_counts}
    )


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
                    if status == 429 and attempt == 1:
                        time.sleep(30)   # NBB-tempolimiet: even wachten en opnieuw
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
