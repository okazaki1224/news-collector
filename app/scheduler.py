import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.collector import collect_all
from app.database import SessionLocal

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
_next_run: datetime | None = None


async def _scheduled_job():
    global _next_run
    logger.info("Starting scheduled news collection")
    db = SessionLocal()
    try:
        await collect_all(db, trigger="scheduled")
    finally:
        db.close()

    job = scheduler.get_job("hourly_collection")
    if job and job.next_run_time:
        _next_run = job.next_run_time.replace(tzinfo=None)


def start_scheduler():
    global _next_run
    if scheduler.running:
        return

    scheduler.add_job(
        _scheduled_job,
        trigger=IntervalTrigger(hours=1),
        id="hourly_collection",
        name="Hourly news collection",
        replace_existing=True,
    )
    scheduler.start()

    job = scheduler.get_job("hourly_collection")
    if job and job.next_run_time:
        _next_run = job.next_run_time.replace(tzinfo=None)

    logger.info("Scheduler started (interval: 1 hour)")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def get_next_run() -> datetime | None:
    job = scheduler.get_job("hourly_collection")
    if job and job.next_run_time:
        return job.next_run_time.replace(tzinfo=None)
    return _next_run
