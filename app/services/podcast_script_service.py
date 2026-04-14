from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import httpx

from app.config import get_settings
from app.models import ProcessedContent

LOGGER = logging.getLogger(__name__)


class PodcastScriptError(RuntimeError):
    pass


@dataclass(slots=True)
class PodcastScript:
    title: str
    intro: str
    outro: str
    dialogue_lines: list[dict[str, str]]
    estimated_minutes: int

    @property
    def script_text(self) -> str:
        sections = [self.title, '']
        for line in self.dialogue_lines:
            speaker = '男主持' if line.get('speaker') == 'host_a' else '女主持'
            sections.append(f'{speaker}：{line.get("text", "").strip()}')
        return '\n'.join(sections).strip()


class PodcastScriptService:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def generate_dialogue_script(self, *, contents: list[ProcessedContent], report_date: date) -> PodcastScript:
        if not self.settings.deepseek_api_key:
            raise PodcastScriptError('DEEPSEEK_API_KEY is not configured.')

        if not contents:
            raise PodcastScriptError('No contents available for podcast script generation.')

        prompt = self._build_prompt(contents=contents, report_date=report_date)
        payload = {
            'model': self.settings.deepseek_model,
            'temperature': 0.4,
            'response_format': {'type': 'json_object'},
            'messages': [
                {
                    'role': 'system',
                    'content': 'You are a Chinese AI news podcast script writer. Reply with a single JSON object only.',
                },
                {'role': 'user', 'content': prompt},
            ],
        }
        headers = {
            'Authorization': f'Bearer {self.settings.deepseek_api_key}',
            'Content-Type': 'application/json',
        }

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(
                    self.settings.deepseek_chat_completions_url,
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            LOGGER.exception('Podcast script model returned an error: %s', detail)
            raise PodcastScriptError(f'Podcast script model returned an error: {detail}') from exc
        except httpx.HTTPError as exc:
            LOGGER.exception('Podcast script request failed')
            raise PodcastScriptError(f'Podcast script request failed: {exc}') from exc

        try:
            content_text = data['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError) as exc:
            raise PodcastScriptError('Podcast script response structure is invalid.') from exc

        parsed = self._parse_json(content_text)
        return self._normalize_result(parsed, report_date=report_date)

    def _build_prompt(self, *, contents: list[ProcessedContent], report_date: date) -> str:
        top_items = sorted(
            [item for item in contents if not item.is_duplicate],
            key=lambda item: ((item.importance_stars or 0), self._sort_timestamp(item.published_at or item.collected_at)),
            reverse=True,
        )[:8]
        serialized_items = []
        for item in top_items:
            serialized_items.append(
                {
                    'title': item.title,
                    'summary': item.summary or item.content or '',
                    'importance_stars': item.importance_stars,
                    'importance_reason': item.importance_reason or '',
                    'source': item.source_name or item.platform,
                    'url': item.url or '',
                }
            )
        items_json = json.dumps(serialized_items, ensure_ascii=False, indent=2)
        return f'''请基于下面的 AI 时讯列表，生成一份适合“男主持 + 女主持”的中文双人播客对话稿，只返回 JSON。

节目日期：{report_date.isoformat()}
节目定位：AI 日报随身听，双人搭档聊天感，像两个熟悉 AI 行业的人在通勤路上把重点新闻讲给你听。
节目要求：
- 只基于给定新闻，不要添加原文里没有的事实。
- 节目总时长目标 5-8 分钟。
- 语气自然、口语化，有一点真人互动感，少一点新闻联播式播报味。
- 允许适度出现“我刚看到这个也有点意外”“这个点我觉得值得注意”这类轻互动表达，但不要油腻，不要夸张。
- 开场简单寒暄一句即可，不要官腔。
- 主持人轮流说话，避免单边长段输出；每轮尽量 1-3 句。
- 每条新闻优先说清楚“发生了什么”和“为什么值得听”。
- 男主持更像负责抛出话题和串联节奏，女主持更像负责补充背景和点出影响，但都不要固定死板。
- 允许两位主持人之间有简短接话、追问、补充，而不是机械轮读摘要。
- 不写夸张段子，不写虚构故事，不写无根据推断。
- 统一使用简体中文。

输出 JSON 结构：
{{
  "title": "今日节目标题",
  "intro": "一句开场简介",
  "outro": "一句收尾",
  "estimated_minutes": 6,
  "dialogue_lines": [
    {{"speaker": "host_a", "text": "男主持台词"}},
    {{"speaker": "host_b", "text": "女主持台词"}}
  ]
}}

可用新闻：
{items_json}
'''

    def _sort_timestamp(self, value: datetime | None) -> float:
        if value is None:
            return 0.0
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()

    def _parse_json(self, content_text: str) -> dict[str, Any]:
        cleaned = content_text.strip()
        fenced_match = re.search(r'```json\s*(\{[\s\S]*\})\s*```', cleaned)
        if fenced_match:
            cleaned = fenced_match.group(1)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise PodcastScriptError('Podcast script output is not valid JSON.') from exc
        if not isinstance(parsed, dict):
            raise PodcastScriptError('Podcast script output is not a JSON object.')
        return parsed

    def _normalize_result(self, parsed: dict[str, Any], *, report_date: date) -> PodcastScript:
        dialogue_lines = parsed.get('dialogue_lines')
        if not isinstance(dialogue_lines, list):
            raise PodcastScriptError('Podcast dialogue_lines is missing or invalid.')

        normalized_lines: list[dict[str, str]] = []
        total_chars = 0
        for raw_line in dialogue_lines:
            if not isinstance(raw_line, dict):
                continue
            speaker = str(raw_line.get('speaker') or '').strip()
            if speaker not in {'host_a', 'host_b'}:
                continue
            text = str(raw_line.get('text') or '').strip()
            if not text:
                continue
            total_chars += len(text)
            normalized_lines.append({'speaker': speaker, 'text': text})

        if not normalized_lines:
            raise PodcastScriptError('Podcast dialogue is empty after normalization.')

        estimated_minutes = parsed.get('estimated_minutes')
        try:
            estimated_minutes = int(estimated_minutes)
        except (TypeError, ValueError):
            estimated_minutes = max(3, min(8, total_chars // 180))
        estimated_minutes = max(3, min(8, estimated_minutes))

        return PodcastScript(
            title=str(parsed.get('title') or f'AI 时讯随身听 {report_date.isoformat()}').strip(),
            intro=str(parsed.get('intro') or '').strip(),
            outro=str(parsed.get('outro') or '').strip(),
            dialogue_lines=normalized_lines,
            estimated_minutes=estimated_minutes,
        )
