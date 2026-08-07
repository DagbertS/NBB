import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..audit import log_action
from ..database import get_db
from ..geo import PROVINCES
from ..models import CompanyList, CompanyListItem, User
from ..security import get_current_user
from ..services.kbo_search import (
    company_detail,
    kbo_loaded,
    nace_description,
    search_companies,
)
from ..templating import templates

router = APIRouter()

MAX_LIST_SIZE = 5000


def _criteria_from_query(request: Request) -> dict:
    q = request.query_params
    return {
        "nace": q.get("nace", "").strip(),
        "name": q.get("name", "").strip(),
        "exact_name": q.get("exact_name") == "on",
        "enterprise_number": q.get("enterprise_number", "").strip(),
        "zipcode": q.get("zipcode", "").strip(),
        "municipality": q.get("municipality", "").strip(),
        "province": q.get("province", "").strip(),
        "active_only": q.get("active_only", "on") == "on",
    }


@router.get("/companies")
def companies_search(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    criteria = _criteria_from_query(request)
    page = max(int(request.query_params.get("page", "1")), 1)
    per_page = 50
    rows: list = []
    total = 0
    searched = any(v for k, v in criteria.items() if k not in ("active_only", "exact_name"))
    if searched:
        rows, total = search_companies(
            **criteria, limit=per_page, offset=(page - 1) * per_page
        )
        log_action(db, user.id, "search_companies", json.dumps(criteria, ensure_ascii=False))
    return templates.TemplateResponse(
        request,
        "companies.html",
        {
            "user": user,
            "criteria": criteria,
            "rows": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "searched": searched,
            "provinces": PROVINCES,
            "kbo_loaded": kbo_loaded(),
            "nace_desc": nace_description(criteria["nace"]) if criteria["nace"] else "",
        },
    )


@router.post("/companies/save-list")
def save_list(
    request: Request,
    list_name: str = Form(...),
    criteria_json: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sla het volledige zoekresultaat op als longlist."""
    criteria = json.loads(criteria_json)
    rows, total = search_companies(**criteria, limit=MAX_LIST_SIZE)
    company_list = CompanyList(
        name=list_name.strip(),
        description=f"{total} resultaten bij aanmaak",
        criteria=criteria_json,
        owner_id=user.id,
    )
    db.add(company_list)
    db.flush()
    for row in rows:
        db.add(
            CompanyListItem(
                list_id=company_list.id,
                enterprise_number=row["enterprise_number"],
                name=row.get("name") or "",
                zipcode=row.get("zipcode") or "",
                municipality=row.get("municipality") or "",
                nace_code=row.get("main_nace") or "",
            )
        )
    db.commit()
    log_action(db, user.id, "save_list", f"{list_name} ({len(rows)} bedrijven)")
    return RedirectResponse(f"/lists/{company_list.id}", status_code=303)


@router.get("/companies/{enterprise_number}")
def company_page(
    enterprise_number: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    detail = company_detail(enterprise_number)
    log_action(db, user.id, "view_company", enterprise_number)
    from ..models import NbbDeposit

    num = enterprise_number.replace(".", "")
    deposits = (
        db.query(NbbDeposit)
        .filter(NbbDeposit.enterprise_number.in_([num, enterprise_number]))
        .order_by(NbbDeposit.deposit_date.desc())
        .all()
    )
    nace = detail["company"].get("main_nace") if detail else ""
    return templates.TemplateResponse(
        request,
        "company_detail.html",
        {
            "user": user,
            "enterprise_number": enterprise_number,
            "detail": detail,
            "deposits": deposits,
            "nace_desc": nace_description(nace) if nace else "",
        },
    )
