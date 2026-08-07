import sqlite3

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..audit import log_action
from ..config import DATABASE_PATH
from ..database import get_db
from ..models import AuditLog, User
from ..security import hash_password, require_admin
from ..templating import templates

router = APIRouter(prefix="/admin")


def _set_import_status(text: str) -> None:
    with sqlite3.connect(DATABASE_PATH, timeout=60) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT DEFAULT '')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('kbo_import_status', ?)",
            (text,),
        )
        conn.commit()


def _kbo_import_bg() -> None:
    """Achtergrondtaak: nieuwste Full-zip downloaden van het KBO-portaal en importeren."""
    from ..services.kbo_import import import_full_zip
    from ..services.kbo_update import _extract_number, download_file, list_available_files

    try:
        _set_import_status("bezig: bestandenlijst opvragen bij KBO-portaal ...")
        fulls = sorted(
            {f for f in list_available_files() if f.endswith("_Full.zip")},
            key=_extract_number,
        )
        if not fulls:
            _set_import_status(
                "fout: geen Full-zip gevonden — controleer KBO_USERNAME/KBO_PASSWORD"
            )
            return
        _set_import_status(f"bezig: downloaden van {fulls[-1]} (enkele GB) ...")
        path = download_file(fulls[-1])
        _set_import_status(f"bezig: importeren van {path.name} ...")
        import_full_zip(path, progress=lambda msg: _set_import_status(f"bezig: {str(msg).strip()}"))
        _set_import_status(f"klaar: {path.name} geïmporteerd")
    except Exception as exc:
        _set_import_status(f"fout: {exc}")


@router.post("/kbo-import")
def start_kbo_import(
    background_tasks: BackgroundTasks,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    background_tasks.add_task(_kbo_import_bg)
    log_action(db, user.id, "kbo_import_started", "")
    return RedirectResponse("/", status_code=303)


@router.get("/users")
def users_page(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.created_at).all()
    return templates.TemplateResponse(request, "admin_users.html", {"user": user, "users": users})


@router.post("/users")
def create_user(
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "E-mailadres bestaat al")
    if role not in ("user", "admin"):
        role = "user"
    db.add(User(email=email, password_hash=hash_password(password), role=role))
    db.commit()
    log_action(db, user.id, "create_user", f"{email} ({role})")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/toggle")
def toggle_user(
    user_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404)
    if target.id == user.id:
        raise HTTPException(400, "Je kan jezelf niet deactiveren")
    target.is_active = not target.is_active
    db.commit()
    log_action(db, user.id, "toggle_user", target.email)
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/password")
def reset_password(
    user_id: int,
    password: str = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404)
    target.password_hash = hash_password(password)
    db.commit()
    log_action(db, user.id, "reset_password", target.email)
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/audit")
def audit_page(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    page = max(int(request.query_params.get("page", "1")), 1)
    per_page = 100
    entries = (
        db.query(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
        .all()
    )
    total = db.query(AuditLog).count()
    return templates.TemplateResponse(
        request,
        "admin_audit.html",
        {"user": user, "entries": entries, "page": page, "per_page": per_page, "total": total},
    )
