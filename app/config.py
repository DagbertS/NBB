import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    """Lees een omgevingsvariabele en verwijder per ongeluk meegeplakte
    spaties of aanhalingstekens (veelvoorkomend bij dashboards als Railway)."""
    return os.getenv(name, default).strip().strip("'\"").strip()


SECRET_KEY = _env("SECRET_KEY", "dev-only-change-me")
DATABASE_PATH = BASE_DIR / _env("DATABASE_PATH", "data/nbb.db")
DOCUMENT_STORE = BASE_DIR / _env("DOCUMENT_STORE", "data/documents")

KBO_USERNAME = _env("KBO_USERNAME")
KBO_PASSWORD = _env("KBO_PASSWORD")
KBO_OPEN_DATA_URL = "https://kbopub.economie.fgov.be/kbo-open-data"

# De NBB geeft per product een eigen subscription key:
# - NBB_CBSO_SUBSCRIPTION_KEY: product "Authentic Data" (jaarrekeningen per
#   bedrijf raadplegen — individuele screenings en pipeline; dekt alleen
#   neerleggingen op het nieuwe CBSO-platform, sinds april 2022)
# - NBB_CBSO_ARCHIVE_KEY: product "Authentic Archive Data" (neerleggingen van
#   vóór april 2022 — de referentielijst toont ze, maar hun data komt uit
#   deze aparte archief-service)
# - NBB_CBSO_EXTRACT_KEY: product "Extracts" (Authentic Data Daily Extract —
#   de dagelijkse stroom nieuwe neerleggingen); valt terug op de eerste key
NBB_CBSO_SUBSCRIPTION_KEY = _env("NBB_CBSO_SUBSCRIPTION_KEY")
NBB_CBSO_ARCHIVE_KEY = _env("NBB_CBSO_ARCHIVE_KEY")
NBB_CBSO_EXTRACT_KEY = _env("NBB_CBSO_EXTRACT_KEY") or NBB_CBSO_SUBSCRIPTION_KEY
NBB_CBSO_BASE_URL = _env("NBB_CBSO_BASE_URL", "https://ws.cbso.nbb.be/authentic")
NBB_CBSO_ARCHIVE_URL = _env(
    "NBB_CBSO_ARCHIVE_URL", "https://ws.cbso.nbb.be/authentic-archive"
)

ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")

SMTP_HOST = _env("SMTP_HOST")
SMTP_PORT = int(_env("SMTP_PORT", "587") or "587")
SMTP_USERNAME = _env("SMTP_USERNAME")
SMTP_PASSWORD = _env("SMTP_PASSWORD")
SMTP_FROM = _env("SMTP_FROM", SMTP_USERNAME)

# Eerste beheerder: wordt bij het opstarten aangemaakt als er nog geen
# gebruikers bestaan (handig bij cloud-deploys zonder shell-toegang).
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
DOCUMENT_STORE.mkdir(parents=True, exist_ok=True)
