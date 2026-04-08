from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import ProcessedContent
from app.services.content_pipeline import ContentPipelineService
from app.services.notifier import NotifierError, PushPlusNotifier
from app.services.system_settings import SystemSettingsService
from app.utils.helpers import get_timezone

LOGGER = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
except ImportError:  # pragma: no cover
    AsyncIOScheduler = None
    CronTrigger = None

_scheduler: AsyncIOScheduler | None = None


async def run_daily_collection_job() -> None:
    LOGGER.info('Starting scheduled collection job')
    settings_service = SystemSettingsService()

    async with AsyncSessionLocal() as session:
        runtime = await settings_service.get_or_create(session)
        result = await ContentPipelineService().collect_and_process(
            session,
            hours=runtime.fetch_lookback_hours,
        )
        LOGGER.info('Scheduled collection finished: %s', result.model_dump())

    async with AsyncSessionLocal() as session:
        runtime = await settings_service.get_or_create(session)
        if runtime.push_provider != 'pushplus' or not runtime.pushplus_token:
            LOGGER.info('Skipping scheduled push because PushPlus token is not configured')
            return

        cutoff = datetime.now(get_timezone(runtime.scheduler_timezone)) - timedelta(hours=runtime.fetch_lookback_hours)
        db_result = await session.execute(
            select(ProcessedContent)
            .where(ProcessedContent.collected_at >= cutoff)
            .order_by(ProcessedContent.importance_stars.desc(), ProcessedContent.collected_at.desc())
        )
        contents = list(db_result.scalars().all())

    if not contents:
        LOGGER.info('Skipping scheduled push because no processed contents were collected')
        return

    try:
        chunk_count, _ = await PushPlusNotifier(runtime.pushplus_token).send_daily_report(contents)
        LOGGER.info('Scheduled push finished with %s message chunk(s)', chunk_count)
    except NotifierError:
        LOGGER.exception('Scheduled push failed')
        raise


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
