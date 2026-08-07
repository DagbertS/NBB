"""Versionering van ruwe downloads: elk bestand in data/raw/ krijgt een
manifestregel met sha256, grootte, bron-URL en ophaaldatum (append-only
JSONL). Idempotentie: een bestand dat al met dezelfde hash geregistreerd
is, wordt niet opnieuw gedownload of geregistreerd."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import RAW_DIR

MANIFEST_PATH = RAW_DIR / "manifest.jsonl"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        return []
    entries = []
    for line in MANIFEST_PATH.read_text().splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def is_registered(path: Path, sha256: str | None = None) -> bool:
    rel = str(path.relative_to(RAW_DIR)) if path.is_absolute() else str(path)
    for entry in read_manifest():
        if entry["file"] == rel and (sha256 is None or entry["sha256"] == sha256):
            return True
    return False


def register(path: Path, source: str, source_url: str = "") -> dict:
    """Registreer een bestand in het manifest (skip als identiek al bekend)."""
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256_of(path)
    rel = str(path.relative_to(RAW_DIR)) if path.is_absolute() else str(path)
    if is_registered(path, digest):
        for entry in read_manifest():
            if entry["file"] == rel and entry["sha256"] == digest:
                return entry
    entry = {
        "file": rel,
        "sha256": digest,
        "bytes": path.stat().st_size,
        "source": source,
        "source_url": source_url,
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(MANIFEST_PATH, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
