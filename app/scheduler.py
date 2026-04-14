from __future__ import annotations

import logging
from app.services.scheduled_push_runner import ScheduledPushRunner

LOGGER = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
except ImportError:  # pragma: no cover
    AsyncIOScheduler = None
    CronTrigger = None

_scheduler: AsyncIOScheduler | None = None


async def run_daily_collection_job() -> None:
    await ScheduledPushRunner().run(attempt_slot=1)


async def start_scheduler() -> None:
    global _scheduler

    if AsyncIOScheduler is None or CronTrigger is None:
        LOGGER.warning('APScheduler is not installed; scheduler will not start')
        return

    if _scheduler is not None:
        return

    async with AsyncSessionLocal() as session:
        runtime = await SystemSettingsService().get_or_create(session)

    if not runtime.scheduler_enabled:
        LOGGER.info('Scheduler disabled by runtime settings')
        return

    timezone = get_timezone(runtime.scheduler_timezone)
    scheduler = AsyncIOScheduler(timezone=timezone)
    scheduler.add_job(
        run_daily_collection_job,
        CronTrigger(hour=runtime.daily_report_hour, minute=runtime.daily_report_minute, timezone=timezone),
        id='daily_collection_job',
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    LOGGER.info(
        'Scheduler started: daily job at %02d:%02d %s',
        runtime.daily_report_hour,
        runtime.daily_report_minute,
        runtime.scheduler_timezone,
    )


async def reload_scheduler() -> None:
    await stop_scheduler()
    await start_scheduler()


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
