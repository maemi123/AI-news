from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date

import httpx

from app.config import get_settings
from app.models import ProcessedContent

LOGGER = logging.getLogger(__name__)


class NotifierError(RuntimeError):
    pass


class WeComNotifier:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.max_markdown_bytes = 3500

    async def send_markdown(self, content: str) -> None:
        if not self.settings.wecom_webhook_url:
            raise NotifierError('WECOM_WEBHOOK_URL is not configured.')

        payload = {
            'msgtype': 'markdown',
            'markdown': {'content': content},
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(self.settings.wecom_webhook_url, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise NotifierError(f'WeCom request failed: {exc}') from exc

        if data.get('errcode', 0) != 0:
            raise NotifierError(data.get('errmsg') or 'WeCom webhook returned an error.')

    def format_daily_report(self, contents: Iterable[ProcessedContent], report_date: date) -> list[str]:
        items = sorted(
            list(contents),
            key=lambda item: (
                item.importance_stars or 0,
                item.published_at or item.collected_at,
            ),
            reverse=True,
        )

        headline_items = [item for item in items if (item.importance_stars or 0) >= 4 and not item.is_duplicate]
        other_items = [item for item in items if item not in headline_items and not item.is_duplicate]

        sections: list[str] = [f'# AI Daily Report {report_date.isoformat()}']
        if headline_items:
            sections.append('## Headlines')
            for item in headline_items:
                sections.append(self._format_item(item, include_reason=True))

        if other_items:
            sections.append('## Other Updates')
            for item in other_items:
                sections.append(self._format_item(item, include_reason=False))

        if len(sections) == 1:
            sections.append('No new content for the selected date.')

        return self._split_markdown_chunks(sections)

    async def send_daily_report(self, contents: Iterable[ProcessedContent], report_date: date | None = None) -> int:
        target_date = report_date or date.today()
        chunks = self.format_daily_report(contents, target_date)
        for chunk in chunks:
            await self.send_markdown(chunk)
        return len(chunks)

    def _format_item(self, item: ProcessedContent, *, include_reason: bool) -> str:
        title = item.title or 'Untitled'
        summary = item.summary or item.content or 'No summary available.'
        link = item.url or ''
        stars = '★' * max(1, min(item.importance_stars or 1, 5))
        reason = f'\nReason: {item.importance_reason}' if include_reason and item.importance_reason else ''
        source = item.source_name or item.platform
        link_part = f'\nLink: {link}' if link else ''
        return f'### {title}\nSource: {source} | Importance: {stars}\n{summary}{reason}{link_part}'

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
