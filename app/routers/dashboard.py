import sqlite3

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..config import DATABASE_PATH
from ..database import get_db
from ..models import CompanyList, NbbDeposit, Prompt, Setting, User
from ..security import get_current_user
from ..services.kbo_search import kbo_loaded
from ..templating import templates

router = APIRouter()


@router.get("/")
def dashboard(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    company_count = 0
    if kbo_loaded():
        with sqlite3.connect(DATABASE_PATH) as conn:
            (company_count,) = conn.execute("SELECT COUNT(*) FROM kbo_company").fetchone()

    list_query = db.query(CompanyList)
    prompt_query = db.query(Prompt)
    if user.role != "admin":
        list_query = list_query.filter(CompanyList.owner_id == user.id)
        prompt_query = prompt_query.filter(Prompt.owner_id == user.id)

    last_import = db.get(Setting, "kbo_last_full_import")
    last_update = db.get(Setting, "kbo_last_update")
    last_extract = db.get(Setting, "daily_extract_last_date")

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "kbo_loaded": kbo_loaded(),
            "company_count": company_count,
            "list_count": list_query.count(),
            "prompt_count": prompt_query.count(),
            "deposit_count": db.query(NbbDeposit).count(),
            "last_import": last_import.value if last_import else None,
            "last_update": last_update.value if last_update else None,
            "last_extract": last_extract.value if last_extract else None,
        },
    )
