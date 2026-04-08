from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import ProcessedContent
from app.services.content_pipeline import ContentPipelineService
from app.services.notifier import NotifierError, WeComNotifier
from app.utils.helpers import get_timezone

LOGGER = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
except ImportError:  # pragma: no cover - optional dependency during bootstrap
    AsyncIOScheduler = None
    CronTrigger = None

_scheduler: AsyncIOScheduler | None = None


async def run_daily_collection_job() -> None:
    settings = get_settings()
    LOGGER.info('Starting scheduled collection job')

    async with AsyncSessionLocal() as session:
        result = await ContentPipelineService().collect_and_process(
            session,
            hours=settings.fetch_lookback_hours,
        )
        LOGGER.info('Scheduled collection finished: %s', result.model_dump())

    if not settings.wecom_webhook_url:
        LOGGER.info('Skipping scheduled push because WECOM_WEBHOOK_URL is not configured')
        return

    async with AsyncSessionLocal() as session:
        cutoff = datetime.now(get_timezone(settings.scheduler_timezone)) - timedelta(hours=settings.fetch_lookback_hours)
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
        chunks = await WeComNotifier().send_daily_report(contents)
        LOGGER.info('Scheduled push finished with %s message chunk(s)', chunks)
    except NotifierError:
        LOGGER.exception('Scheduled push failed')
        raise


def start_scheduler() -> None:
    global _scheduler
    settings = get_settings()

    if not settings.scheduler_enabled:
        LOGGER.info('Scheduler disabled by configuration')
        return

    if AsyncIOScheduler is None or CronTrigger is None:
        LOGGER.warning('APScheduler is not installed; scheduler will not start')
        return

    if _scheduler is not None:
        return

    timezone = get_timezone(settings.scheduler_timezone)
    scheduler = AsyncIOScheduler(timezone=timezone)
    scheduler.add_job(
        run_daily_collection_job,
        CronTrigger(hour=settings.daily_report_hour, minute=settings.daily_report_minute, timezone=timezone),
        id='daily_collection_job',
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    LOGGER.info(
        'Scheduler started: daily job at %02d:%02d %s',
        settings.daily_report_hour,
        settings.daily_report_minute,
        settings.scheduler_timezone,
    )


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
