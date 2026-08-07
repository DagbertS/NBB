"""Analysetool: gedetailleerd rapport over een gezocht bedrijf op basis van
alle lokaal beschikbare data (KBO-gegevens + NBB-neerleggingen)."""

import json
import logging
from pathlib import Path

import anthropic
from sqlalchemy.orm import Session

from ..config import ANTHROPIC_API_KEY
from ..models import NbbDeposit
from .kbo_search import company_detail, nace_description

log = logging.getLogger(__name__)

MODEL = "claude-opus-5"
MAX_CHARS_PER_DEPOSIT = 40000

SYSTEM_PROMPT = (
    "Je bent een senior M&A-analist. Maak op basis van de aangeleverde "
    "KBO-gegevens en NBB-jaarrekeningen een gestructureerd bedrijfsrapport "
    "in het Nederlands met de volgende onderdelen:\n"
    "1. Bedrijfsprofiel (activiteit, rechtsvorm, locatie, historiek)\n"
    "2. Financiële analyse (omzet/brutomarge indien beschikbaar, EBITDA, "
    "eigen vermogen, schulden, personeelsbestand, evolutie over de jaren)\n"
    "3. Sterktes en risico's vanuit een overnameperspectief\n"
    "4. Conclusie: aantrekkelijkheid als overnamekandidaat\n"
    "Wees expliciet over welke cijfers uit welke jaarrekening komen en welke "
    "informatie ontbreekt."
)


def build_company_dossier(db: Session, enterprise_number: str) -> str:
    """Verzamel alle lokale data over een onderneming als tekstdossier."""
    num = enterprise_number.replace(".", "").replace(" ", "")
    parts: list[str] = []

    detail = company_detail(enterprise_number) or company_detail(num)
    if detail:
        c = detail["company"]
        nace = c.get("main_nace") or ""
        parts.append(
            "## KBO-gegevens\n"
            + json.dumps(detail, ensure_ascii=False, indent=1)
            + (f"\nHoofdactiviteit NACE {nace}: {nace_description(nace)}" if nace else "")
        )

    deposits = (
        db.query(NbbDeposit)
        .filter(NbbDeposit.enterprise_number.in_([num, enterprise_number]))
        .order_by(NbbDeposit.deposit_date.desc())
        .all()
    )
    for dep in deposits:
        path = Path(dep.file_path)
        if path.exists() and path.suffix.lower() == ".json":
            parts.append(
                f"## Jaarrekening {dep.reference} ({dep.deposit_date}, "
                f"boekjaar t.e.m. {dep.exercise_end})\n"
                + path.read_text()[:MAX_CHARS_PER_DEPOSIT]
            )
        else:
            parts.append(
                f"## Neerlegging {dep.reference} ({dep.deposit_date}) — "
                f"formaat {dep.data_format}, niet als tekst beschikbaar"
            )
    return "\n\n".join(parts)


def generate_report(db: Session, enterprise_number: str) -> str:
    if not ANTHROPIC_API_KEY:
        return "ANTHROPIC_API_KEY ontbreekt in .env — rapport kan niet gegenereerd worden."
    dossier = build_company_dossier(db, enterprise_number)
    if not dossier:
        return (
            "Geen lokale gegevens gevonden voor deze onderneming. Importeer eerst "
            "de KBO-data en haal de NBB-documenten op."
        )
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    with client.messages.stream(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Maak een bedrijfsrapport voor onderneming {enterprise_number}.\n\n{dossier}",
            }
        ],
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        return "Het rapport werd geweigerd door het model."
    return "".join(block.text for block in message.content if block.type == "text")
