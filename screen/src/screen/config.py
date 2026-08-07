"""Configuratie: datapaden, .env en de thesis (vrij invulbare zoekcriteria)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Repo-root: screen/src/screen/config.py -> 3 niveaus omhoog
REPO_ROOT = Path(os.getenv("SCREEN_REPO_ROOT", Path(__file__).resolve().parents[3]))
load_dotenv(REPO_ROOT / ".env")

DATA_ROOT = Path(os.getenv("SCREEN_DATA_ROOT", REPO_ROOT / "data"))
RAW_DIR = DATA_ROOT / "raw"
INTERIM_DIR = DATA_ROOT / "interim"
MARTS_DIR = DATA_ROOT / "marts"
REPORTS_DIR = DATA_ROOT / "reports"

DEFAULT_THESIS_PATH = REPO_ROOT / "screen" / "thesis.yaml"
DEFAULT_OVERRIDES_PATH = REPO_ROOT / "screen" / "overrides.yaml"

VALID_PROVINCES = {
    "Brussel", "Waals-Brabant", "Vlaams-Brabant", "Antwerpen", "Limburg",
    "Luik", "Namen", "Henegouwen", "Luxemburg", "West-Vlaanderen",
    "Oost-Vlaanderen",
}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip().strip("'\"").strip()


KBO_USERNAME = _env("KBO_USERNAME")
KBO_PASSWORD = _env("KBO_PASSWORD")
NBB_CBSO_SUBSCRIPTION_KEY = _env("NBB_CBSO_SUBSCRIPTION_KEY")


class ThesisError(ValueError):
    """Ongeldige thesis-configuratie, met leesbare uitleg."""


@dataclass
class SizeCriteria:
    balance_total_min: float | None = None
    balance_total_max: float | None = None
    fte_min: float | None = None
    fte_max: float | None = None


@dataclass
class Thesis:
    """De vrij invulbare zoekcriteria van één dossier."""

    name: str
    sector: str = ""
    nace_version: int = 2025
    nace_codes: list[str] = field(default_factory=list)
    provinces: list[str] = field(default_factory=list)
    size: SizeCriteria = field(default_factory=SizeCriteria)
    platform_criteria: dict = field(default_factory=dict)
    bolt_on_criteria: dict = field(default_factory=dict)
    weights: dict = field(default_factory=dict)

    @property
    def nace_prefixes(self) -> list[str]:
        """Codes zonder punten, voor prefixmatching ('37.00' -> '3700')."""
        return [c.replace(".", "").strip() for c in self.nace_codes]


def load_thesis(path: str | Path | None = None) -> Thesis:
    path = Path(path) if path else DEFAULT_THESIS_PATH
    if not path.exists():
        raise ThesisError(f"Thesis-bestand niet gevonden: {path}")
    raw = yaml.safe_load(path.read_text()) or {}

    if not raw.get("name"):
        raise ThesisError("thesis.yaml: 'name' is verplicht")

    nace_codes = [str(c) for c in raw.get("nace_codes") or []]
    if not nace_codes:
        raise ThesisError("thesis.yaml: minstens één NACE-code vereist in 'nace_codes'")

    nace_version = int(raw.get("nace_version", 2025))
    if nace_version not in (2008, 2025):
        raise ThesisError("thesis.yaml: 'nace_version' moet 2008 of 2025 zijn")

    provinces = [str(p) for p in (raw.get("geography") or {}).get("provinces") or []]
    unknown = [p for p in provinces if p not in VALID_PROVINCES]
    if unknown:
        raise ThesisError(
            f"thesis.yaml: onbekende provincie(s) {unknown}; "
            f"geldig: {sorted(VALID_PROVINCES)}"
        )

    size_raw = raw.get("size") or {}
    size = SizeCriteria(
        balance_total_min=size_raw.get("balance_total_min"),
        balance_total_max=size_raw.get("balance_total_max"),
        fte_min=size_raw.get("fte_min"),
        fte_max=size_raw.get("fte_max"),
    )
    for lo, hi, label in (
        (size.balance_total_min, size.balance_total_max, "balance_total"),
        (size.fte_min, size.fte_max, "fte"),
    ):
        if lo is not None and hi is not None and lo > hi:
            raise ThesisError(f"thesis.yaml: size.{label}_min > size.{label}_max")

    weights = {k: float(v) for k, v in (raw.get("weights") or {}).items()}
    if weights and abs(sum(weights.values()) - 1.0) > 1e-6:
        raise ThesisError(
            f"thesis.yaml: gewichten moeten optellen tot 1.0 (nu {sum(weights.values()):.3f})"
        )

    targets = raw.get("targets") or {}
    return Thesis(
        name=str(raw["name"]),
        sector=str(raw.get("sector", "")),
        nace_version=nace_version,
        nace_codes=nace_codes,
        provinces=provinces,
        size=size,
        platform_criteria=targets.get("platform") or {},
        bolt_on_criteria=targets.get("bolt_on") or {},
        weights=weights,
    )


def load_overrides(path: str | Path | None = None) -> dict:
    path = Path(path) if path else DEFAULT_OVERRIDES_PATH
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def ensure_data_dirs() -> None:
    for d in (RAW_DIR, INTERIM_DIR, MARTS_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
