from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..audit import log_action
from ..database import SessionLocal, get_db
from ..models import Prompt, PromptRun, User
from ..security import get_current_user
from ..templating import templates

router = APIRouter()


def _get_prompt(db: Session, prompt_id: int, user: User) -> Prompt:
    prompt = db.get(Prompt, prompt_id)
    if not prompt:
        raise HTTPException(404)
    if user.role != "admin" and prompt.owner_id != user.id:
        raise HTTPException(403)
    return prompt


@router.get("/prompts")
def prompts_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Prompt)
    if user.role != "admin":
        query = query.filter(Prompt.owner_id == user.id)
    prompts = query.order_by(Prompt.created_at.desc()).all()
    return templates.TemplateResponse(
        request, "prompts.html", {"user": user, "prompts": prompts}
    )


@router.post("/prompts")
def create_prompt(
    name: str = Form(...),
    prompt_text: str = Form(...),
    recipients: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    prompt = Prompt(
        name=name.strip(),
        prompt_text=prompt_text.strip(),
        recipients=recipients.strip(),
        owner_id=user.id,
    )
    db.add(prompt)
    db.commit()
    log_action(db, user.id, "create_prompt", name)
    return RedirectResponse("/prompts", status_code=303)


@router.post("/prompts/{prompt_id}/toggle")
def toggle_prompt(
    prompt_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    prompt = _get_prompt(db, prompt_id, user)
    prompt.enabled = not prompt.enabled
    db.commit()
    return RedirectResponse("/prompts", status_code=303)


@router.post("/prompts/{prompt_id}/delete")
def delete_prompt(
    prompt_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    prompt = _get_prompt(db, prompt_id, user)
    db.delete(prompt)
    db.commit()
    log_action(db, user.id, "delete_prompt", str(prompt_id))
    return RedirectResponse("/prompts", status_code=303)


def _run_prompt_bg(prompt_id: int) -> None:
    from ..services.prompt_runner import run_prompt

    db = SessionLocal()
    try:
        prompt = db.get(Prompt, prompt_id)
        if prompt:
            run_prompt(db, prompt)
    finally:
        db.close()


@router.post("/prompts/{prompt_id}/run")
def run_now(
    prompt_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_prompt(db, prompt_id, user)
    background_tasks.add_task(_run_prompt_bg, prompt_id)
    log_action(db, user.id, "run_prompt", str(prompt_id))
    return RedirectResponse(f"/prompts/{prompt_id}/runs", status_code=303)


@router.get("/prompts/{prompt_id}/runs")
def prompt_runs(
    prompt_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    prompt = _get_prompt(db, prompt_id, user)
    runs = (
        db.query(PromptRun)
        .filter(PromptRun.prompt_id == prompt_id)
        .order_by(PromptRun.run_at.desc())
        .limit(50)
        .all()
    )
    return templates.TemplateResponse(
        request, "prompt_runs.html", {"user": user, "prompt": prompt, "runs": runs}
    )
