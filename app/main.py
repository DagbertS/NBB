import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .database import Base, engine
from .routers import (admin, analyses, auth, companies, dashboard, lists, nbb,
                      prompts, screening)
from .scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO)


def _bootstrap_admin() -> None:
    """Maak de eerste admin aan via ADMIN_EMAIL/ADMIN_PASSWORD (cloud-deploys)."""
    from .config import ADMIN_EMAIL, ADMIN_PASSWORD
    from .database import SessionLocal
    from .models import User
    from .security import hash_password

    if not (ADMIN_EMAIL and ADMIN_PASSWORD):
        return
    with SessionLocal() as db:
        if db.query(User).count() == 0:
            db.add(
                User(
                    email=ADMIN_EMAIL.strip().lower(),
                    password_hash=hash_password(ADMIN_PASSWORD),
                    role="admin",
                )
            )
            db.commit()
            logging.info("Eerste admin aangemaakt: %s", ADMIN_EMAIL)


def _migrate() -> None:
    """Minimale schema-migraties voor bestaande databases (create_all voegt
    geen kolommen toe aan al bestaande tabellen)."""
    from sqlalchemy import text

    with engine.connect() as conn:
        cols = [row[1] for row in
                conn.execute(text("PRAGMA table_info(screening_pipelines)"))]
        if cols and "thesis_name" not in cols:
            conn.execute(text("ALTER TABLE screening_pipelines "
                              "ADD COLUMN thesis_name VARCHAR(120) DEFAULT ''"))
            conn.commit()
        cols = [row[1] for row in
                conn.execute(text("PRAGMA table_info(company_list_items)"))]
        if cols and "nbb_error" not in cols:
            conn.execute(text("ALTER TABLE company_list_items "
                              "ADD COLUMN nbb_error TEXT DEFAULT ''"))
            conn.commit()
        if cols and "nbb_note" not in cols:
            conn.execute(text("ALTER TABLE company_list_items "
                              "ADD COLUMN nbb_note TEXT DEFAULT ''"))
            conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    _migrate()
    _bootstrap_admin()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="NBB M&A Screening", lifespan=lifespan)

app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="static",
)

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(companies.router)
app.include_router(lists.router)
app.include_router(nbb.router)
app.include_router(prompts.router)
app.include_router(screening.router)
app.include_router(analyses.router)
app.include_router(admin.router)
