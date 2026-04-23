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
from app.services.notifier import PushPlusNotifier

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

        last_script: PodcastScript | None = None
        for attempt in range(2):
            prompt = self._build_prompt(contents=contents, report_date=report_date, strict_length=attempt > 0)
            payload = {
                'model': self.settings.deepseek_model,
                'temperature': 0.45,
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
                async with httpx.AsyncClient(timeout=120.0) as client:
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
            script = self._normalize_result(parsed, report_date=report_date)
            last_script = script
            total_chars = sum(len(line['text']) for line in script.dialogue_lines)
            if script.estimated_minutes >= 5 and total_chars >= 900 and len(script.dialogue_lines) >= 24:
                return script
            LOGGER.info(
                'Podcast script was shorter than target on attempt %s: estimated_minutes=%s, chars=%s, lines=%s',
                attempt + 1,
                script.estimated_minutes,
                total_chars,
                len(script.dialogue_lines),
            )

        if last_script is None:
            raise PodcastScriptError('Podcast script generation produced no valid script.')
        return last_script

    def _build_prompt(self, *, contents: list[ProcessedContent], report_date: date, strict_length: bool = False) -> str:
        grouped_items = PushPlusNotifier('')._group_report_items(contents)
        top_items = sorted(
            grouped_items,
            key=lambda item: (
                int(item.get('importance_stars') or 0),
                self._sort_timestamp(item.get('published_at') or item.get('collected_at')),
            ),
            reverse=True,
        )[:18]
        serialized_items = []
        for item in top_items:
            serialized_items.append(
                {
                    'title': item.get('title') or '',
                    'summary': item.get('summary') or '',
                    'importance_stars': item.get('importance_stars') or 1,
                    'importance_reason': item.get('importance_reason') or '',
                    'source': ' / '.join(str(name) for name in item.get('source_names', [])),
                    'related_titles': item.get('cluster_notes') or [],
                    'url': item.get('primary_url') or '',
                }
            )
        items_json = json.dumps(serialized_items, ensure_ascii=False, indent=2)
        extra_length_rule = ''
        if strict_length:
            extra_length_rule = '\n- 上一版太短了，这一版必须明显更完整：至少 24 轮对话、总字数至少 900 字，尽量覆盖更多主题。'
        return f'''请基于下面的 AI 时讯列表，生成一份适合“男主持 + 女主持”的中文双人播客对话稿，只返回 JSON。

节目日期：{report_date.isoformat()}
节目定位：AI 日报随身听。听感要像两位熟悉 AI 行业的人在通勤路上帮听众快速梳理今天的重点。

硬性要求：
- 只基于给定新闻，不添加原文没有的事实。
- 目标时长 5-8 分钟，不能只讲两三条。内容多时可以压缩，但至少覆盖 10 条新闻或全部新闻中的 70%，取二者较小值。
- 对相同主题的重复新闻要合并讲，例如 OpenAI 图像模型、Kimi K2.6 这类同一主题不要反复播报。
- 每个主题至少说明“发生了什么”和“为什么值得听”。
- B 站聚合视频里如果包含多个独立新闻，要拆成多个短点讲，不要只说“某视频提到了很多更新”。
- 语气自然、口语化、有搭档互动感，少一点新闻联播式播报。
- 每轮台词 1-3 句，不要单人长篇独白。
- 可以有轻微接话、追问、补充，但不要段子化，不要虚构人物互动。
- 统一使用简体中文。
{extra_length_rule}

建议结构：
- 开场 2 轮：一句寒暄 + 今天整体看点。
- 重点主题 5-8 个：每个主题 2-4 轮对话。
- 快速扫尾 3-6 条：每条 1-2 轮对话。
- 结尾 1-2 轮：总结今天主线。

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
            estimated_minutes = max(5, min(8, total_chars // 170))
        estimated_minutes = max(5, min(8, estimated_minutes))

        return PodcastScript(
            title=str(parsed.get('title') or f'AI 时讯随身听 {report_date.isoformat()}').strip(),
            intro=str(parsed.get('intro') or '').strip(),
            outro=str(parsed.get('outro') or '').strip(),
            dialogue_lines=normalized_lines,
            estimated_minutes=estimated_minutes,
        )
