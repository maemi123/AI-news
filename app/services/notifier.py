from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx

from app.models import ProcessedContent

PUSHPLUS_API = 'https://www.pushplus.plus/send'


class NotifierError(RuntimeError):
    pass


@dataclass(slots=True)
class PodcastAttachment:
    title: str
    audio_url: str
    duration_seconds: int | None = None
    status_message: str | None = None


class PushPlusNotifier:
    def __init__(self, token: str) -> None:
        self.token = token.strip()
        self.max_markdown_bytes = 10000

    async def send_markdown(self, title: str, content: str) -> None:
        if not self.token:
            raise NotifierError('PushPlus token is not configured.')

        payload = {
            'token': self.token,
            'title': title,
            'content': content,
            'template': 'markdown',
            'channel': 'wechat',
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(PUSHPLUS_API, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise NotifierError(f'PushPlus request failed: {exc}') from exc

        if data.get('code') != 200:
            raise NotifierError(data.get('msg') or 'PushPlus returned an error.')

    def format_daily_report(
        self,
        contents: Iterable[ProcessedContent],
        report_date: date,
        podcast: PodcastAttachment | None = None,
    ) -> list[str]:
        items = sorted(
            [item for item in contents if not item.is_duplicate],
            key=lambda item: ((item.importance_stars or 0), self._sort_timestamp(item.published_at or item.collected_at)),
            reverse=True,
        )

        headline_items = [item for item in items if (item.importance_stars or 0) >= 4]
        other_items = [item for item in items if item not in headline_items]

        sections: list[str] = [f'# AI 行业日报 {report_date.isoformat()}']
        if podcast is not None:
            sections.append(self._format_podcast_section(podcast))
        if headline_items:
            sections.append('## 今日头条')
            for item in headline_items:
                sections.append(self._format_item(item, include_reason=True))

        if other_items:
            sections.append('## 其他动态')
            for item in other_items:
                sections.append(self._format_item(item, include_reason=False))

        if len(sections) == 1:
            sections.append('今天还没有生成新的 AI 相关内容。')

        return self._split_markdown_chunks(sections)

    async def send_daily_report(
        self,
        contents: Iterable[ProcessedContent],
        report_date: date | None = None,
        podcast: PodcastAttachment | None = None,
    ) -> tuple[int, list[str]]:
        target_date = report_date or date.today()
        chunks = self.format_daily_report(contents, target_date, podcast=podcast)
        for index, chunk in enumerate(chunks, start=1):
            title = f'AI 行业日报 {target_date.isoformat()}'
            if len(chunks) > 1:
                title = f'{title} ({index}/{len(chunks)})'
            await self.send_markdown(title, chunk)
        return len(chunks), chunks

    def _format_podcast_section(self, podcast: PodcastAttachment) -> str:
        duration = ''
        if podcast.duration_seconds:
            minutes = max(1, round(podcast.duration_seconds / 60))
            duration = f'\n- 预计时长：约 {minutes} 分钟'
        audio_line = f'- 播放链接：{podcast.audio_url}'
        if not str(podcast.audio_url).startswith('http'):
            audio_line = f'- 状态：{podcast.audio_url}'
        status_message = ''
        if podcast.status_message:
            status_message = f'\n- 说明：{podcast.status_message}'
        return (
            '## AI 随身听\n'
            f'- 节目标题：{podcast.title}{duration}\n'
            f'{audio_line}{status_message}'
        )

    def _format_item(self, item: ProcessedContent, *, include_reason: bool) -> str:
        title = item.title or '未命名内容'
        summary = (item.summary or item.content or '暂无摘要').strip()
        stars = '★' * max(1, min(item.importance_stars or 1, 5))
        reason = f'\n- 评分理由：{item.importance_reason}' if include_reason and item.importance_reason else ''
        source = item.source_name or item.platform
        link_part = f'\n- 原文链接：{item.url}' if item.url else ''
        return (
            f'### {title}\n'
            f'- 来源：{source}\n'
            f'- 重要性：{stars}\n'
            f'- 摘要：{summary}{reason}{link_part}'
        )

    def _split_markdown_chunks(self, sections: list[str]) -> list[str]:
        chunks: list[str] = []
        current = ''
        for section in sections:
            candidate = section if not current else f'{current}\n\n{section}'
            if len(candidate.encode('utf-8')) <= self.max_markdown_bytes:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = section
        if current:
            chunks.append(current)
        return chunks

    def _sort_timestamp(self, value: datetime | None) -> float:
        if value is None:
            return 0.0
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
