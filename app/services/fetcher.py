from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MonitorSource
from app.schemas import RawContent
from app.services.bilibili_service import BilibiliAPIError, BilibiliService

LOGGER = logging.getLogger(__name__)
CACHE_TTL = timedelta(minutes=5)


class FetcherError(RuntimeError):
    pass


@dataclass
class SourceFetchResult:
    source: MonitorSource
    items: list[RawContent]


class FetcherService:
    _cache_sources: list[MonitorSource] = []
    _cache_expire_at: datetime | None = None

    def __init__(self, bilibili_service: BilibiliService | None = None) -> None:
        self.bilibili_service = bilibili_service or BilibiliService()

    @classmethod
    def invalidate_cache(cls) -> None:
        cls._cache_sources = []
        cls._cache_expire_at = None

    async def get_active_sources(self, session: AsyncSession, force_reload: bool = False) -> list[MonitorSource]:
        now = datetime.now(timezone.utc)
        if not force_reload and self._cache_expire_at and now < self._cache_expire_at and self._cache_sources:
            return list(self._cache_sources)

        result = await session.execute(
            select(MonitorSource)
            .where(MonitorSource.is_active.is_(True))
            .order_by(MonitorSource.importance_weight.desc(), MonitorSource.id.desc())
        )
        sources = list(result.scalars().all())
        type(self)._cache_sources = sources
        type(self)._cache_expire_at = now + CACHE_TTL
        return sources

    async def fetch_source_content(self, source: MonitorSource, *, hours: int = 24) -> list[RawContent]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        platform = source.platform.lower().strip()

        if platform == 'bilibili':
            return await self.fetch_bilibili_user(source, cutoff=cutoff)
        if platform == 'weibo':
            return await self.fetch_weibo_user(source, cutoff=cutoff)
        if platform in {'twitter', 'x'}:
            return await self.fetch_twitter_user(source, cutoff=cutoff)

        raise FetcherError(f'Unsupported platform: {source.platform}')

    async def fetch_all_sources(
        self,
        session: AsyncSession,
        *,
        hours: int = 24,
        force_reload: bool = False,
    ) -> list[RawContent]:
        sources = await self.get_active_sources(session, force_reload=force_reload)
        contents: list[RawContent] = []

        for source in sources:
            try:
                items = await self.fetch_source_content(source, hours=hours)
            except Exception as exc:
                LOGGER.exception('Failed to fetch source %s (%s)', source.name, source.platform)
                raise FetcherError(f'Failed to fetch source {source.name}: {exc}') from exc
            contents.extend(items)

        return contents

    async def fetch_bilibili_user(self, source: MonitorSource, *, cutoff: datetime | None = None) -> list[RawContent]:
        if source.rss_url:
            return await self._fetch_rss(source, cutoff=cutoff)

        try:
            videos = await self.bilibili_service.get_user_videos(source.platform_id)
        except BilibiliAPIError as exc:
            raise FetcherError(str(exc)) from exc

        return [
            RawContent(
                source_id=source.id,
                source_name=source.name,
                source_category=source.category,
                importance_weight=source.importance_weight,
                platform='bilibili',
                original_id=item['original_id'],
                title=item['title'],
                content=item.get('content') or '',
                url=item.get('url'),
                published_at=item.get('published_at'),
                author=item.get('author'),
                metadata={'source_url': source.source_url or '', 'rss_url': source.rss_url or ''},
            )
            for item in videos
            if self._is_after_cutoff(item.get('published_at'), cutoff)
        ]

    async def fetch_weibo_user(self, source: MonitorSource, *, cutoff: datetime | None = None) -> list[RawContent]:
        if not source.rss_url:
            raise FetcherError(f'RSS url is required for platform {source.platform}')
        return await self._fetch_rss(source, cutoff=cutoff)

    async def fetch_twitter_user(self, source: MonitorSource, *, cutoff: datetime | None = None) -> list[RawContent]:
        if not source.rss_url:
            raise FetcherError(f'RSS url is required for platform {source.platform}')
        return await self._fetch_rss(source, cutoff=cutoff)

    async def _fetch_rss(self, source: MonitorSource, *, cutoff: datetime | None = None) -> list[RawContent]:
        try:
            import feedparser
        except ImportError as exc:
            raise FetcherError('feedparser is not installed. Run pip install -r requirements.txt.') from exc

        if not source.rss_url:
            raise FetcherError('rss_url is required for RSS fetching')

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(source.rss_url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise FetcherError(f'Failed to fetch RSS feed: {exc}') from exc

        parsed = feedparser.parse(response.text)
        items: list[RawContent] = []
        for entry in parsed.entries:
            published_at = self._parse_entry_datetime(entry)
            if not self._is_after_cutoff(published_at, cutoff):
                continue

            original_id = self._entry_original_id(entry)
            title = str(getattr(entry, 'title', '') or '').strip()
            summary = str(getattr(entry, 'summary', '') or getattr(entry, 'description', '') or '').strip()
            link = str(getattr(entry, 'link', '') or '').strip() or None
            author = str(getattr(entry, 'author', '') or '').strip() or source.name

            if not original_id or not title:
                continue

            items.append(
                RawContent(
                    source_id=source.id,
                    source_name=source.name,
                    source_category=source.category,
                    importance_weight=source.importance_weight,
                    platform=source.platform.lower(),
                    original_id=original_id,
                    title=title,
                    content=summary,
                    url=link,
                    published_at=published_at,
                    author=author,
                    metadata={'rss_url': source.rss_url, 'source_url': source.source_url or ''},
                )
            )
        return items

    def _is_after_cutoff(self, published_at: datetime | None, cutoff: datetime | None) -> bool:
        if cutoff is None or published_at is None:
            return True
        return published_at >= cutoff

    def _entry_original_id(self, entry: Any) -> str:
        candidates = [
            str(getattr(entry, 'id', '') or '').strip(),
            str(getattr(entry, 'guid', '') or '').strip(),
            str(getattr(entry, 'link', '') or '').strip(),
            str(getattr(entry, 'title', '') or '').strip(),
        ]
        for candidate in candidates:
            if candidate:
                return candidate
        return ''

    def _parse_entry_datetime(self, entry: Any) -> datetime | None:
        structured_time = getattr(entry, 'published_parsed', None) or getattr(entry, 'updated_parsed', None)
        if structured_time is not None:
            return datetime(*structured_time[:6], tzinfo=timezone.utc)

        for attr in ('published', 'updated', 'created'):
            value = getattr(entry, attr, None)
            if not value:
                continue
            try:
                parsed = parsedate_to_datetime(str(value))
            except (TypeError, ValueError):
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        return None
