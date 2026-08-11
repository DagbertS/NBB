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


def _overview_context(request: Request, user: User, db: Session,
                      error: str = "", notice: str = ""):
    from ..models import CompanyList

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
        "pipelines": screening.list_pipelines(db),
        "company_lists": db.query(CompanyList).order_by(CompanyList.name).all(),
        "theses": screening.list_theses(),
        "error": error,
        "notice": notice,
    }


@router.get("")
def overview(request: Request, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "screening.html", _overview_context(request, user, db)
    )


@router.post("/run")
def run_pipeline(
    background_tasks: BackgroundTasks,
    list_id: str = Form(""),
    thesis_key: str = Form(screening.DEFAULT_THESIS_KEY),
    analyses: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if screening.is_running():
        return RedirectResponse("/screening", status_code=303)
    target = int(list_id) if list_id.strip().isdigit() else None
    thesis_key = thesis_key.strip() or screening.DEFAULT_THESIS_KEY
    make_analyses = analyses == "on" and target is not None
    screening.set_status("bezig: gestart ...")
    background_tasks.add_task(screening.run_pipeline_bg, target, thesis_key,
                              make_analyses, user.id)
    log_action(db, user.id, "screening_run_started",
               f"bron={'list-' + str(target) if target else 'thesis'} "
               f"criteria={thesis_key} analyses={make_analyses}")
    return RedirectResponse("/screening", status_code=303)


def _data_context(request: Request, user: User, db: Session,
                  error: str = "", notice: str = ""):
    from ..models import Setting
    from ..services.kbo_search import kbo_loaded

    from .. import config as app_config

    return {
        "request": request,
        "user": user,
        "status": screening.data_status(),
        "kbo_loaded": kbo_loaded(),
        "kbo_import_status": (db.get(Setting, "kbo_import_status") or Setting(value="")).value,
        "has_cbso_key": bool(app_config.NBB_CBSO_SUBSCRIPTION_KEY),
        "has_extract_key": bool(app_config.NBB_CBSO_EXTRACT_KEY),
        "has_kbo_login": bool(app_config.KBO_USERNAME and app_config.KBO_PASSWORD),
        "error": error,
        "notice": notice,
    }


@router.get("/data")
def data_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request, "screening_data.html", _data_context(request, user, db)
    )


def _upload_response(request, user, db, action: str, fn, detail_fmt: str):
    """Gemeenschappelijk stramien: uitvoeren, audit-loggen, fout netjes tonen."""
    try:
        result = fn()
    except screening.ScreeningError as exc:
        return templates.TemplateResponse(
            request, "screening_data.html",
            _data_context(request, user, db, error=str(exc)),
        )
    log_action(db, user.id, action, detail_fmt.format(result=result))
    return templates.TemplateResponse(
        request, "screening_data.html",
        _data_context(request, user, db, notice=detail_fmt.format(result=result)),
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
    background_tasks: BackgroundTasks,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Eén upload voedt de hele applicatie: de zip gaat naar de
    pipeline-datamap én wordt op de achtergrond in de zoekdatabase geladen."""

    def do():
        target = screening.save_kbo_zip(file)

        def import_to_search_db():
            from .admin import _set_import_status
            from ..services.kbo_import import import_full_zip

            try:
                _set_import_status(f"bezig: importeren van {target.name} in de zoekdatabase ...")
                import_full_zip(target, progress=lambda m: _set_import_status(
                    f"bezig: {str(m).strip()}"))
                _set_import_status(f"klaar: {target.name} geïmporteerd")
            except Exception as exc:
                _set_import_status(f"fout: {exc}")

        background_tasks.add_task(import_to_search_db)
        return target.name

    return _upload_response(
        request, user, db, "screening_kbo_upload", do,
        "KBO-zip opgeslagen als {result} — de zoekdatabase wordt op de "
        "achtergrond bijgewerkt (status hieronder)")


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


# ── criteria-editor (benoemde thesis-sets) ───────────────────────────────────

@router.get("/thesis")
def thesis_form(request: Request, thesis: str = screening.DEFAULT_THESIS_KEY,
                user: User = Depends(require_admin)):
    import yaml

    try:
        raw = yaml.safe_load(screening.thesis_path_for(thesis).read_text()) or {}
    except screening.ScreeningError:
        return RedirectResponse("/screening/thesis", status_code=303)
    return templates.TemplateResponse(
        request, "screening_thesis.html",
        {"user": user, "raw": raw, "thesis_key": thesis,
         "thesis_options": screening.list_theses(),
         "provinces": sorted(screening.pipeline_config.VALID_PROVINCES),
         "error": ""},
    )


@router.post("/thesis/new")
def thesis_new(
    name: str = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        key = screening.create_thesis(name)
    except screening.ScreeningError:
        return RedirectResponse("/screening/thesis", status_code=303)
    log_action(db, user.id, "screening_thesis_created", key)
    return RedirectResponse(f"/screening/thesis?thesis={key}", status_code=303)


@router.post("/thesis")
async def thesis_save(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    thesis_key = str(form.get("thesis_key", screening.DEFAULT_THESIS_KEY)) \
        or screening.DEFAULT_THESIS_KEY

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
    save_as = str(form.get("save_as", "")).strip()
    try:
        if save_as:
            thesis_key = screening.save_thesis_as(raw, save_as)
        else:
            screening.save_thesis(raw, key=thesis_key)
    except (screening.ScreeningError, screening.pipeline_config.ThesisError,
            ValueError) as exc:
        return templates.TemplateResponse(
            request, "screening_thesis.html",
            {"user": user, "raw": raw, "thesis_key": thesis_key,
             "thesis_options": screening.list_theses(),
             "provinces": sorted(screening.pipeline_config.VALID_PROVINCES),
             "error": str(exc)},
        )
    log_action(db, user.id, "screening_thesis_saved", thesis_key)
    return RedirectResponse(f"/screening/thesis?thesis={thesis_key}", status_code=303)


@router.post("/thesis/delete")
def thesis_delete(
    thesis_key: str = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        screening.delete_thesis(thesis_key)
        log_action(db, user.id, "screening_thesis_deleted", thesis_key)
    except screening.ScreeningError:
        pass
    return RedirectResponse("/screening/thesis", status_code=303)


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
    pipeline: str = "thesis",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        min_score_val = float(min_score.replace(",", ".")) if min_score.strip() else None
    except ValueError:
        min_score_val = None
    df = screening.load_longlist(q=q, target_class=klasse,
                                 min_score=min_score_val, sort=sort,
                                 pipeline=pipeline)
    rows, total, pages = [], 0, 1
    nbb_counts: dict[str, int] = {}
    if df is not None:
        total = len(df)
        pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
        page = min(max(page, 1), pages)
        offset = (page - 1) * PER_PAGE
        page_df = df.slice(offset, PER_PAGE)

        # overzicht van bij de NBB opgehaalde documenten, per bedrijf op deze pagina
        from sqlalchemy import func

        from ..models import NbbDeposit

        numbers = page_df["enterprise_number"].to_list()
        if numbers:
            for number, count in (
                db.query(NbbDeposit.enterprise_number, func.count(NbbDeposit.id))
                .filter(NbbDeposit.enterprise_number.in_(numbers))
                .group_by(NbbDeposit.enterprise_number)
            ):
                nbb_counts[number] = count

        for i, row in enumerate(page_df.iter_rows(named=True),
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
                "nbb_count": nbb_counts.get(row["enterprise_number"], 0),
            })
    list_id = None
    if pipeline.startswith("list-") and pipeline[5:].isdigit():
        list_id = int(pipeline[5:])
    return templates.TemplateResponse(
        request, "screening_longlist.html",
        {"user": user, "rows": rows, "built": df is not None, "total": total,
         "page": page, "pages": pages, "q": q, "klasse": klasse,
         "min_score": min_score, "sort": sort, "pipeline": pipeline,
         "pipelines": screening.list_pipelines(db), "list_id": list_id},
    )


@router.get("/longlist.csv")
def longlist_csv(pipeline: str = "thesis", user: User = Depends(get_current_user)):
    df = screening.load_longlist(pipeline=pipeline)
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
    pipeline: str = "thesis",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    analysis_id = None
    try:
        report = screening.onepager_markdown(enterprise_number, pipeline=pipeline)
        error = ""
        # elk gegenereerd resultaat wordt bewaard (historiek in /analyses)
        name = report.splitlines()[0].lstrip("# ").split("—")[0].strip() if report else ""
        analysis = screening.save_analysis(
            db, enterprise_number, name, "pipeline", report, user.id
        )
        analysis_id = analysis.id
    except screening.ScreeningError as exc:
        report, error = "", str(exc)
    return templates.TemplateResponse(
        request, "screening_onepager.html",
        {"user": user, "enterprise_number": enterprise_number,
         "report_html": screening.render_report_html(report) if report else "",
         "error": error, "analysis_id": analysis_id},
    )


@router.post("/individual/{enterprise_number}")
def individual_screening(
    enterprise_number: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Individuele screening van één bedrijf: NBB-data ophalen (indien key),
    cijfers herberekenen en het rapport bewaren bij de analyses."""
    num = screening.normalize_number(enterprise_number)
    background_tasks.add_task(screening.run_individual_screening_bg, num, user.id)
    log_action(db, user.id, "individual_screening_started", num)
    return RedirectResponse("/analyses?started=1", status_code=303)
