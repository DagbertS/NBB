"""Bewaarde analyses: elk screeningresultaat (pipeline-one-pager of
individuele screening) blijft bewaard en is als PDF te downloaden."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..audit import log_action
from ..database import get_db
from ..models import Analysis, User
from ..security import get_current_user, require_admin
from ..services import screening
from ..templating import templates

router = APIRouter(prefix="/analyses")


@router.get("")
def analyses_page(
    request: Request,
    q: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from ..services import company_filters

    query = db.query(Analysis)
    if q.strip():
        needle = f"%{q.strip()}%"
        number_needle = f"%{screening.normalize_number(q)}%"
        query = query.filter(
            Analysis.company_name.ilike(needle)
            | Analysis.enterprise_number.like(number_needle)
        )
    entries = query.order_by(Analysis.created_at.desc()).limit(200).all()

    # filters op bedrijfskenmerken (VTE / EBITDA-proxy / provincie)
    filters = company_filters.parse_filters(request.query_params)
    facts = company_filters.lookup(
        [a.enterprise_number for a in entries])
    if filters["active"]:
        entries = [
            a for a in entries
            if company_filters.matches(
                facts.get(screening.normalize_number(a.enterprise_number)),
                filters)
        ]
    return templates.TemplateResponse(
        request, "analyses.html",
        {"user": user, "entries": entries, "q": q,
         "filters": filters, "facts": facts,
         "operators": company_filters.OPERATORS,
         "provinces": company_filters.PROVINCES,
         "norm": screening.normalize_number,
         "fmt_fte": company_filters.fmt_fte,
         "fmt_eur": company_filters.fmt_eur,
         "started": request.query_params.get("started") == "1",
         "individual_status": screening.get_individual_status()},
    )


def _get_analysis(db: Session, analysis_id: int) -> Analysis:
    analysis = db.get(Analysis, analysis_id)
    if not analysis:
        raise HTTPException(404, "Analyse niet gevonden")
    return analysis


@router.get("/{analysis_id}")
def analysis_detail(
    analysis_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    analysis = _get_analysis(db, analysis_id)
    return templates.TemplateResponse(
        request, "analysis_detail.html",
        {"user": user, "a": analysis,
         "report_html": screening.render_report_html(analysis.report_md)},
    )


@router.get("/{analysis_id}/pdf")
def analysis_pdf(
    analysis_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    analysis = _get_analysis(db, analysis_id)
    from ..services.pdf_export import markdown_to_pdf

    stamp = analysis.created_at.strftime("%Y-%m-%d %H:%M")
    pdf = markdown_to_pdf(
        analysis.report_md,
        footer_text=f"NBB M&A Screening - {analysis.kind} - {stamp} UTC - "
                    "geschatte waarden zijn nooit feiten",
    )
    log_action(db, user.id, "analysis_pdf", f"{analysis.enterprise_number} (#{analysis.id})")
    filename = f"screening_{analysis.enterprise_number}_{analysis.created_at:%Y%m%d}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{analysis_id}/delete")
def analysis_delete(
    analysis_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    analysis = _get_analysis(db, analysis_id)
    db.delete(analysis)
    db.commit()
    log_action(db, user.id, "analysis_deleted", f"#{analysis_id}")
    return RedirectResponse("/analyses", status_code=303)
