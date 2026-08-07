import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .database import Base, engine
from .routers import admin, auth, companies, dashboard, lists, nbb, prompts
from .scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
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
app.include_router(admin.router)
