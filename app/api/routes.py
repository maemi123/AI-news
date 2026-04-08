from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db_session
from app.models import MonitorSource, ProcessedContent
from app.schemas import (
    CategoryCount,
    FetchRunResponse,
    MonitorSourceCreate,
    MonitorSourceRead,
    MonitorSourceToggleResponse,
    MonitorSourceUpdate,
    ProcessVideoResponse,
    ProcessedContentListResponse,
    ProcessedContentRead,
    PushTestResponse,
    SimpleStatusResponse,
    StatsResponse,
)
from app.services.ai_processor import AIProcessorError
from app.services.bilibili_service import BilibiliAPIError
from app.services.content_pipeline import ContentPipelineError, ContentPipelineService
from app.services.fetcher import FetcherService
from app.services.notifier import NotifierError, WeComNotifier
from app.services.video_processor import VideoProcessor, VideoProcessorError
from app.utils.helpers import get_timezone

LOGGER = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


def build_error_detail(message: str, *, stage: str, hint: str) -> dict[str, str]:
    return {
        'message': message,
        'stage': stage,
        'hint': hint,
    }


def get_hint_from_error(message: str, *, stage: str) -> str:
    normalized = message.lower()
    if 'deepseek_api_key' in normalized:
        return 'Please configure DEEPSEEK_API_KEY in .env and restart the service.'
    if 'whisper_api_key' in normalized:
        return 'Please configure WHISPER_API_KEY, WHISPER_BASE_URL, and WHISPER_MODEL in .env.'
    if 'bilibili' in normalized and 'failed' in normalized:
        return 'Check network connectivity, BV id validity, and BILIBILI_SESSDATA if login is required.'
    if 'feedparser' in normalized:
        return 'Install dependencies again with pip install -r requirements.txt.'
    if 'wecom' in normalized:
        return 'Check WECOM_WEBHOOK_URL and make sure the webhook is valid.'
    if stage == 'ai':
        return 'Check DeepSeek configuration and network connectivity, then try again.'
    if stage == 'pipeline':
        return 'Review the upstream content source, network, and API configuration.'
    return 'Check the server logs for more details and verify the .env configuration.'


async def get_monitor_source_or_404(session: AsyncSession, source_id: int) -> MonitorSource:
    source = await session.get(MonitorSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Monitor source not found.')
    return source


@router.get('/health', response_model=SimpleStatusResponse, summary='Health check')
async def health_check() -> SimpleStatusResponse:
    return SimpleStatusResponse()


@router.post(
    '/test/process_video/{bv_id}',
    response_model=ProcessVideoResponse,
    summary='Process one Bilibili video',
)
async def test_process_video(
    bv_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ProcessVideoResponse:
    processor = VideoProcessor()
    try:
        result = await processor.process_video(session, bv_id)
    except BilibiliAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=build_error_detail(
                f'Failed to fetch Bilibili data: {exc}',
                stage='bilibili',
                hint=get_hint_from_error(str(exc), stage='bilibili'),
            ),
        ) from exc
    except AIProcessorError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=build_error_detail(
                f'AI processing failed: {exc}',
                stage='ai',
                hint=get_hint_from_error(str(exc), stage='ai'),
            ),
        ) from exc
    except VideoProcessorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=build_error_detail(
                str(exc),
                stage='pipeline',
                hint=get_hint_from_error(str(exc), stage='pipeline'),
            ),
        ) from exc
    except Exception as exc:
        LOGGER.exception('Unexpected error while processing video %s', bv_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=build_error_detail(
                f'Internal error: {exc}',
                stage='internal',
                hint='Check the server logs and retry.',
            ),
        ) from exc

    return ProcessVideoResponse(**result)


@router.get('/api/monitor-sources', response_model=list[MonitorSourceRead], summary='List monitor sources')
async def list_monitor_sources(session: AsyncSession = Depends(get_db_session)) -> list[MonitorSourceRead]:
    result = await session.execute(
        select(MonitorSource).order_by(MonitorSource.importance_weight.desc(), MonitorSource.created_at.desc())
    )
    return [MonitorSourceRead.model_validate(item) for item in result.scalars().all()]


@router.post(
    '/api/monitor-sources',
    response_model=MonitorSourceRead,
    status_code=status.HTTP_201_CREATED,
    summary='Create monitor source',
)
async def create_monitor_source(
    payload: MonitorSourceCreate,
    session: AsyncSession = Depends(get_db_session),
) -> MonitorSourceRead:
    source = MonitorSource(**payload.model_dump())
    session.add(source)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Monitor source already exists.') from exc

    await session.refresh(source)
    FetcherService.invalidate_cache()
    return MonitorSourceRead.model_validate(source)


@router.put('/api/monitor-sources/{source_id}', response_model=MonitorSourceRead, summary='Update monitor source')
async def update_monitor_source(
    source_id: int,
    payload: MonitorSourceUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> MonitorSourceRead:
    source = await get_monitor_source_or_404(session, source_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(source, field, value)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Monitor source already exists.') from exc

    await session.refresh(source)
    FetcherService.invalidate_cache()
    return MonitorSourceRead.model_validate(source)


@router.put(
    '/api/monitor-sources/{source_id}/toggle',
    response_model=MonitorSourceToggleResponse,
    summary='Toggle monitor source',
)
async def toggle_monitor_source(
    source_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> MonitorSourceToggleResponse:
    source = await get_monitor_source_or_404(session, source_id)
    source.is_active = not source.is_active
    await session.commit()
    await session.refresh(source)
    FetcherService.invalidate_cache()
    return MonitorSourceToggleResponse.model_validate(source)


@router.delete('/api/monitor-sources/{source_id}', response_model=SimpleStatusResponse, summary='Delete monitor source')
async def delete_monitor_source(
    source_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> SimpleStatusResponse:
    source = await get_monitor_source_or_404(session, source_id)
    await session.execute(
        update(ProcessedContent)
        .where(ProcessedContent.source_id == source_id)
        .values(source_id=None)
    )
    await session.delete(source)
    await session.commit()
    FetcherService.invalidate_cache()
    return SimpleStatusResponse(message='deleted')


@router.get('/api/contents', response_model=ProcessedContentListResponse, summary='List processed contents')
async def list_contents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    category: str | None = None,
    min_importance: int | None = Query(default=None, ge=1, le=5),
    platform: str | None = None,
    source_id: int | None = None,
    include_duplicates: bool = False,
    session: AsyncSession = Depends(get_db_session),
) -> ProcessedContentListResponse:
    filters = []
    if category:
        filters.append(ProcessedContent.category == category)
    if min_importance is not None:
        filters.append(ProcessedContent.importance_stars >= min_importance)
    if platform:
        filters.append(ProcessedContent.platform == platform)
    if source_id is not None:
        filters.append(ProcessedContent.source_id == source_id)
    if not include_duplicates:
        filters.append(ProcessedContent.is_duplicate.is_(False))

    total = await session.scalar(select(func.count()).select_from(ProcessedContent).where(*filters)) or 0
    result = await session.execute(
        select(ProcessedContent)
        .where(*filters)
        .order_by(ProcessedContent.published_at.desc(), ProcessedContent.collected_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [ProcessedContentRead.model_validate(item) for item in result.scalars().all()]
    return ProcessedContentListResponse(total=total, page=page, page_size=page_size, items=items)


@router.get('/api/contents/today', response_model=list[ProcessedContentRead], summary='List today contents')
async def list_today_contents(session: AsyncSession = Depends(get_db_session)) -> list[ProcessedContentRead]:
    timezone_name = settings.scheduler_timezone
    start_of_day = datetime.now(get_timezone(timezone_name)).replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = start_of_day.astimezone(timezone.utc)
    result = await session.execute(
        select(ProcessedContent)
        .where(ProcessedContent.collected_at >= cutoff)
        .order_by(ProcessedContent.importance_stars.desc(), ProcessedContent.collected_at.desc())
    )
    return [ProcessedContentRead.model_validate(item) for item in result.scalars().all()]


@router.get('/api/stats', response_model=StatsResponse, summary='Get content stats')
async def get_stats(session: AsyncSession = Depends(get_db_session)) -> StatsResponse:
    timezone_name = settings.scheduler_timezone
    start_of_day = datetime.now(get_timezone(timezone_name)).replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = start_of_day.astimezone(timezone.utc)

    total_contents = await session.scalar(select(func.count()).select_from(ProcessedContent)) or 0
    today_contents = await session.scalar(
        select(func.count()).select_from(ProcessedContent).where(ProcessedContent.collected_at >= cutoff)
    ) or 0
    active_sources = await session.scalar(
        select(func.count()).select_from(MonitorSource).where(MonitorSource.is_active.is_(True))
    ) or 0
    duplicate_contents = await session.scalar(
        select(func.count()).select_from(ProcessedContent).where(ProcessedContent.is_duplicate.is_(True))
    ) or 0

    platform_rows = await session.execute(
        select(ProcessedContent.platform, func.count())
        .group_by(ProcessedContent.platform)
        .order_by(func.count().desc())
    )
    category_rows = await session.execute(
        select(ProcessedContent.category, func.count())
        .where(ProcessedContent.category.is_not(None))
        .group_by(ProcessedContent.category)
        .order_by(func.count().desc())
    )

    return StatsResponse(
        total_contents=total_contents,
        today_contents=today_contents,
        active_sources=active_sources,
        duplicate_contents=duplicate_contents,
        by_platform={str(platform or 'unknown'): int(count) for platform, count in platform_rows.all()},
        by_category={str(category or 'uncategorized'): int(count) for category, count in category_rows.all()},
    )


@router.get('/api/categories', response_model=list[CategoryCount], summary='Get category counts')
async def get_categories(session: AsyncSession = Depends(get_db_session)) -> list[CategoryCount]:
    result = await session.execute(
        select(ProcessedContent.category, func.count())
        .where(ProcessedContent.category.is_not(None))
        .group_by(ProcessedContent.category)
        .order_by(func.count().desc())
    )
    return [CategoryCount(category=str(category), count=int(count)) for category, count in result.all() if category]


@router.post('/api/fetch/now', response_model=FetchRunResponse, summary='Run fetch pipeline now')
async def fetch_now(
    push: bool = Query(default=False),
    force_reload: bool = Query(default=True),
    session: AsyncSession = Depends(get_db_session),
) -> FetchRunResponse:
    try:
        result = await ContentPipelineService().collect_and_process(
            session,
            hours=settings.fetch_lookback_hours,
            force_reload=force_reload,
        )
    except ContentPipelineError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=build_error_detail(
                f'Fetch pipeline failed: {exc}',
                stage='pipeline',
                hint=get_hint_from_error(str(exc), stage='pipeline'),
            ),
        ) from exc

    pushed_messages = 0
    if push:
        push_response = await push_test(session)
        pushed_messages = push_response.message_chunks if push_response.sent else 0

    return result.model_copy(update={'pushed_messages': pushed_messages})


@router.post('/api/push/test', response_model=PushTestResponse, summary='Send test daily report')
async def push_test(session: AsyncSession = Depends(get_db_session)) -> PushTestResponse:
    timezone_name = settings.scheduler_timezone
    start_of_day = datetime.now(get_timezone(timezone_name)).replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = start_of_day.astimezone(timezone.utc)
    result = await session.execute(
        select(ProcessedContent)
        .where(ProcessedContent.collected_at >= cutoff)
        .order_by(ProcessedContent.importance_stars.desc(), ProcessedContent.collected_at.desc())
    )
    contents = list(result.scalars().all())
    if not contents:
        return PushTestResponse(date=start_of_day.date(), items=0, message_chunks=0, sent=False)

    try:
        chunk_count = await WeComNotifier().send_daily_report(contents, report_date=start_of_day.date())
    except NotifierError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=build_error_detail(
                f'Push failed: {exc}',
                stage='notification',
                hint=get_hint_from_error(str(exc), stage='notification'),
            ),
        ) from exc

    return PushTestResponse(date=start_of_day.date(), items=len(contents), message_chunks=chunk_count, sent=True)
