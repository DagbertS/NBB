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


def onepager_markdown(enterprise_number: str) -> str:
    from screen.report import onepager

    try:
        return onepager.generate_report(enterprise_number)
    except onepager.ReportError as exc:
        raise ScreeningError(str(exc)) from exc
