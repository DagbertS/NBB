"""Prompt-analyses: draai gebruikersprompts over de dagelijkse neerleggingen
met Claude en mail de conclusie naar de opgegeven ontvangers."""

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import anthropic
from sqlalchemy.orm import Session

from ..config import ANTHROPIC_API_KEY
from ..models import NbbDeposit, Prompt, PromptRun
from .mailer import send_mail

log = logging.getLogger(__name__)

MODEL = "claude-opus-5"
MAX_DEPOSITS_PER_RUN = 50
MAX_CHARS_PER_DEPOSIT = 20000

SYSTEM_PROMPT = (
    "Je bent een M&A-analist die dagelijkse neerleggingen bij de Belgische "
    "Nationale Bank (Balanscentrale) analyseert voor een acquisitie-screening. "
    "Je krijgt jaarrekeninggegevens (JSON/XBRL) van recent gepubliceerde "
    "neerleggingen en een analysevraag van de gebruiker. Antwoord in het "
    "Nederlands, beknopt en zakelijk. Vermeld altijd het ondernemingsnummer "
    "bij elke bevinding. Als de data de vraag niet kan beantwoorden, zeg dat."
)


def _deposits_for_day(db: Session, day: date) -> list[NbbDeposit]:
    return (
        db.query(NbbDeposit)
        .filter(NbbDeposit.deposit_date == day.isoformat())
        .limit(MAX_DEPOSITS_PER_RUN)
        .all()
    )


def _build_context(deposits: list[NbbDeposit]) -> str:
    parts = []
    for dep in deposits:
        path = Path(dep.file_path)
        if not path.exists() or path.suffix.lower() != ".json":
            continue
        try:
            content = path.read_text()[:MAX_CHARS_PER_DEPOSIT]
        except OSError:
            continue
        parts.append(
            f"### Neerlegging {dep.reference} "
            f"(onderneming {dep.enterprise_number}, {dep.deposit_date})\n{content}"
        )
    return "\n\n".join(parts)


def run_prompt(db: Session, prompt: Prompt, day: date | None = None) -> PromptRun:
    """Voer één prompt uit over de neerleggingen van één dag."""
    day = day or (date.today() - timedelta(days=1))
    run = PromptRun(prompt_id=prompt.id, run_at=datetime.utcnow())

    if not ANTHROPIC_API_KEY:
        run.status = "error"
        run.result_text = "ANTHROPIC_API_KEY ontbreekt in .env"
        db.add(run)
        db.commit()
        return run

    deposits = _deposits_for_day(db, day)
    if not deposits:
        run.status = "ok"
        run.result_text = f"Geen neerleggingen gevonden voor {day.isoformat()}."
        db.add(run)
        db.commit()
        return run

    context = _build_context(deposits)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Neerleggingen van {day.isoformat()}:\n\n{context}\n\n"
                        f"---\nAnalysevraag: {prompt.prompt_text}"
                    ),
                }
            ],
        ) as stream:
            message = stream.get_final_message()
        if message.stop_reason == "refusal":
            run.status = "error"
            run.result_text = "De analyse werd geweigerd door het model."
        else:
            run.result_text = "".join(
                block.text for block in message.content if block.type == "text"
            )
    except Exception as exc:
        log.exception("Promptanalyse mislukt: %s", prompt.name)
        run.status = "error"
        run.result_text = f"Fout bij uitvoeren: {exc}"

    if run.status == "ok" and prompt.recipients:
        recipients = [r.strip() for r in prompt.recipients.split(",") if r.strip()]
        run.emailed = send_mail(
            recipients,
            subject=f"[NBB-screening] {prompt.name} — {day.isoformat()}",
            body=run.result_text,
        )

    db.add(run)
    db.commit()
    return run


def run_all_enabled_prompts(db: Session, day: date | None = None) -> int:
    prompts = db.query(Prompt).filter(Prompt.enabled.is_(True)).all()
    for p in prompts:
        run_prompt(db, p, day)
    return len(prompts)
