"""Brug tussen de webapp en de screen-pipeline (fases 1-6).

De pipeline (screen/src/screen) blijft een zelfstandige bibliotheek; deze
module maakt haar importeerbaar vanuit de webapp, wijst haar datamap naar
het persistente volume (naast de databank) en draait de volledige build
als achtergrondtaak met een statusregel in de settings-tabel.
"""

import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..config import BASE_DIR, DATABASE_PATH

# Datamap van de pipeline: op het volume, zodat uploads en parquet-marts
# een redeploy overleven. SCREEN_DATA_ROOT moet gezet zijn vóór de eerste
# import van screen.config (die leest de variabele bij het importeren).
SCREEN_DATA_ROOT = Path(os.getenv("SCREEN_DATA_ROOT") or DATABASE_PATH.parent / "screen")
os.environ["SCREEN_DATA_ROOT"] = str(SCREEN_DATA_ROOT)

_SRC = str(BASE_DIR / "screen" / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from screen import config as pipeline_config  # noqa: E402  (na sys.path-opzet)

THESIS_PATH = SCREEN_DATA_ROOT / "thesis.yaml"
DEFAULT_THESIS_PATH = BASE_DIR / "screen" / "thesis.yaml"
MANUAL_SIGNALS_PATH = SCREEN_DATA_ROOT / "manual_signals.yaml"
TAXONOMY_UPLOAD_DIR = pipeline_config.RAW_DIR / "nbb" / "taxonomy" / "uploads"
BRIDGE_PATH = pipeline_config.RAW_DIR / "statbel" / "conversion_2008_2025.xlsx"
KBO_RAW_DIR = pipeline_config.RAW_DIR / "kbo"

STATUS_KEY = "screening_status"


class ScreeningError(Exception):
    """Fout met een boodschap die rechtstreeks aan de gebruiker getoond wordt."""


# ── status (zelfde patroon als de KBO-import: schrijfbaar vanuit een thread) ──

def set_status(text: str) -> None:
    with sqlite3.connect(DATABASE_PATH, timeout=60) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT DEFAULT '')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (STATUS_KEY, text),
        )
        conn.commit()


def get_status() -> str:
    try:
        with sqlite3.connect(DATABASE_PATH, timeout=60) as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (STATUS_KEY,)
            ).fetchone()
        return row[0] if row else ""
    except sqlite3.OperationalError:
        return ""


def is_running() -> bool:
    return get_status().startswith("bezig")


# ── thesis (zoekcriteria) ─────────────────────────────────────────────────────

def ensure_thesis() -> Path:
    """Kopieer het voorbeeld uit de repo naar het volume bij eerste gebruik."""
    if not THESIS_PATH.exists():
        THESIS_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DEFAULT_THESIS_PATH, THESIS_PATH)
    return THESIS_PATH


def load_thesis() -> pipeline_config.Thesis:
    return pipeline_config.load_thesis(ensure_thesis())


def save_thesis(raw: dict) -> pipeline_config.Thesis:
    """Schrijf de thesis atomisch: eerst naar een tijdelijk bestand,
    valideren met de pipeline-loader, en pas dan de echte vervangen."""
    import yaml

    ensure_thesis()
    tmp = THESIS_PATH.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    try:
        thesis = pipeline_config.load_thesis(tmp)  # ThesisError bij ongeldige invoer
    except pipeline_config.ThesisError:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(THESIS_PATH)
    return thesis


# ── databestanden: status en uploads ─────────────────────────────────────────

def _mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) \
        .strftime("%Y-%m-%d %H:%M UTC")


def data_status() -> dict:
    """Aanwezigheid van elke databron + de belangrijkste marts."""
    from screen.parse.taxonomy import load_taxonomy

    kbo_zips = sorted(KBO_RAW_DIR.glob("KboOpenData_*_Full.zip"))
    taxonomy = load_taxonomy()
    bridge_found = next(
        (p for p in (BRIDGE_PATH, BRIDGE_PATH.with_suffix(".csv"),
                     BRIDGE_PATH.with_suffix(".parquet")) if p.exists()),
        None,
    )
    nbb_dir = pipeline_config.RAW_DIR / "nbb"
    deposits = len(list(nbb_dir.glob("*.json"))) if nbb_dir.exists() else 0
    facts = pipeline_config.INTERIM_DIR / "facts.parquet"
    longlist = pipeline_config.MARTS_DIR / "longlist.parquet"

    longlist_rows = 0
    if longlist.exists():
        import polars as pl

        longlist_rows = pl.scan_parquet(longlist).select(pl.len()).collect().item()

    return {
        "kbo_zip": kbo_zips[-1].name if kbo_zips else None,
        "kbo_zip_mtime": _mtime(kbo_zips[-1]) if kbo_zips else None,
        "taxonomy_codes": len(taxonomy.codes) if taxonomy else 0,
        "bridge": bridge_found.name if bridge_found else None,
        "deposits": deposits,
        "facts": facts.exists(),
        "longlist_rows": longlist_rows,
        "longlist_mtime": _mtime(longlist),
        "manual_signals": MANUAL_SIGNALS_PATH.exists(),
        "data_root": str(SCREEN_DATA_ROOT),
    }


def _save_upload(upload, target: Path) -> Path:
    """Stream een geüpload bestand in blokken naar schijf (zips zijn GB's)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    with open(tmp, "wb") as out:
        while chunk := upload.file.read(8 * 1024 * 1024):
            out.write(chunk)
    tmp.replace(target)
    return target


def save_taxonomy_files(uploads: list) -> tuple[int, int]:
    """Bewaar model-/taxonomiebestanden en herbouw rubrics.csv uit ALLE
    aanwezige uploads samen. Geeft (aantal bestanden, aantal codes)."""
    from screen.ingest.taxonomy_import import TaxonomyImportError, import_taxonomy

    allowed = {".pdf", ".zip", ".xlsx", ".xls", ".csv"}
    for upload in uploads:
        name = Path(upload.filename or "").name
        if not name or Path(name).suffix.lower() not in allowed:
            raise ScreeningError(
                f"'{name or '?'}' heeft geen ondersteund formaat "
                "(.pdf, .zip, .xlsx of .csv)"
            )
        _save_upload(upload, TAXONOMY_UPLOAD_DIR / name)

    files = sorted(p for p in TAXONOMY_UPLOAD_DIR.iterdir() if p.is_file())
    try:
        _, count = import_taxonomy(files)
    except TaxonomyImportError as exc:
        raise ScreeningError(str(exc)) from exc
    return len(files), count


def save_bridge_file(upload) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in (".xlsx", ".csv"):
        raise ScreeningError("Conversietabel moet een .xlsx- of .csv-bestand zijn")
    target = BRIDGE_PATH.with_suffix(suffix)
    _save_upload(upload, target)
    from screen.peers.nace_bridge import BridgeError, load_bridge

    try:
        load_bridge(target)  # valideert kolommen '2008'/'2025' en inhoud
    except BridgeError as exc:
        target.unlink(missing_ok=True)
        raise ScreeningError(str(exc)) from exc
    return target


def save_kbo_zip(upload) -> Path:
    import zipfile

    name = Path(upload.filename or "").name
    if not name.lower().endswith(".zip"):
        raise ScreeningError("De KBO Open Data Full-dump moet een .zip zijn")
    if not (name.startswith("KboOpenData_") and name.endswith("_Full.zip")):
        # De build zoekt op patroon; bewaar onder een naam die erin past.
        name = "KboOpenData_00000_webupload_Full.zip"
    target = _save_upload(upload, KBO_RAW_DIR / name)
    if not zipfile.is_zipfile(target):
        target.unlink()
        raise ScreeningError("Het geüploade bestand is geen geldige zip")
    return target


def adopt_downloaded_zip() -> Path:
    """Hergebruik de Full-zip die de webapp-KBO-import al downloadde
    (hardlink waar mogelijk — zelfde volume, dus geen dubbele GB's)."""
    from .kbo_update import DOWNLOAD_DIR, _extract_number

    candidates = sorted(DOWNLOAD_DIR.glob("KboOpenData_*_Full.zip"),
                        key=lambda p: _extract_number(p.name))
    manual = DOWNLOAD_DIR / "manual_full.zip"
    source = candidates[-1] if candidates else (manual if manual.exists() else None)
    if source is None:
        raise ScreeningError(
            "Geen eerder gedownloade Full-zip gevonden — gebruik de KBO-import "
            "op het dashboard of upload de zip hieronder"
        )
    name = source.name if source.name.startswith("KboOpenData_") \
        else "KboOpenData_00000_dashboard_Full.zip"
    target = KBO_RAW_DIR / name
    if target.exists():
        return target
    KBO_RAW_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return target


def save_manual_signals(upload) -> int:
    """Bewaar het handmatige-signalen-yaml en valideer het meteen
    (elk signaal verplicht met enterprise_number, signal, source, as_of)."""
    from screen.signals.manual_input import ManualSignalError, load_manual_signals

    tmp = MANUAL_SIGNALS_PATH.with_suffix(".tmp")
    _save_upload(upload, tmp)
    try:
        signals = load_manual_signals(tmp)
    except ManualSignalError as exc:
        tmp.unlink(missing_ok=True)
        raise ScreeningError(str(exc)) from exc
    tmp.replace(MANUAL_SIGNALS_PATH)
    return len(signals)


# ── de volledige pipeline als achtergrondtaak ────────────────────────────────

def run_pipeline_bg() -> None:
    from screen import orchestrate
    from screen.score import build_longlist as ll
    from screen.signals.manual_input import ManualSignalError

    def progress(msg) -> None:
        text = str(msg).strip()
        if text:
            set_status(f"bezig: {text}")

    try:
        set_status("bezig: thesis laden ...")
        thesis = load_thesis()
        manual = MANUAL_SIGNALS_PATH if MANUAL_SIGNALS_PATH.exists() else None
        orchestrate.run_build(thesis, manual_signals=manual, progress=progress)
        set_status("bezig: longlist scoren ...")
        result = ll.build_longlist(thesis, progress=progress)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if result.row_count:
            set_status(f"klaar: longlist met {result.row_count} ondernemingen ({stamp})")
        else:
            set_status(
                f"klaar zonder longlist: geen universe — controleer of de KBO-zip "
                f"geladen is en de thesis-criteria niet te streng staan ({stamp})"
            )
    except (ManualSignalError, pipeline_config.ThesisError) as exc:
        set_status(f"fout: {exc}")
    except Exception as exc:  # rapporteer elke crash in de UI i.p.v. stil te sterven
        set_status(f"fout: {type(exc).__name__}: {exc}")


# ── longlist en one-pager voor de UI ─────────────────────────────────────────

LONGLIST_SORTS = {
    "score": ("score_total", True),
    "revenue": ("revenue", True),
    "ebitda": ("ebitda_proxy", True),
    "fte": ("fte", True),
    "name": ("name", False),
}


def load_longlist(q: str = "", target_class: str = "", min_score: float | None = None,
                  sort: str = "score"):
    """Gefilterde, gesorteerde longlist als polars-DataFrame (None = nog niet gebouwd)."""
    import polars as pl

    path = pipeline_config.MARTS_DIR / "longlist.parquet"
    if not path.exists():
        return None
    df = pl.read_parquet(path)
    if q:
        name_needle = q.strip().lower()
        number_needle = name_needle.replace(".", "").replace(" ", "")
        df = df.filter(
            pl.col("name").str.to_lowercase().str.contains(name_needle, literal=True)
            | pl.col("enterprise_number").str.contains(number_needle, literal=True)
        )
    if target_class:
        df = df.filter(pl.col("target_class") == target_class)
    if min_score is not None:
        df = df.filter(pl.col("score_total") >= min_score)
    column, descending = LONGLIST_SORTS.get(sort, LONGLIST_SORTS["score"])
    return df.sort(column, descending=descending, nulls_last=True)


def render_report_html(markdown_text: str) -> str:
    """Analyse-markdown -> HTML in de stijl van de app (tabellen incluis)."""
    import markdown as md_lib

    return md_lib.markdown(markdown_text, extensions=["tables"])


def onepager_markdown(enterprise_number: str) -> str:
    from screen.report import onepager

    try:
        return onepager.generate_report(enterprise_number)
    except onepager.ReportError as exc:
        raise ScreeningError(str(exc)) from exc


# ── bewaarde analyses ────────────────────────────────────────────────────────

def normalize_number(enterprise_number: str) -> str:
    return str(enterprise_number).upper().replace("BTW", "").replace("BE", "") \
        .replace(".", "").replace(" ", "").strip()


def save_analysis(db, enterprise_number: str, name: str, kind: str,
                  report_md: str, user_id: int | None):
    """Bewaar een analyse; identieke opeenvolgende versies worden niet
    gedupliceerd (de historiek toont alleen inhoudelijke wijzigingen)."""
    from ..models import Analysis

    num = normalize_number(enterprise_number)
    newest = (
        db.query(Analysis)
        .filter(Analysis.enterprise_number == num, Analysis.kind == kind)
        .order_by(Analysis.created_at.desc())
        .first()
    )
    if newest and newest.report_md == report_md:
        return newest
    analysis = Analysis(enterprise_number=num, company_name=name or "",
                        kind=kind, report_md=report_md, created_by_id=user_id)
    db.add(analysis)
    db.commit()
    return analysis


# ── individuele screening van één bedrijf ────────────────────────────────────

INDIVIDUAL_STATUS_KEY = "individual_status"


def _set_individual_status(text: str) -> None:
    with sqlite3.connect(DATABASE_PATH, timeout=60) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT DEFAULT '')"
        )
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                     (INDIVIDUAL_STATUS_KEY, text))
        conn.commit()


def get_individual_status() -> str:
    try:
        with sqlite3.connect(DATABASE_PATH, timeout=60) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?",
                               (INDIVIDUAL_STATUS_KEY,)).fetchone()
        return row[0] if row else ""
    except sqlite3.OperationalError:
        return ""


def sync_deposits_to_pipeline(enterprise_number: str) -> int:
    """Kopieer de CBSO-JSON-neerleggingen die de webapp voor dit bedrijf
    ophaalde (DOCUMENT_STORE) naar de pipeline-datamap (raw/nbb), zodat één
    opgehaald bestand overal in de applicatie bruikbaar is."""
    from ..config import DOCUMENT_STORE

    num = normalize_number(enterprise_number)
    src_dir = Path(DOCUMENT_STORE) / num
    if not src_dir.exists():
        return 0
    nbb_raw = pipeline_config.RAW_DIR / "nbb"
    nbb_raw.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in src_dir.glob("*.json"):
        target = nbb_raw / src.name
        if not target.exists():
            try:
                os.link(src, target)
            except OSError:
                shutil.copy2(src, target)
        n += 1
    return n


def _identity_from_kbo(num: str) -> dict:
    from .kbo_search import company_detail, nace_description

    detail = company_detail(num) or company_detail(
        f"{num[0:4]}.{num[4:7]}.{num[7:10]}" if len(num) == 10 else num
    )
    if not detail:
        return {}
    company = detail["company"]
    nace = company.get("main_nace") or ""
    return {
        "name": company.get("name") or "",
        "nace": nace,
        "nace_desc": nace_description(nace) if nace else "",
        "status": company.get("status") or "",
        "juridical_form": company.get("juridical_form") or "",
        "municipality": company.get("municipality") or "",
        "province": company.get("province") or "",
        "start_date": company.get("start_date") or "",
    }


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:,.1f}%"


def _fmt_ratio(value: float | None) -> str:
    return "—" if value is None else f"{value:,.1f}x"


def _company_metrics(num: str):
    import polars as pl

    metrics_path = pipeline_config.INTERIM_DIR / "metrics.parquet"
    if not metrics_path.exists():
        return pl.DataFrame()
    return (
        pl.read_parquet(metrics_path)
        .filter(pl.col("enterprise_number") == num)
        .sort("fiscal_year")
    )


def individual_markdown(enterprise_number: str, deposits: list[dict] | None = None,
                        skipped: list[tuple[str, str]] | None = None) -> str:
    """Individuele analyse van één bedrijf tegen zijn eigen gepubliceerde
    cijfers: identiteit, databasis (welke neerleggingen, wat parste wel/niet),
    kerncijfers per boekjaar, afgeleide ratio's en neerleggingsgedrag.
    Bewust zonder peer-vergelijking: die hoort bij de pipeline-longlist."""
    import polars as pl
    from screen.report.onepager import REPORT_METRICS, _fmt

    num = normalize_number(enterprise_number)
    identity = _identity_from_kbo(num)
    lines: list[str] = []
    add = lines.append

    add(f"# {identity.get('name') or 'Naam onbekend'} — {num}")
    add("")
    add("## Identiteit (KBO)")
    add("")
    add("| | |")
    add("|---|---|")
    add(f"| Ondernemingsnummer | {num} |")
    if identity:
        nace_txt = identity["nace"] + (f" — {identity['nace_desc']}" if identity["nace_desc"] else "")
        add(f"| Hoofdactiviteit | {nace_txt or '—'} |")
        add(f"| Rechtsvorm | {identity['juridical_form'] or '—'} |")
        add(f"| Gemeente | {identity['municipality'] or '—'} |")
        add(f"| Provincie | {identity['province'] or '—'} |")
        add(f"| Status | {'actief' if identity['status'] == 'AC' else identity['status'] or '—'} |")
        add(f"| Opgericht | {identity['start_date'] or '—'} |")
    else:
        add("| KBO-gegevens | niet lokaal beschikbaar (KBO-data nog niet geladen) |")
    add("")

    # ── databasis: wat hebben we, wat parste, wat niet ────────────────────
    add("## Databasis")
    add("")
    if deposits:
        add(f"{len(deposits)} neerlegging(en) bij de NBB gevonden:")
        add("")
        add("| Referentie | Neergelegd | Boekjaar t.e.m. | Model | Formaat |")
        add("|---|---|---|---|---|")
        for d in deposits:
            add(f"| {d['reference']} | {d['deposit_date'] or '—'} | "
                f"{d['exercise_end'] or '—'} | {d['model_type'] or '—'} | "
                f"{d['data_format']} |")
        add("")
        pdf_only = all(d["data_format"] != "JSON" for d in deposits)
        if pdf_only:
            add("> ⚠ Geen enkele neerlegging is als gestructureerde data (JSON) "
                "beschikbaar — alleen PDF. Cijfers kunnen dan niet automatisch "
                "gelezen worden; de PDF's zijn wel te downloaden op de "
                "bedrijfsfiche.")
            add("")
    elif deposits is not None:
        add("_Geen neerleggingen gevonden bij de NBB voor dit nummer._")
        add("")
    if skipped:
        add("> ⚠ Niet verwerkt (parseerfout — technische oorzaak, geen oordeel "
            "over het bedrijf):")
        for name, reason in skipped:
            add(f"> - {name}: {reason}")
        add("")

    # ── kerncijfers per boekjaar ──────────────────────────────────────────
    add("## Kerncijfers per boekjaar")
    add("")
    company_metrics = _company_metrics(num)
    years: list[int] = []
    if company_metrics.is_empty():
        add("_Geen automatisch leesbare jaarrekeningcijfers beschikbaar (zie "
            "Databasis hierboven voor de reden)._")
        add("")
    else:
        years = company_metrics["fiscal_year"].to_list()[-5:]
        sub = company_metrics.filter(pl.col("fiscal_year").is_in(years))
        add("| Metric | " + " | ".join(str(y) for y in years) + " |")
        add("|---" * (len(years) + 1) + "|")
        estimates = False
        table_metrics = REPORT_METRICS + [("personnel_cost", "Personeelskosten"),
                                          ("value_added", "Toegevoegde waarde (proxy)")]
        for metric, label in table_metrics:
            if metric not in sub.columns:
                continue
            cells = []
            for y in years:
                r = sub.filter(pl.col("fiscal_year") == y)
                if r.height == 0:
                    cells.append("—")
                    continue
                value = r[metric][0]
                source_col = f"{metric}_source"
                source = r[source_col][0] if source_col in r.columns else None
                cell = _fmt(value, metric)
                if source == "estimate" and value is not None:
                    cell += " *(schatting)*"
                    estimates = True
                cells.append(cell)
            add(f"| {label} | " + " | ".join(cells) + " |")
        add("")
        if estimates:
            add("> *(schatting)* = afgeleide waarde (bv. omzetproxy uit "
                "brutomarge), géén gepubliceerd feit.")
            add("")

        # ── afgeleide ratio's: feitelijke economische parameters ──────────
        add("## Economische ratio's (berekend uit bovenstaande cijfers)")
        add("")
        add("| Ratio | " + " | ".join(str(y) for y in years) + " |")
        add("|---" * (len(years) + 1) + "|")

        by_year = {int(r["fiscal_year"]): r for r in sub.iter_rows(named=True)}

        def ratio_row(label, fn, fmt):
            cells = []
            for y in years:
                r = by_year.get(y)
                cells.append(fmt(fn(r)) if r else "—")
            add(f"| {label} | " + " | ".join(cells) + " |")

        def _div(a, b):
            if a is None or b in (None, 0):
                return None
            return a / b

        ratio_row("EBITDA-marge", lambda r: _div(r.get("ebitda_proxy"), r.get("revenue")), _fmt_pct)
        ratio_row("Solvabiliteit (EV/balans)", lambda r: _div(r.get("equity"), r.get("balance_total")), _fmt_pct)
        ratio_row("Netto schuld / EBITDA",
                  lambda r: _div(r.get("net_financial_debt"), r.get("ebitda_proxy"))
                  if (r.get("ebitda_proxy") or 0) > 0 else None, _fmt_ratio)
        ratio_row("Personeelskost / VTE (EUR)", lambda r: _div(r.get("personnel_cost"), r.get("fte")),
                  lambda v: "—" if v is None else f"{v:,.0f}")
        ratio_row("Klantendagen (DSO)", lambda r: r.get("dso_days"),
                  lambda v: "—" if v is None else f"{v:,.0f}")
        add("")

        # groei over de beschikbare jaren
        growth_basis = "revenue"
        series = [(y, by_year[y].get("revenue")) for y in years
                  if by_year.get(y, {}).get("revenue") is not None
                  and by_year[y].get("revenue_source") != "estimate"]
        if len(series) < 2:
            growth_basis = "ebitda_proxy"
            series = [(y, by_year[y].get("ebitda_proxy")) for y in years
                      if by_year.get(y, {}).get("ebitda_proxy") is not None]
        if len(series) >= 2 and series[0][1] and series[0][1] > 0 and series[-1][1] > 0:
            span = series[-1][0] - series[0][0]
            if span > 0:
                cagr = (series[-1][1] / series[0][1]) ** (1 / span) - 1
                label = "omzet" if growth_basis == "revenue" else "EBITDA-proxy"
                add(f"**Groei ({label}, {series[0][0]}–{series[-1][0]}): "
                    f"{cagr * 100:+.1f}% per jaar (CAGR)**")
                add("")

    add("> Peer-vergelijking maakt bewust geen deel uit van een individuele "
        "analyse; kwartielen t.o.v. sectorgenoten vind je in de "
        "screening-longlist van een dossier.")
    add("")

    # ── neerleggingsgedrag / signalen ─────────────────────────────────────
    add("## Neerleggingsgedrag en signalen")
    add("")
    signals_path = pipeline_config.MARTS_DIR / "signals.parquet"
    company_signals = pl.DataFrame()
    if signals_path.exists():
        company_signals = pl.read_parquet(signals_path).filter(
            pl.col("enterprise_number") == num
        )
    if company_signals.is_empty():
        add("_Geen signalen geregistreerd (geen laattijdige neerleggingen, "
            "modelwissels of correcties gedetecteerd in de beschikbare data)._")
    else:
        add("| Signaal | Waarde | Bron | Datum |")
        add("|---|---|---|---|")
        for s in company_signals.sort("as_of").iter_rows(named=True):
            add(f"| {s['signal']} | {s['value']} | {s['source']} | {s['as_of']} |")
    add("")
    add("---")
    add("_Individuele screening — methodologie en aannames: docs/METHODOLOGY.md. "
        "Geschatte waarden zijn nooit feiten._")
    return "\n".join(lines)


def ai_commentary(markdown_report: str, company_name: str) -> str | None:
    """Optionele AI-interpretatie: Claude leest uitsluitend de berekende
    cijfers hierboven en schrijft er een analistencommentaar bij. Faalt stil
    (None) zonder key of bij een API-fout — de feiten staan er dan nog steeds."""
    from ..config import ANTHROPIC_API_KEY

    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-opus-5",
            max_tokens=2500,
            system=(
                "Je bent een senior M&A-analist. Je krijgt een feitenrapport "
                "over één Belgisch bedrijf (KBO-identiteit, jaarrekeningcijfers, "
                "ratio's, neerleggingsgedrag). Schrijf in het Nederlands een "
                "beknopt commentaar met exact deze koppen (##): Financiële "
                "beoordeling, Sterktes, Aandachtspunten en risico's, Conclusie "
                "vanuit overnameperspectief. Baseer je UITSLUITEND op de "
                "aangeleverde cijfers; verzin niets bij. Benoem expliciet welke "
                "informatie ontbreekt en wat als schatting gemarkeerd staat. "
                "Wees concreet en zakelijk, geen holle frasen."
            ),
            messages=[{"role": "user", "content":
                       f"Feitenrapport voor {company_name}:\n\n{markdown_report}"}],
        )
        text = "".join(b.text for b in message.content if b.type == "text").strip()
        return text or None
    except Exception:
        return None


def run_individual_screening_bg(enterprise_number: str, user_id: int | None) -> None:
    """Achtergrondtaak: NBB-neerleggingen ophalen (als er een CBSO-key is),
    facts/metrics/signalen herbouwen en het resultaat als analyse bewaren."""
    from ..database import SessionLocal

    num = normalize_number(enterprise_number)
    note = ""
    try:
        _set_individual_status(f"bezig: {num} — NBB-neerleggingen ophalen ...")
        db = SessionLocal()
        try:
            try:
                from .cbso_client import CbsoError, fetch_and_store

                fetch_and_store(db, num)
            except CbsoError as exc:
                note = f"NBB niet geraadpleegd: {exc}"
            except Exception as exc:
                note = f"NBB-ophaling mislukt ({type(exc).__name__}: {exc}) — lokale data gebruikt"

            from ..models import NbbDeposit

            deposit_rows = (
                db.query(NbbDeposit)
                .filter(NbbDeposit.enterprise_number == num)
                .order_by(NbbDeposit.deposit_date.desc())
                .all()
            )
            deposits = [{
                "reference": d.reference, "deposit_date": d.deposit_date,
                "exercise_end": d.exercise_end, "model_type": d.model_type,
                "data_format": d.data_format,
            } for d in deposit_rows]
            references = {d.reference for d in deposit_rows}

            skipped: list[tuple[str, str]] = []
            copied = sync_deposits_to_pipeline(num)
            if copied:
                _set_individual_status(f"bezig: {num} — cijfers herberekenen ...")
                from screen import build as build_mod
                from screen.normalize import build_metrics as norm
                from screen.peers import peer_set
                from screen.signals import build_signals as sig

                facts_result = build_mod.build_facts(progress=lambda *_: None)
                # alleen de parseerfouten van dít bedrijf tonen
                skipped = [(name, reason) for name, reason in facts_result.skipped
                           if Path(name).stem in references]
                ratio, ratio_note = None, ""
                universe_path = pipeline_config.INTERIM_DIR / "universe.parquet"
                if universe_path.exists():
                    import polars as pl

                    ratio, ratio_note = peer_set.compute_revenue_ratio(
                        pl.read_parquet(universe_path)
                    )
                norm.build_metrics(revenue_ratio=ratio, revenue_ratio_note=ratio_note,
                                   progress=lambda *_: None)
                manual = MANUAL_SIGNALS_PATH if MANUAL_SIGNALS_PATH.exists() else None
                sig.build_signals(manual_path=manual, progress=lambda *_: None)

            _set_individual_status(f"bezig: {num} — rapport opstellen ...")
            report = individual_markdown(num, deposits=deposits, skipped=skipped)
            if note:
                report += f"\n\n> ⚠ {note}\n"

            identity = _identity_from_kbo(num)
            name = identity.get("name", "")
            _set_individual_status(f"bezig: {num} — AI-interpretatie schrijven ...")
            commentary = ai_commentary(report, name or num)
            if commentary:
                report += ("\n\n# Interpretatie (AI-analist)\n\n"
                           "> Automatisch commentaar, uitsluitend gebaseerd op de "
                           "cijfers hierboven — geen vervanging van due diligence.\n\n"
                           + commentary + "\n")
            save_analysis(db, num, name, "individueel", report, user_id)
        finally:
            db.close()
        _set_individual_status(f"klaar: individuele screening van {num} bewaard")
    except Exception as exc:
        _set_individual_status(f"fout: {num} — {type(exc).__name__}: {exc}")


def adopt_zip_file(path: Path) -> None:
    """Zet een elders gedownloade Full-zip ook in de pipeline-datamap
    (hardlink, geen dubbele GB's). Stil bij mislukking — dit is een bonus."""
    try:
        name = path.name if path.name.startswith("KboOpenData_") \
            else "KboOpenData_00000_dashboard_Full.zip"
        target = KBO_RAW_DIR / name
        if target.exists():
            return
        KBO_RAW_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.link(path, target)
        except OSError:
            shutil.copy2(path, target)
    except Exception:
        pass
