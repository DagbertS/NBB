from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..audit import log_action
from ..database import get_db
from ..models import NbbDeposit, User
from ..security import get_current_user
from ..templating import templates

router = APIRouter()


@router.post("/nbb/fetch/{enterprise_number}")
def fetch_company_nbb(
    enterprise_number: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Haal alle NBB-neerleggingen van één onderneming op."""
    from ..services.cbso_client import CbsoError, fetch_and_store

    try:
        fetch_and_store(db, enterprise_number)
        log_action(db, user.id, "fetch_nbb", enterprise_number)
    except CbsoError as exc:
        raise HTTPException(400, str(exc))
    return RedirectResponse(f"/companies/{enterprise_number}", status_code=303)


@router.get("/nbb/document/{deposit_id}")
def download_document(
    deposit_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    deposit = db.get(NbbDeposit, deposit_id)
    if not deposit or not deposit.file_path:
        raise HTTPException(404)
    path = Path(deposit.file_path)
    if not path.exists():
        raise HTTPException(404, "Bestand niet gevonden op schijf")
    log_action(db, user.id, "download_document", deposit.reference)
    return FileResponse(path, filename=path.name)


@router.get("/nbb/document/{deposit_id}/pdf")
def download_document_pdf(
    deposit_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """De officiële PDF-versie van een neerlegging — al lokaal, of anders
    op aanvraag bij de NBB opgehaald en gecachet."""
    deposit = db.get(NbbDeposit, deposit_id)
    if not deposit:
        raise HTTPException(404)
    stored = Path(deposit.file_path) if deposit.file_path else None
    if stored and stored.suffix.lower() == ".pdf" and stored.exists():
        pdf_path = stored
    else:
        from ..services.cbso_client import CbsoError, fetch_pdf

        try:
            pdf_path = fetch_pdf(deposit)
        except CbsoError as exc:
            raise HTTPException(502, str(exc))
        except Exception as exc:
            raise HTTPException(
                502, f"PDF ophalen bij de NBB mislukt: {type(exc).__name__}: {exc}"
            )
    log_action(db, user.id, "download_document_pdf", deposit.reference)
    return FileResponse(pdf_path, filename=f"{deposit.reference}.pdf",
                        media_type="application/pdf")


@router.get("/nbb/report/{enterprise_number}")
def company_report(
    enterprise_number: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Genereer een gedetailleerd analyserapport (Claude) voor een onderneming."""
    from ..services.analyzer import generate_report

    log_action(db, user.id, "generate_report", enterprise_number)
    report = generate_report(db, enterprise_number)
    return templates.TemplateResponse(
        request,
        "report.html",
        {"user": user, "enterprise_number": enterprise_number, "report": report},
    )
