"""Parser: CBSO JSON-XBRL-neerlegging -> tidy feitrijen.

Veldnamen volgen de CBSO-webservices technical guide (zie docs/SOURCES.md);
zolang we op fixtures draaien is de mapping 'gebaseerd op documentatie,
te verifiëren op de eerste echte neerlegging'. De parser is daarom strikt:
ontbrekende kernvelden geven een ParseError met uitleg, geen stille gok.

Eén feitrij per rubriek per periode:
  enterprise_number, fiscal_year, schema_type, rubric_code, period, value,
  deposit_reference, deposit_date, model, source, as_of
`source` = 'nbb' (officiële neerlegging); afgeleide waarden bestaan hier
nooit — die ontstaan pas in normalize (fase 3) en krijgen source='derived'.
"""

import json
from datetime import date
from pathlib import Path


class ParseError(Exception):
    pass


def _get(d: dict, *names, default=None, required=False, context=""):
    """Veldnaam-tolerante lookup (documentatie wisselt tussen casings)."""
    for name in names:
        for key in (name, name[0].lower() + name[1:], name.upper(), name.lower()):
            if key in d:
                return d[key]
    if required:
        raise ParseError(f"Verplicht veld {names[0]} ontbreekt {context}".strip())
    return default


def normalize_enterprise_number(value: str) -> str:
    num = str(value).replace(".", "").replace(" ", "")
    if not (num.isdigit() and len(num) == 10):
        raise ParseError(f"Ongeldig ondernemingsnummer: {value!r}")
    return num


def _fiscal_year(exercise_dates: dict, context: str) -> int:
    end = _get(exercise_dates or {}, "EndDate", required=True,
               context=f"(ExerciseDates, {context})")
    return date.fromisoformat(str(end)[:10]).year


def _structure_sketch(doc, depth: int = 0) -> str:
    """Compacte beschrijving van de werkelijke JSON-structuur, voor in de
    foutmelding: zo vertelt de eerste échte neerlegging ons zelf hoe het
    officiële formaat eruitziet (veldnamen konden nooit live geverifieerd
    worden — zie docs/SOURCES.md)."""
    if isinstance(doc, list):
        inner = f" — eerste element: {_structure_sketch(doc[0], depth + 1)}" \
            if doc and depth < 2 else ""
        return f"lijst[{len(doc)}]{inner}"
    if not isinstance(doc, dict):
        return type(doc).__name__
    parts = [f"velden: {list(doc.keys())[:10]}"]
    facts = doc.get("facts")
    if isinstance(facts, dict) and facts and depth < 2:
        first = next(iter(facts.values()))
        if isinstance(first, dict):
            parts.append(f"voorbeeldfact: {list(first.keys())[:6]}")
            dims = first.get("dimensions")
            if isinstance(dims, dict):
                sample = {k: str(v)[:60] for k, v in list(dims.items())[:6]}
                parts.append(f"dimensions: {sample}")
    doc_info = doc.get("documentInfo")
    if isinstance(doc_info, dict):
        parts.append(f"documentInfo: {list(doc_info.keys())[:8]}")
    return "; ".join(parts)


def _locate_body(doc):
    """Vind het object met de neerleggingsvelden: bovenaan, in een lijst of
    één niveau dieper ingepakt (bv. onder 'Content' of 'Deposit')."""
    candidates = [doc]
    if isinstance(doc, list):
        candidates = [el for el in doc if isinstance(el, dict)]
    elif isinstance(doc, dict):
        candidates += [v for v in doc.values() if isinstance(v, dict)]
        candidates += [el for v in doc.values() if isinstance(v, list)
                       for el in v if isinstance(el, dict)]
    for cand in candidates:
        if isinstance(cand, dict) and _get(cand, "EnterpriseNumber") is not None:
            return cand
    return None


def parse_deposit(path: str | Path) -> list[dict]:
    """Eén JSON-XBRL-bestand -> lijst feitrijen (kan leeg zijn)."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ParseError(f"{path.name}: geen geldige JSON ({exc})") from exc

    doc = _locate_body(raw)
    if doc is None:
        raise ParseError(
            f"{path.name}: onbekend formaat — geen EnterpriseNumber gevonden. "
            f"Werkelijke structuur: {_structure_sketch(raw)}"
        )

    ctx = f"in {path.name}"
    enterprise = normalize_enterprise_number(
        _get(doc, "EnterpriseNumber", required=True, context=ctx)
    )
    reference = str(_get(doc, "ReferenceNumber", "Reference", required=True, context=ctx))
    deposit_date = str(_get(doc, "DepositDate", default=""))[:10]
    model = str(_get(doc, "ModelType", "Model", default="unknown"))
    schema_type = classify_schema(model)
    exercise_dates = _get(doc, "ExerciseDates", default={})
    fiscal_year = _fiscal_year(exercise_dates, ctx)
    exercise_start = str(_get(exercise_dates, "StartDate", default=""))[:10]
    exercise_end = str(_get(exercise_dates, "EndDate", default=""))[:10]

    rubrics = _get(doc, "Rubrics", "rubrics", default=[])
    rows: list[dict] = []
    for i, rubric in enumerate(rubrics):
        code = _get(rubric, "Code", required=True, context=f"(rubriek {i} {ctx})")
        raw_value = _get(rubric, "Value", required=True, context=f"(rubriek {code} {ctx})")
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ParseError(
                f"Rubriek {code} {ctx}: waarde {raw_value!r} is niet numeriek"
            ) from exc
        rows.append({
            "enterprise_number": enterprise,
            "fiscal_year": fiscal_year,
            "schema_type": schema_type,
            "rubric_code": str(code).strip(),
            "period": str(_get(rubric, "Period", default="N")),
            "value": value,
            "deposit_reference": reference,
            "deposit_date": deposit_date,
            "exercise_start": exercise_start,
            "exercise_end": exercise_end,
            "model": model,
            "source": "nbb",
            "as_of": deposit_date,
        })
    return rows


def classify_schema(model: str) -> str:
    """Modelcode -> volledig | verkort | micro | unknown.

    Gebaseerd op de CBSO-modelnamen (f=full, a=abbreviated, m=micro in de
    modelcodes zoals m01-f / m01-a / m01-m); te verifiëren op echte data.
    """
    m = model.lower()
    if m.endswith("-f") or "full" in m or "volledig" in m:
        return "volledig"
    if m.endswith("-a") or "abbreviated" in m or "verkort" in m:
        return "verkort"
    if m.endswith("-m") or "micro" in m:
        return "micro"
    return "unknown"
