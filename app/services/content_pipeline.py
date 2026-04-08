from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MonitorSource, ProcessedContent
from app.schemas import FetchRunResponse
from app.services.ai_processor import AIProcessor, AIProcessorError
from app.services.deduplicator import Deduplicator
from app.services.fetcher import FetcherError, FetcherService
from app.utils.helpers import utcnow

LOGGER = logging.getLogger(__name__)


class ContentPipelineError(RuntimeError):
    pass


class ContentPipelineService:
    def __init__(
        self,
        fetcher: FetcherService | None = None,
        deduplicator: Deduplicator | None = None,
        ai_processor: AIProcessor | None = None,
    ) -> None:
        self.fetcher = fetcher or FetcherService()
        self.deduplicator = deduplicator or Deduplicator()
        self.ai_processor = ai_processor or AIProcessor()

    async def collect_and_process(
        self,
        session: AsyncSession,
        *,
        hours: int = 24,
        force_reload: bool = False,
    ) -> FetchRunResponse:
        try:
            sources = await self.fetcher.get_active_sources(session, force_reload=force_reload)
            raw_contents = await self.fetcher.fetch_all_sources(session, hours=hours, force_reload=force_reload)
        except FetcherError as exc:
            raise ContentPipelineError(str(exc)) from exc

        existing_result = await session.execute(
            select(ProcessedContent).where(
                or_(
                    ProcessedContent.published_at >= datetime.now(timezone.utc) - timedelta(days=7),
                    ProcessedContent.collected_at >= datetime.now(timezone.utc) - timedelta(days=7),
                )
            )
        )
        recent_contents = list(existing_result.scalars().all())

        decisions = self.deduplicator.deduplicate_and_merge(raw_contents, recent_contents=recent_contents)
        new_items = 0
        duplicate_items = 0
        ai_processed_items = 0
        stored_items = 0

        for decision in decisions:
            existing = await self._find_existing_content(session, decision.content.platform, decision.content.original_id)
            if existing is not None:
                continue

            processed = ProcessedContent(
                source_id=decision.content.source_id,
                source_name=decision.content.source_name,
                platform=decision.content.platform,
                original_id=decision.content.original_id,
                title=decision.content.title,
                content=decision.content.content or '',
                url=decision.content.url,
                published_at=decision.content.published_at,
                is_duplicate=decision.is_duplicate,
                duplicate_of=decision.duplicate_of,
                collected_at=utcnow(),
            )

            if decision.is_duplicate:
                duplicate_items += 1
            else:
                try:
                    ai_result = await self.ai_processor.generate_summary(
                        title=decision.content.title,
                        content=decision.content.content or decision.content.title,
                        source_weight=decision.content.importance_weight,
                    )
                except AIProcessorError as exc:
                    LOGGER.exception('AI processing failed for %s', decision.content.title)
                    raise ContentPipelineError(f'AI processing failed: {exc}') from exc

                processed.summary = ai_result.get('summary') or ''
                processed.category = ai_result.get('category') or 'other'
                processed.importance_stars = ai_result.get('importance_stars') or 1
                processed.importance_reason = ai_result.get('importance_reason') or None
                processed.key_entities = ai_result.get('key_entities') or []
                processed.tags = ai_result.get('tags') or []
                processed.processed_at = utcnow()
                new_items += 1
                ai_processed_items += 1

            session.add(processed)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                raise ContentPipelineError('Database write failed because of a duplicate content record.')
            stored_items += 1

        now = utcnow()
        for source in sources:
            source.last_fetched_at = now
        await session.commit()

        return FetchRunResponse(
            sources_checked=len(sources),
            fetched_items=len(raw_contents),
            new_items=new_items,
            duplicate_items=duplicate_items,
            ai_processed_items=ai_processed_items,
            stored_items=stored_items,
        )

    async def _find_existing_content(
        self,
        session: AsyncSession,
        platform: str,
        original_id: str,
    ) -> ProcessedContent | None:
        result = await session.execute(
            select(ProcessedContent).where(
                ProcessedContent.platform == platform,
                ProcessedContent.original_id == original_id,
            )
        )
        return result.scalar_one_or_none()
