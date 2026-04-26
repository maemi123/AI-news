from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import re
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
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
    error: str | None = None


class FetcherService:
    _cache_sources: list[MonitorSource] = []
    _cache_expire_at: datetime | None = None

    def __init__(self, bilibili_service: BilibiliService | None = None) -> None:
        self.settings = get_settings()
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

    async def fetch_source_results(
        self,
        sources: list[MonitorSource],
        *,
        hours: int = 24,
    ) -> list[SourceFetchResult]:
        results: list[SourceFetchResult] = []
        for source in sources:
            try:
                items = await self.fetch_source_content(source, hours=hours)
            except Exception as exc:
                LOGGER.warning(
                    'Skipping source %s (%s) because fetching failed: %s',
                    source.name,
                    source.platform,
                    exc,
                )
                results.append(SourceFetchResult(source=source, items=[], error=str(exc)))
                continue
            results.append(SourceFetchResult(source=source, items=items))
        return results

    async def fetch_all_sources(
        self,
        session: AsyncSession,
        *,
        hours: int = 24,
        force_reload: bool = False,
    ) -> list[RawContent]:
        sources = await self.get_active_sources(session, force_reload=force_reload)
        results = await self.fetch_source_results(sources, hours=hours)
        contents = [item for result in results for item in result.items]
        failed_sources = [
            f'{result.source.name} ({result.source.platform}): {result.error}'
            for result in results
            if result.error
        ]

        if failed_sources:
            LOGGER.warning('Fetch completed with %s failed source(s)', len(failed_sources))
            for message in failed_sources[:10]:
                LOGGER.warning('Source failure: %s', message)
        if not contents and failed_sources:
            raise FetcherError(
                'All monitored sources failed to fetch. First failures: '
                + '; '.join(failed_sources[:3])
            )

        return contents

    async def fetch_bilibili_user(self, source: MonitorSource, *, cutoff: datetime | None = None) -> list[RawContent]:
        if source.rss_url:
            return await self._fetch_rss(source, cutoff=cutoff)

        try:
            videos = await self.bilibili_service.get_user_videos(source.platform_id)
        except BilibiliAPIError as exc:
            rsshub_bilibili_url = self._resolve_bilibili_rsshub_url(source)
            if rsshub_bilibili_url:
                LOGGER.warning(
                    'Falling back to RSSHub for bilibili source %s (%s) after direct fetch failed: %s',
                    source.name,
                    source.platform_id,
                    exc,
                )
                return await self._fetch_rss(source, cutoff=cutoff, rss_url=rsshub_bilibili_url)
            raise FetcherError(str(exc)) from exc

        results: list[RawContent] = []
        for item in videos:
            if not self._is_after_cutoff(item.get('published_at'), cutoff):
                continue
            results.extend(self._expand_bilibili_video_into_contents(source, item))
        return results

    def _expand_bilibili_video_into_contents(
        self,
        source: MonitorSource,
        item: dict[str, Any],
    ) -> list[RawContent]:
        base_metadata = {'source_url': source.source_url or '', 'rss_url': source.rss_url or ''}
        timeline_topics = self._extract_bilibili_timeline_topics(item)
        if not timeline_topics:
            return [
                RawContent(
                    source_id=source.id,
                    source_name=source.name,
                    source_category=source.category,
                    importance_weight=source.importance_weight,
                    platform='bilibili',
                    original_id=item['original_id'],
                    title=item['title'],
                    content=item.get('transcript_content') or item.get('content') or '',
                    url=item.get('url'),
                    published_at=item.get('published_at'),
                    author=item.get('author'),
                    metadata={
                        **base_metadata,
                        'bilibili_transcript_source': item.get('transcript_source') or 'none',
                    },
                )
            ]

        expanded: list[RawContent] = []
        parent_title = str(item.get('title') or '').strip()
        parent_content = str(item.get('content') or '').strip()
        transcript_content = str(item.get('transcript_content') or '').strip()
        transcript_segments = item.get('transcript_segments') or []
        transcript_source = str(item.get('transcript_source') or 'none').strip() or 'none'
        for index, topic in enumerate(timeline_topics, start=1):
            next_topic = timeline_topics[index] if index < len(timeline_topics) else None
            topic_excerpt = self._build_bilibili_topic_excerpt(
                transcript_segments=transcript_segments,
                start_seconds=topic.get('timestamp_seconds'),
                end_seconds=(next_topic or {}).get('timestamp_seconds'),
                fallback_text=transcript_content or parent_content,
                topic_title=topic['title'],
            )
            expanded.append(
                RawContent(
                    source_id=source.id,
                    source_name=source.name,
                    source_category=source.category,
                    importance_weight=source.importance_weight,
                    platform='bilibili',
                    original_id=f"{item['original_id']}#topic#{index}",
                    title=topic['title'],
                    content=self._build_bilibili_topic_content(
                        parent_title=parent_title,
                        parent_content=parent_content,
                        transcript_source=transcript_source,
                        topic_title=topic['title'],
                        timestamp=topic.get('timestamp'),
                        topic_excerpt=topic_excerpt,
                    ),
                    url=item.get('url'),
                    published_at=item.get('published_at'),
                    author=item.get('author'),
                    metadata={
                        **base_metadata,
                        'bilibili_parent_title': parent_title,
                        'bilibili_topic_timestamp': topic.get('timestamp') or '',
                        'bilibili_topic_timestamp_seconds': topic.get('timestamp_seconds'),
                        'bilibili_topic_index': index,
                        'bilibili_transcript_source': transcript_source,
                    },
                )
            )
        return expanded

    def _extract_bilibili_timeline_topics(self, item: dict[str, Any]) -> list[dict[str, str]]:
        content = str(item.get('content') or '').strip()
        if not content:
            return []

        patterns = [
            r'(?:^|\n)\s*(?P<ts>\d{1,2}[:：]\d{2})\s*[ \u00a0]*(?P<title>[^\n\r]+)',
            r'(?:^|\n)\s*\d+[·\.\-、]\s*(?P<title>[^\[\n\r]+?)\s*\[(?P<ts>\d{1,2}[:：]\d{2})\]',
        ]
        topics: list[dict[str, str]] = []
        for pattern in patterns:
            for match in re.finditer(pattern, content, flags=re.IGNORECASE):
                title = str(match.group('title') or '').strip(' ：:·.-、，,；;。')
                timestamp = str(match.group('ts') or '').replace('：', ':').strip()
                if not title:
                    continue
                if self._is_generic_bilibili_topic(title):
                    continue
                if not self._looks_like_ai_topic(title):
                    continue
                if any(existing['title'] == title for existing in topics):
                    continue
                topics.append(
                    {
                        'title': title,
                        'timestamp': timestamp,
                        'timestamp_seconds': self._parse_bilibili_timestamp_seconds(timestamp),
                    }
                )
        return topics[:12]

    def _build_bilibili_topic_content(
        self,
        *,
        parent_title: str,
        parent_content: str,
        transcript_source: str,
        topic_title: str,
        timestamp: str | None,
        topic_excerpt: str,
    ) -> str:
        headline = f'视频标题：{parent_title}' if parent_title else ''
        timeline = f'视频时间点：{timestamp}' if timestamp else ''
        topic = f'本条新闻点：{topic_title}'
        transcript_label = '对应字幕/转写内容'
        if transcript_source == 'subtitle':
            transcript_label = '对应字幕内容'
        elif transcript_source == 'whisper':
            transcript_label = '对应音频转写内容'
        detail = f'{transcript_label}：{topic_excerpt}' if topic_excerpt else ''
        fallback = f'视频简介与时间轴：{parent_content}' if parent_content and not topic_excerpt else ''
        parts = [part for part in [headline, timeline, topic, detail, fallback] if part]
        return '\n'.join(parts)

    def _build_bilibili_topic_excerpt(
        self,
        *,
        transcript_segments: list[Any],
        start_seconds: int | None,
        end_seconds: int | None,
        fallback_text: str,
        topic_title: str,
    ) -> str:
        excerpt = self._slice_bilibili_transcript_segments(
            transcript_segments=transcript_segments,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )
        if excerpt:
            return excerpt

        fallback_excerpt = self._slice_bilibili_fallback_text(
            fallback_text=fallback_text,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            topic_title=topic_title,
        )
        return fallback_excerpt

    def _slice_bilibili_transcript_segments(
        self,
        *,
        transcript_segments: list[Any],
        start_seconds: int | None,
        end_seconds: int | None,
    ) -> str:
        if not transcript_segments:
            return ''

        matched_texts: list[str] = []
        upper_bound = end_seconds if end_seconds is not None else 10**9
        for segment in transcript_segments:
            segment_start = self._segment_seconds(segment, 'start_seconds', 'start')
            segment_end = self._segment_seconds(segment, 'end_seconds', 'end')
            segment_text = self._segment_text(segment)
            if not segment_text:
                continue
            if start_seconds is not None and segment_end < max(start_seconds - 2, 0):
                continue
            if end_seconds is not None and segment_start > upper_bound + 2:
                continue
            matched_texts.append(segment_text)

        excerpt = ' '.join(matched_texts).strip()
        excerpt = re.sub(r'\s+', ' ', excerpt)
        return excerpt[:1200].strip()

    def _slice_bilibili_fallback_text(
        self,
        *,
        fallback_text: str,
        start_seconds: int | None,
        end_seconds: int | None,
        topic_title: str,
    ) -> str:
        normalized = str(fallback_text or '').strip()
        if not normalized:
            return topic_title

        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        timestamp_pattern = re.compile(r'(?P<ts>\d{1,2}[:：]\d{2})')
        matched_lines: list[str] = []
        upper_bound = end_seconds if end_seconds is not None else 10**9

        for line in lines:
            match = timestamp_pattern.search(line)
            if not match:
                continue
            line_seconds = self._parse_bilibili_timestamp_seconds(match.group('ts'))
            if line_seconds is None:
                continue
            if start_seconds is not None and line_seconds < max(start_seconds - 2, 0):
                continue
            if end_seconds is not None and line_seconds > upper_bound + 2:
                continue
            matched_lines.append(line)

        if not matched_lines:
            matched_lines = [line for line in lines if topic_title in line][:3]

        excerpt = ' '.join(matched_lines).strip()
        excerpt = re.sub(r'\s+', ' ', excerpt)
        return excerpt[:800].strip() or topic_title

    def _segment_seconds(self, segment: Any, primary_key: str, fallback_key: str) -> float:
        if hasattr(segment, primary_key):
            return float(getattr(segment, primary_key) or 0.0)
        if isinstance(segment, dict):
            return float(segment.get(primary_key) or segment.get(fallback_key) or 0.0)
        return 0.0

    def _segment_text(self, segment: Any) -> str:
        if hasattr(segment, 'text'):
            return str(getattr(segment, 'text') or '').strip()
        if isinstance(segment, dict):
            return str(segment.get('text') or '').strip()
        return ''

    def _parse_bilibili_timestamp_seconds(self, timestamp: str | None) -> int | None:
        normalized = str(timestamp or '').replace('：', ':').strip()
        if not normalized:
            return None
        match = re.fullmatch(r'(?P<minutes>\d{1,2}):(?P<seconds>\d{2})', normalized)
        if not match:
            return None
        minutes = int(match.group('minutes'))
        seconds = int(match.group('seconds'))
        return minutes * 60 + seconds

    def _looks_like_ai_topic(self, text: str) -> bool:
        normalized = text.lower()
        keywords = (
            'ai', 'gpt', 'kimi', 'claude', 'gemini', 'deepseek', 'openai', 'chatgpt',
            'copilot', 'cursor', 'agent', '智能体', '模型', '大模型', '开源', '图像',
            '图片', '代码', '编程', 'api', '研究', '自动驾驶', '机器人',
        )
        return any(keyword in normalized for keyword in keywords)

    def _is_generic_bilibili_topic(self, text: str) -> bool:
        normalized = text.strip().lower()
        generic_phrases = (
            '多家ai公司发布新动态',
            '多家公司发布ai产品更新',
            'ai行业早报',
            '新动态',
            '产品更新',
            '行业早报',
        )
        return any(phrase in normalized for phrase in generic_phrases)

    async def fetch_weibo_user(self, source: MonitorSource, *, cutoff: datetime | None = None) -> list[RawContent]:
        if not source.platform_id.strip().isdigit():
            raise FetcherError(
                'Weibo monitoring requires a numeric uid. Current platform_id is not a uid, '
                'so this source cannot be fetched reliably.'
            )
        if self.settings.effective_weibo_cookies:
            try:
                return await self._fetch_weibo_direct(source, cutoff=cutoff)
            except FetcherError as exc:
                LOGGER.warning(
                    'Direct Weibo fetch failed for %s (%s), falling back to RSS: %s',
                    source.name,
                    source.platform_id,
                    exc,
                )
        rss_url = self._resolve_rss_url(source)
        if not rss_url:
            raise FetcherError(f'RSS url is required for platform {source.platform}')
        return await self._fetch_rss(source, cutoff=cutoff, rss_url=rss_url)

    async def fetch_twitter_user(self, source: MonitorSource, *, cutoff: datetime | None = None) -> list[RawContent]:
        rss_url = self._resolve_rss_url(source)
        if not rss_url:
            raise FetcherError(f'RSS url is required for platform {source.platform}')
        return await self._fetch_rss(source, cutoff=cutoff, rss_url=rss_url)

    async def _fetch_rss(
        self,
        source: MonitorSource,
        *,
        cutoff: datetime | None = None,
        rss_url: str | None = None,
    ) -> list[RawContent]:
        try:
            import feedparser
        except ImportError as exc:
            raise FetcherError('feedparser is not installed. Run pip install -r requirements.txt.') from exc

        target_rss_url = (rss_url or source.rss_url or '').strip()
        if not target_rss_url:
            raise FetcherError('rss_url is required for RSS fetching')

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(
                    target_rss_url,
                    headers={'User-Agent': 'Mozilla/5.0'},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise FetcherError(f'Failed to fetch RSS feed: {exc}') from exc

        parsed = feedparser.parse(response.text)
        if self._is_xcancel_blocked(parsed, target_rss_url):
            raise FetcherError(
                'The configured X/Twitter RSS source is blocked by XCancel whitelist protection.'
            )
        items: list[RawContent] = []
        for entry in parsed.entries:
            published_at = self._parse_entry_datetime(entry)
            if not self._is_after_cutoff(published_at, cutoff):
                continue

            original_id = self._entry_original_id(entry)
            title = self._clean_rss_text(getattr(entry, 'title', '') or '')
            summary = self._clean_rss_text(getattr(entry, 'summary', '') or getattr(entry, 'description', '') or '')
            link = str(getattr(entry, 'link', '') or '').strip() or None
            author = self._clean_rss_text(getattr(entry, 'author', '') or '') or source.name

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
                    metadata={'rss_url': target_rss_url, 'source_url': source.source_url or ''},
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

    def _resolve_rss_url(self, source: MonitorSource) -> str | None:
        platform = source.platform.lower().strip()
        rss_url = (source.rss_url or '').strip()
        rsshub_base_url = self.settings.effective_rsshub_base_url
        platform_id = quote(source.platform_id.strip())

        if platform == 'weibo':
            if rsshub_base_url:
                return f'{rsshub_base_url}/weibo/user/{platform_id}'
            return rss_url or None

        if platform in {'twitter', 'x'}:
            if rsshub_base_url:
                return f'{rsshub_base_url}/twitter/user/{platform_id}'
            if rss_url and 'rsshub.app/twitter/' not in rss_url:
                return rss_url
            return f'https://rss.xcancel.com/{platform_id}/rss'
        return rss_url or None

    def _resolve_bilibili_rsshub_url(self, source: MonitorSource) -> str | None:
        rsshub_base_url = self.settings.effective_rsshub_base_url
        if not rsshub_base_url:
            return None
        platform_id = quote(source.platform_id.strip())
        if not platform_id:
            return None
        return f'{rsshub_base_url}/bilibili/user/video/{platform_id}'

    async def _fetch_weibo_direct(
        self,
        source: MonitorSource,
        *,
        cutoff: datetime | None = None,
    ) -> list[RawContent]:
        uid = source.platform_id.strip()
        if not uid:
            raise FetcherError('Weibo uid is required')

        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/123.0.0.0 Safari/537.36'
            ),
            'Referer': f'https://weibo.com/u/{uid}',
            'Cookie': self.settings.effective_weibo_cookies,
            'X-Requested-With': 'XMLHttpRequest',
        }
        url = f'https://weibo.com/ajax/statuses/mymblog?uid={uid}&page=1&feature=0'

        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                if 'login.sina.com.cn' in str(response.url):
                    raise FetcherError('Weibo cookies expired or invalid; request was redirected to Sina login.')
                payload = response.json()
        except httpx.HTTPError as exc:
            raise FetcherError(f'Failed to fetch Weibo API: {exc}') from exc
        except ValueError as exc:
            raise FetcherError('Weibo API returned invalid JSON') from exc

        items = ((payload.get('data') or {}).get('list')) or []
        if not isinstance(items, list):
            raise FetcherError('Weibo API returned an unexpected payload')

        results: list[RawContent] = []
        for item in items:
            if not isinstance(item, dict):
                continue

            published_at = self._parse_weibo_datetime(item.get('created_at'))
            if not self._is_after_cutoff(published_at, cutoff):
                continue

            text = self._clean_rss_text(item.get('text_raw') or item.get('text') or '')
            if not text:
                continue

            title = text[:80]
            mblog_id = str(item.get('mblogid') or item.get('idstr') or item.get('id') or '').strip()
            original_id = str(item.get('idstr') or item.get('id') or mblog_id).strip()
            url_value = f'https://weibo.com/{uid}/{mblog_id}' if mblog_id else None
            user = item.get('user') or {}
            author = self._clean_rss_text(user.get('screen_name') or '') or source.name

            if not original_id:
                continue

            results.append(
                RawContent(
                    source_id=source.id,
                    source_name=source.name,
                    source_category=source.category,
                    importance_weight=source.importance_weight,
                    platform='weibo',
                    original_id=original_id,
                    title=title,
                    content=text,
                    url=url_value,
                    published_at=published_at,
                    author=author,
                    metadata={'source_url': source.source_url or '', 'weibo_uid': uid},
                )
            )
        return results

    def _parse_weibo_datetime(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.strptime(str(value), '%a %b %d %H:%M:%S %z %Y')
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc)

    def _clean_rss_text(self, value: str) -> str:
        text = html.unescape(str(value or '').strip())
        if not text:
            return ''
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return text
        return BeautifulSoup(text, 'html.parser').get_text(' ', strip=True)

    def _is_xcancel_blocked(self, parsed: Any, rss_url: str) -> bool:
        if 'rss.xcancel.com' not in rss_url:
            return False
        entries = list(getattr(parsed, 'entries', []) or [])
        if len(entries) != 1:
            return False
        entry = entries[0]
        title = str(getattr(entry, 'title', '') or '').lower()
        summary = str(getattr(entry, 'summary', '') or '').lower()
        return 'not yet whitelist' in title or 'not yet whitelist' in summary

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
