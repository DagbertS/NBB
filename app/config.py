import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
DATABASE_PATH = BASE_DIR / os.getenv("DATABASE_PATH", "data/nbb.db")
DOCUMENT_STORE = BASE_DIR / os.getenv("DOCUMENT_STORE", "data/documents")

KBO_USERNAME = os.getenv("KBO_USERNAME", "")
KBO_PASSWORD = os.getenv("KBO_PASSWORD", "")
KBO_OPEN_DATA_URL = "https://kbopub.economie.fgov.be/kbo-open-data"

NBB_CBSO_SUBSCRIPTION_KEY = os.getenv("NBB_CBSO_SUBSCRIPTION_KEY", "")
NBB_CBSO_BASE_URL = os.getenv("NBB_CBSO_BASE_URL", "https://ws.cbso.nbb.be/authentic")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USERNAME)

# Eerste beheerder: wordt bij het opstarten aangemaakt als er nog geen
# gebruikers bestaan (handig bij cloud-deploys zonder shell-toegang).
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
DOCUMENT_STORE.mkdir(parents=True, exist_ok=True)
