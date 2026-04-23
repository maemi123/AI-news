from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
import re

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
        grouped_items = self._group_report_items(contents)
        items = sorted(
            grouped_items,
            key=lambda item: (
                item['importance_stars'],
                self._sort_timestamp(item['published_at'] or item['collected_at']),
            ),
            reverse=True,
        )

        headline_items = [item for item in items if item['importance_stars'] >= 4]
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

    def _group_report_items(self, contents: Iterable[ProcessedContent]) -> list[dict[str, object]]:
        content_list = list(contents)
        primary_items = [item for item in content_list if not item.is_duplicate]
        duplicates_by_parent: dict[int, list[ProcessedContent]] = {}
        for item in content_list:
            if not item.is_duplicate or item.duplicate_of is None:
                continue
            duplicates_by_parent.setdefault(item.duplicate_of, []).append(item)

        base_items: list[dict[str, object]] = []
        for item in primary_items:
            duplicate_items = duplicates_by_parent.get(item.id, [])
            base_items.extend(self._expand_report_item(item, duplicate_items))
        return self._cluster_similar_report_items(base_items)

    def _expand_report_item(
        self,
        item: ProcessedContent,
        duplicate_items: list[ProcessedContent],
    ) -> list[dict[str, object]]:
        base = {
            'item': item,
            'title': item.title or '未命名内容',
            'summary': (item.summary or item.content or '暂无摘要').strip(),
            'importance_stars': max(1, min(item.importance_stars or 1, 5)),
            'importance_reason': item.importance_reason,
            'published_at': item.published_at,
            'collected_at': item.collected_at,
            'source_names': self._merge_source_names(item, duplicate_items),
            'primary_url': item.url,
            'extra_urls': self._merge_extra_urls(item, duplicate_items),
            'cluster_notes': [],
        }
        split_titles = self._extract_bilibili_subtopics(item)
        if not split_titles:
            return [base]

        expanded: list[dict[str, object]] = []
        parent_title = str(base['title'])
        for title in split_titles:
            child = dict(base)
            child['title'] = title
            child['summary'] = f'来自聚合视频《{parent_title}》：{base["summary"]}'
            child['cluster_notes'] = ['已从 B 站聚合视频中拆分为独立动态']
            expanded.append(child)
        return expanded

    def _extract_bilibili_subtopics(self, item: ProcessedContent) -> list[str]:
        detailed_parts = self._extract_bilibili_timeline_topics(item)
        if detailed_parts:
            return detailed_parts
        return self._split_aggregate_title(item)

    def _extract_bilibili_timeline_topics(self, item: ProcessedContent) -> list[str]:
        if item.platform != 'bilibili':
            return []
        content = (item.content or '').strip()
        if not content:
            return []

        patterns = [
            r'(?:^|\n)\s*\d{1,2}[:：]\d{2}\s*[ \u00a0]*([^\n\r]+)',
            r'(?:^|\n)\s*\d+[·\.\-、]\s*([^\[\n\r]+?)\s*\[\d{1,2}[:：]\d{2}\]',
        ]
        candidates: list[str] = []
        for pattern in patterns:
            matches = re.findall(pattern, content, flags=re.IGNORECASE)
            for match in matches:
                text = str(match).strip(' ：:·.-、，,；;。')
                if not text:
                    continue
                if self._looks_like_ai_topic(text) and not self._is_generic_aggregate_part(text) and text not in candidates:
                    candidates.append(text)
        return candidates[:10]

    def _split_aggregate_title(self, item: ProcessedContent) -> list[str]:
        if item.platform != 'bilibili':
            return []
        title = (item.title or '').strip()
        if not title:
            return []
        parts = [
            part.strip(' ，,、；;。')
            for part in re.split(r'[，,、；;]+', title)
            if part.strip(' ，,、；;。')
        ]
        aiish_parts = [part for part in parts if self._looks_like_ai_topic(part) and not self._is_generic_aggregate_part(part)]
        if len(aiish_parts) < 2:
            return []
        return aiish_parts[:8]

    def _looks_like_ai_topic(self, text: str) -> bool:
        normalized = text.lower()
        keywords = (
            'ai', 'gpt', 'kimi', 'claude', 'gemini', 'deepseek', 'openai', 'chatgpt',
            '模型', '大模型', 'agent', 'copilot', 'cursor', '图像', '图片', '视频生成',
            '推理', '训练', '内测', '发布', '上线', '开源',
        )
        return any(keyword in normalized for keyword in keywords)

    def _is_generic_aggregate_part(self, text: str) -> bool:
        normalized = text.strip().lower()
        generic_phrases = (
            '多家ai公司发布新动态',
            'ai行业早报',
            '多家公司发布ai产品更新',
            'ai产品更新',
            '新动态',
            '行业早报',
        )
        return any(phrase in normalized for phrase in generic_phrases)

    def _cluster_similar_report_items(self, items: list[dict[str, object]]) -> list[dict[str, object]]:
        clusters: dict[str, dict[str, object]] = {}
        ordered_keys: list[str] = []
        for item in items:
            key = self._topic_key(item)
            if key not in clusters:
                clusters[key] = item
                ordered_keys.append(key)
                continue
            clusters[key] = self._merge_report_cluster(clusters[key], item)
        return [clusters[key] for key in ordered_keys]

    def _topic_key(self, item: dict[str, object]) -> str:
        text = f'{item.get("title") or ""}\n{item.get("summary") or ""}'.lower()
        normalized = self._normalize_topic_text(text)
        special_key = self._special_topic_key(normalized)
        if special_key:
            return special_key
        tokens = re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]{2,}', normalized)
        stopwords = {
            '发布', '上线', '推出', '更新', '模型', '能力', '功能', '开源', '重大', '行业',
            '中国', '一个', '这个', '支持', '实现', '产品', '工具',
        }
        meaningful = [token for token in tokens if token not in stopwords and len(token) >= 2]
        return 'generic:' + ':'.join(meaningful[:5])

    def _normalize_topic_text(self, text: str) -> str:
        normalized = text.lower()
        replacements = {
            'gpt-image-2': 'gpt image 2',
            'gpt image 2': 'gpt image 2',
            'gptimage2': 'gpt image 2',
            'chatgpt images 2.0': 'chatgpt images 2',
            'images 2.0': 'images 2',
            'image 2.0': 'image 2',
            '图片生成': '图像生成',
        }
        for old, new in replacements.items():
            normalized = normalized.replace(old, new)
        return normalized

    def _special_topic_key(self, text: str) -> str | None:
        title_text = str(text.split('\n', 1)[0] if '\n' in text else text)
        if ('openai' in title_text or 'chatgpt' in title_text or 'gpt' in title_text) and (
            'image' in title_text or 'images' in title_text or '图像' in title_text or '图片' in title_text
        ):
            return 'topic:openai_images_2'
        if 'kimi' in title_text and ('k2.6' in title_text or 'k2 6' in title_text or 'k 2.6' in title_text):
            return 'topic:kimi_k2_6'
        if 'deepseek' in title_text and ('1m' in text or '100万' in text or '上下文' in text):
            return 'topic:deepseek_1m_context'
        if 'cubesandbox' in title_text or ('腾讯' in title_text and 'sandbox' in text):
            return 'topic:tencent_cubesandbox'
        if 'flashkda' in title_text:
            return 'topic:kimi_flashkda'
        if 'cursor' in title_text and ('spacex' in text or '收购' in text):
            return 'topic:cursor_spacex'
        if 'claude code' in title_text and ('移除' in title_text or '访问' in title_text or 'pro' in text):
            return 'topic:claude_code_pro_access'
        return None

    def _merge_report_cluster(
        self,
        primary: dict[str, object],
        duplicate: dict[str, object],
    ) -> dict[str, object]:
        merged = dict(primary)
        merged['source_names'] = self._dedupe_strings(
            [*self._as_string_list(primary.get('source_names')), *self._as_string_list(duplicate.get('source_names'))]
        )
        merged['extra_urls'] = self._dedupe_strings(
            [
                *self._as_string_list(primary.get('extra_urls')),
                str(duplicate.get('primary_url') or ''),
                *self._as_string_list(duplicate.get('extra_urls')),
            ],
            exclude={str(primary.get('primary_url') or '')},
        )
        merged['importance_stars'] = max(int(primary.get('importance_stars') or 1), int(duplicate.get('importance_stars') or 1))
        merged['published_at'] = self._newer_datetime(primary.get('published_at'), duplicate.get('published_at'))
        merged['collected_at'] = self._newer_datetime(primary.get('collected_at'), duplicate.get('collected_at'))

        duplicate_note = str(duplicate.get('title') or '').strip()
        notes = self._as_string_list(primary.get('cluster_notes'))
        if duplicate_note and duplicate_note != str(primary.get('title') or '').strip():
            notes.append(duplicate_note)
        merged['cluster_notes'] = self._dedupe_strings(notes)
        return merged

    def _as_string_list(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _dedupe_strings(self, values: list[str], *, exclude: set[str] | None = None) -> list[str]:
        exclude = exclude or set()
        results: list[str] = []
        for value in values:
            if not value or value in exclude or value in results:
                continue
            results.append(value)
        return results

    def _newer_datetime(self, left: object, right: object) -> object:
        if not isinstance(left, datetime):
            return right
        if not isinstance(right, datetime):
            return left
        return left if self._sort_timestamp(left) >= self._sort_timestamp(right) else right

    def _merge_source_names(self, item: ProcessedContent, duplicates: list[ProcessedContent]) -> list[str]:
        names: list[str] = []
        for candidate in [item, *duplicates]:
            source_name = (candidate.source_name or candidate.platform or '').strip()
            if source_name and source_name not in names:
                names.append(source_name)
        return names or [item.platform]

    def _merge_extra_urls(self, item: ProcessedContent, duplicates: list[ProcessedContent]) -> list[str]:
        urls: list[str] = []
        primary_url = (item.url or '').strip()
        for candidate in duplicates:
            url = (candidate.url or '').strip()
            if url and url != primary_url and url not in urls:
                urls.append(url)
        return urls

    def _format_item(self, item: dict[str, object], *, include_reason: bool) -> str:
        title = str(item['title'])
        summary = str(item['summary'])
        stars = '★' * int(item['importance_stars'])
        importance_reason = str(item.get('importance_reason') or '').strip()
        reason = f'\n- 评分理由：{importance_reason}' if include_reason and importance_reason else ''
        source_names = item.get('source_names') or []
        source = ' / '.join(str(name) for name in source_names)
        primary_url = str(item.get('primary_url') or '').strip()
        link_part = f'\n- 原文链接：{primary_url}' if primary_url else ''
        extra_urls = item.get('extra_urls') or []
        merged_sources = ''
        if len(source_names) > 1:
            merged_sources = '\n- 去重融合：同一条消息已合并多个来源'
        cluster_notes = item.get('cluster_notes') or []
        related_part = ''
        if cluster_notes:
            related_part = '\n- 相关提法：' + '；'.join(str(note) for note in cluster_notes[:5])
        extra_link_part = ''
        if extra_urls:
            extra_link_part = '\n- 其他来源：' + ' | '.join(str(url) for url in extra_urls)
        return (
            f'### {title}\n'
            f'- 来源：{source}\n'
            f'- 重要性：{stars}\n'
            f'- 摘要：{summary}{reason}{merged_sources}{related_part}{link_part}{extra_link_part}'
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
