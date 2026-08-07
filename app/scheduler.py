"""Geplande taken:
- dagelijks: NBB daily extract ophalen + prompt-analyses draaien en mailen
- dagelijks: controleren op nieuwe KBO Open Data update-zips
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .database import SessionLocal

log = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="Europe/Brussels")


def job_daily_extract_and_prompts():
    from .services.daily_extract import run_daily_sync
    from .services.prompt_runner import run_all_enabled_prompts

    db = SessionLocal()
    try:
        n = run_daily_sync(db)
        log.info("Daily extract: %s nieuwe neerleggingen", n)
        run_all_enabled_prompts(db)
    finally:
        db.close()


def job_kbo_updates():
    from .services.kbo_update import check_and_apply_updates

    applied = check_and_apply_updates()
    if applied:
        log.info("KBO-updates toegepast: %s", applied)


def start_scheduler():
    # 07:00 Belgische tijd: extract van gisteren is dan gepubliceerd
    scheduler.add_job(job_daily_extract_and_prompts, "cron", hour=7, minute=0,
                      id="daily_extract", replace_existing=True)
    # 05:30: check op nieuwe KBO-updates (maandelijks gepubliceerd)
    scheduler.add_job(job_kbo_updates, "cron", hour=5, minute=30,
                      id="kbo_updates", replace_existing=True)
    scheduler.start()


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
