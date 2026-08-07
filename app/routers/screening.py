"""Screening-sectie: de volledige pipeline (fases 1-6) bedienbaar vanuit
de browser — thesis-editor, uploads, build-knop, longlist en one-pagers.

Bekijken mag elke ingelogde gebruiker; alles wat gedeelde data wijzigt
(uploads, thesis, pipeline draaien) is voor beheerders.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..audit import log_action
from ..database import get_db
from ..models import User
from ..security import get_current_user, require_admin
from ..services import screening
from ..templating import templates

router = APIRouter(prefix="/screening")

PER_PAGE = 100


def _overview_context(request: Request, user: User, error: str = "", notice: str = ""):
    thesis = None
    thesis_error = ""
    try:
        thesis = screening.load_thesis()
    except Exception as exc:
        thesis_error = str(exc)
    return {
        "request": request,
        "user": user,
        "status": screening.data_status(),
        "run_status": screening.get_status(),
        "running": screening.is_running(),
        "thesis": thesis,
        "thesis_error": thesis_error,
        "error": error,
        "notice": notice,
    }


@router.get("")
def overview(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse(
        request, "screening.html", _overview_context(request, user)
    )


@router.post("/run")
def run_pipeline(
    background_tasks: BackgroundTasks,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if screening.is_running():
        return RedirectResponse("/screening", status_code=303)
    screening.set_status("bezig: gestart ...")
    background_tasks.add_task(screening.run_pipeline_bg)
    log_action(db, user.id, "screening_run_started", "")
    return RedirectResponse("/screening", status_code=303)


def _upload_response(request, user, db, action: str, fn, detail_fmt: str):
    """Gemeenschappelijk stramien: uitvoeren, audit-loggen, fout netjes tonen."""
    try:
        result = fn()
    except screening.ScreeningError as exc:
        return templates.TemplateResponse(
            request, "screening.html",
            _overview_context(request, user, error=str(exc)),
        )
    log_action(db, user.id, action, detail_fmt.format(result=result))
    return templates.TemplateResponse(
        request, "screening.html",
        _overview_context(request, user, notice=detail_fmt.format(result=result)),
    )


@router.post("/upload/taxonomy")
def upload_taxonomy(
    request: Request,
    files: list[UploadFile],
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    def do():
        n_files, n_codes = screening.save_taxonomy_files(files)
        return f"{n_codes} rubriekcodes uit {n_files} bestand(en)"

    return _upload_response(request, user, db, "screening_taxonomy_upload", do,
                            "Taxonomie bijgewerkt: {result}")


@router.post("/upload/bridge")
def upload_bridge(
    request: Request,
    file: UploadFile,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    def do():
        return screening.save_bridge_file(file).name

    return _upload_response(request, user, db, "screening_bridge_upload", do,
                            "NACE-conversietabel opgeslagen als {result}")


@router.post("/upload/kbo")
def upload_kbo(
    request: Request,
    file: UploadFile,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    def do():
        return screening.save_kbo_zip(file).name

    return _upload_response(request, user, db, "screening_kbo_upload", do,
                            "KBO-zip opgeslagen als {result}")


@router.post("/kbo-adopt")
def kbo_adopt(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    def do():
        return screening.adopt_downloaded_zip().name

    return _upload_response(request, user, db, "screening_kbo_adopt", do,
                            "Bestaande download gekoppeld: {result}")


@router.post("/upload/manual-signals")
def upload_manual_signals(
    request: Request,
    file: UploadFile,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    def do():
        return screening.save_manual_signals(file)

    return _upload_response(request, user, db, "screening_signals_upload", do,
                            "{result} handmatige signalen geregistreerd")


# ── thesis-editor ────────────────────────────────────────────────────────────

@router.get("/thesis")
def thesis_form(request: Request, user: User = Depends(require_admin)):
    import yaml

    raw = yaml.safe_load(screening.ensure_thesis().read_text()) or {}
    return templates.TemplateResponse(
        request, "screening_thesis.html",
        {"user": user, "raw": raw,
         "provinces": sorted(screening.pipeline_config.VALID_PROVINCES),
         "error": ""},
    )


@router.post("/thesis")
async def thesis_save(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()

    def _num(name: str) -> float | None:
        value = str(form.get(name, "")).strip().replace(",", ".")
        return float(value) if value else None

    nace_codes = [line.split("#")[0].strip()
                  for line in str(form.get("nace_codes", "")).splitlines()]
    raw = {
        "name": str(form.get("name", "")).strip(),
        "sector": str(form.get("sector", "")).strip(),
        "nace_version": int(form.get("nace_version", 2025)),
        "nace_codes": [c for c in nace_codes if c],
        "geography": {"provinces": form.getlist("provinces")},
        "size": {
            "balance_total_min": _num("balance_total_min"),
            "balance_total_max": _num("balance_total_max"),
            "fte_min": _num("fte_min"),
            "fte_max": _num("fte_max"),
        },
        "targets": {
            "platform": {"fte_min": _num("platform_fte_min")},
            "bolt_on": {"fte_max": _num("bolt_on_fte_max")},
        },
        "weights": {
            "financial": _num("weight_financial") or 0.0,
            "growth": _num("weight_growth") or 0.0,
            "signals": _num("weight_signals") or 0.0,
        },
    }
    try:
        thesis = screening.save_thesis(raw)
    except (screening.pipeline_config.ThesisError, ValueError) as exc:
        return templates.TemplateResponse(
            request, "screening_thesis.html",
            {"user": user, "raw": raw,
             "provinces": sorted(screening.pipeline_config.VALID_PROVINCES),
             "error": str(exc)},
        )
    log_action(db, user.id, "screening_thesis_saved", thesis.name)
    return RedirectResponse("/screening", status_code=303)


# ── longlist en one-pager ────────────────────────────────────────────────────

def _fmt(value, decimals: int = 0) -> str:
    if value is None:
        return "—"
    text = f"{value:,.{decimals}f}"
    return text.replace(",", ".")  # 1.234.567 zoals in de rest van de app


@router.get("/longlist")
def longlist(
    request: Request,
    q: str = "",
    klasse: str = "",
    min_score: str = "",
    sort: str = "score",
    page: int = 1,
    user: User = Depends(get_current_user),
):
    try:
        min_score_val = float(min_score.replace(",", ".")) if min_score.strip() else None
    except ValueError:
        min_score_val = None
    df = screening.load_longlist(q=q, target_class=klasse,
                                 min_score=min_score_val, sort=sort)
    rows, total, pages = [], 0, 1
    if df is not None:
        total = len(df)
        pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
        page = min(max(page, 1), pages)
        offset = (page - 1) * PER_PAGE
        for i, row in enumerate(df.slice(offset, PER_PAGE).iter_rows(named=True),
                                start=offset + 1):
            revenue = _fmt(row["revenue"])
            if row.get("revenue_source") == "estimate" and row["revenue"] is not None:
                revenue += " *"
            rows.append({
                "rank": i,
                "number": row["enterprise_number"],
                "name": row["name"] or "—",
                "score": _fmt(row["score_total"], 1),
                "klasse": row["target_class"] or "—",
                "year": row["fiscal_year"] or "—",
                "revenue": revenue,
                "ebitda": _fmt(row["ebitda_proxy"]),
                "fte": _fmt(row["fte"], 1) if row["fte"] is not None else "—",
                "province": row["province"] or "—",
                "nace": row["nace"] or "—",
                "signals": (row["signals_derived_count"] or 0)
                           + (row["signals_manual_count"] or 0),
                "in_size": row["in_size_range"],
                "ambiguous": row["nace_conversion_ambiguous"],
            })
    return templates.TemplateResponse(
        request, "screening_longlist.html",
        {"user": user, "rows": rows, "built": df is not None, "total": total,
         "page": page, "pages": pages, "q": q, "klasse": klasse,
         "min_score": min_score, "sort": sort},
    )


@router.get("/longlist.csv")
def longlist_csv(user: User = Depends(get_current_user)):
    df = screening.load_longlist()
    if df is None:
        return PlainTextResponse("longlist nog niet gebouwd", status_code=404)
    import io

    buf = io.BytesIO()
    df.write_csv(buf)
    return PlainTextResponse(
        buf.getvalue().decode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=longlist.csv"},
    )


@router.get("/company/{enterprise_number}")
def company_onepager(
    request: Request,
    enterprise_number: str,
    user: User = Depends(get_current_user),
):
    try:
        report = screening.onepager_markdown(enterprise_number)
        error = ""
    except screening.ScreeningError as exc:
        report, error = "", str(exc)
    return templates.TemplateResponse(
        request, "screening_onepager.html",
        {"user": user, "enterprise_number": enterprise_number,
         "report": report, "error": error},
    )
