from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import ProcessedContent, ScheduledPushState
from app.services.content_pipeline import ContentPipelineService
from app.services.notifier import NotifierError, PodcastAttachment, PushPlusNotifier
from app.services.podcast_service import PodcastBuildResult, PodcastService
from app.services.system_settings import SystemSettingsService
from app.utils.helpers import get_timezone, utcnow

LOGGER = logging.getLogger(__name__)
MAX_DAILY_ATTEMPTS = 3


@dataclass(slots=True)
class ScheduledPushRunResult:
    attempted_push: bool
    pushed_chunks: int = 0
    skipped_reason: str | None = None


class ScheduledPushRunner:
    async def run(self, *, attempt_slot: int = 1) -> ScheduledPushRunResult:
        LOGGER.info('Starting scheduled collection job for slot %s', attempt_slot)
        settings_service = SystemSettingsService()
        podcast_service = PodcastService()

        async with AsyncSessionLocal() as session:
            runtime = await settings_service.get_or_create(session)
            timezone = get_timezone(runtime.scheduler_timezone)
            report_date = datetime.now(timezone).date().isoformat()
            state = await self._get_or_create_state(session, report_date)

            if state.success_at is not None:
                LOGGER.info('Skipping slot %s because report %s was already pushed successfully', attempt_slot, report_date)
                return ScheduledPushRunResult(attempted_push=False, skipped_reason='already_succeeded')

            if state.attempt_count >= MAX_DAILY_ATTEMPTS:
                LOGGER.info('Skipping slot %s because report %s already reached max attempts', attempt_slot, report_date)
                return ScheduledPushRunResult(attempted_push=False, skipped_reason='max_attempts_reached')

            if attempt_slot > state.attempt_count + 1:
                LOGGER.info(
                    'Skipping slot %s because previous slot has not failed yet; current attempts=%s',
                    attempt_slot,
                    state.attempt_count,
                )
                return ScheduledPushRunResult(attempted_push=False, skipped_reason='waiting_for_previous_failure')

            state.attempt_count += 1
            state.last_attempt_slot = attempt_slot
            state.last_error = None
            await session.commit()

            result = await ContentPipelineService().collect_and_process(
                session,
                hours=runtime.fetch_lookback_hours,
            )
            LOGGER.info('Scheduled collection finished: %s', result.model_dump())

            if runtime.push_provider != 'pushplus' or not runtime.pushplus_token:
                state.success_at = utcnow()
                state.last_error = 'PushPlus token is not configured.'
                await session.commit()
                LOGGER.info('Skipping scheduled push because PushPlus token is not configured')
                return ScheduledPushRunResult(attempted_push=False, skipped_reason='pushplus_not_configured')

            cutoff = datetime.now(timezone) - timedelta(hours=runtime.fetch_lookback_hours)
            db_result = await session.execute(
                select(ProcessedContent)
                .where(ProcessedContent.collected_at >= cutoff)
                .order_by(ProcessedContent.importance_stars.desc(), ProcessedContent.collected_at.desc())
            )
            contents = list(db_result.scalars().all())

            if not contents:
                await podcast_service.build_episode(session, report_date=datetime.now(timezone).date(), contents=[])
                state.success_at = utcnow()
                state.last_error = None
                await session.commit()
                LOGGER.info('Skipping scheduled push because no processed contents were collected')
                return ScheduledPushRunResult(attempted_push=False, skipped_reason='no_contents')

            podcast_result = await podcast_service.build_episode(
                session,
                report_date=datetime.now(timezone).date(),
                contents=contents,
            )
            podcast_settings = await podcast_service.get_or_create_settings(session)
            podcast_attachment = self._build_podcast_attachment(
                podcast_result,
                include_audio_link=podcast_settings.podcast_include_audio_link,
            )

            try:
                chunk_count, _ = await PushPlusNotifier(runtime.pushplus_token).send_daily_report(
                    contents,
                    report_date=datetime.now(timezone).date(),
                    podcast=podcast_attachment,
                )
            except NotifierError as exc:
                state.last_error = str(exc)
                await session.commit()
                LOGGER.exception('Scheduled push failed on slot %s', attempt_slot)
                raise

            state.success_at = utcnow()
            state.last_error = None
            await session.commit()
            LOGGER.info('Scheduled push finished with %s message chunk(s)', chunk_count)
            return ScheduledPushRunResult(attempted_push=True, pushed_chunks=chunk_count)

    def _build_podcast_attachment(
        self,
        result: PodcastBuildResult,
        *,
        include_audio_link: bool,
    ) -> PodcastAttachment | None:
        if not include_audio_link:
            return None
        if result.status == 'ready' and result.audio_url and result.title:
            return PodcastAttachment(
                title=result.title,
                audio_url=result.audio_url,
                duration_seconds=result.duration_seconds,
            )
        if result.status == 'failed':
            return PodcastAttachment(
                title='双人 AI 随身听',
                audio_url='生成失败',
                status_message=result.error_message or '本期音频生成失败，已退化为纯文字日报。',
            )
        return None

    async def _get_or_create_state(self, session, report_date: str) -> ScheduledPushState:
        result = await session.execute(
            select(ScheduledPushState).where(ScheduledPushState.report_date == report_date)
        )
        state = result.scalar_one_or_none()
        if state is not None:
            return state

        state = ScheduledPushState(report_date=report_date)
        session.add(state)
        await session.commit()
        await session.refresh(state)
        return state
