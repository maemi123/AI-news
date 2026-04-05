﻿import json
import logging
import re
from typing import Any

import httpx

from app.config import get_settings

LOGGER = logging.getLogger(__name__)
CATEGORIES = [
    '新模型发布',
    '融资并购',
    '政策法规',
    '产品更新',
    '学术论文',
    '行业观点',
    '教程分享',
    '其他',
]


class AIProcessorError(RuntimeError):
    pass


class AIProcessor:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def generate_summary(self, *, title: str, content: str) -> dict[str, Any]:
        if not self.settings.deepseek_api_key:
            raise AIProcessorError('未配置 DEEPSEEK_API_KEY，无法调用摘要服务。')

        prompt = self._build_prompt(title=title, content=content)
        payload = {
            'model': self.settings.deepseek_model,
            'temperature': 0.2,
            'response_format': {'type': 'json_object'},
            'messages': [
                {
                    'role': 'system',
                    'content': '你是AI资讯分析助手。请严格输出JSON对象，不要输出额外解释。',
                },
                {'role': 'user', 'content': prompt},
            ],
        }
        headers = {
            'Authorization': f'Bearer {self.settings.deepseek_api_key}',
            'Content-Type': 'application/json',
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    self.settings.deepseek_chat_completions_url,
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            LOGGER.exception('DeepSeek 接口返回异常: %s', detail)
            raise AIProcessorError(f'DeepSeek 接口返回异常: {detail}') from exc
        except httpx.HTTPError as exc:
            LOGGER.exception('DeepSeek 请求失败')
            raise AIProcessorError(f'DeepSeek 请求失败: {exc}') from exc

        try:
            content_text = data['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProcessorError('DeepSeek 返回结构不符合预期。') from exc

        parsed = self._parse_json(content_text)
        return self._normalize_result(parsed, fallback_title=title)

    def _build_prompt(self, *, title: str, content: str) -> str:
        excerpt = content.strip()[:12000]
        categories_json = json.dumps(CATEGORIES, ensure_ascii=False)
        return f'''请基于以下视频文字内容，生成结构化摘要与分类。

分类只能从以下列表中选择一个：{categories_json}

输出必须是合法 JSON，对象字段必须完整，字段结构如下：
{{
  "title": "视频标题",
  "summary": "3-5句话的核心内容摘要，200字以内",
  "category": "新模型发布",
  "key_entities": ["DeepSeek", "GPT-4", "OpenAI"],
  "tags": ["大模型", "开源", "评测"],
  "structured_notes": {{
    "core_concept": "视频核心概念一句话总结",
    "key_points": ["要点1", "要点2", "要点3"],
    "code_or_example": "如果视频中有代码或示例，提取出来；没有则填空字符串",
    "reference_links": ["相关链接1", "相关链接2"]
  }}
}}

视频标题：{title}

视频文字内容：
{excerpt}
'''

    def _parse_json(self, content_text: str) -> dict[str, Any]:
        cleaned = content_text.strip()
        fenced_match = re.search(r'```json\s*(\{[\s\S]*\})\s*```', cleaned)
        if fenced_match:
            cleaned = fenced_match.group(1)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            LOGGER.error('LLM 返回无法解析为 JSON: %s', content_text)
            raise AIProcessorError('LLM 返回内容不是合法 JSON。') from exc
        if not isinstance(parsed, dict):
            raise AIProcessorError('LLM 返回内容不是 JSON 对象。')
        return parsed

    def _normalize_result(self, parsed: dict[str, Any], *, fallback_title: str) -> dict[str, Any]:
        category = parsed.get('category') or '其他'
        if category not in CATEGORIES:
            category = '其他'

        structured_notes = parsed.get('structured_notes')
        if not isinstance(structured_notes, dict):
            structured_notes = {}

        key_points = structured_notes.get('key_points')
        if not isinstance(key_points, list):
            key_points = []

        reference_links = structured_notes.get('reference_links')
        if not isinstance(reference_links, list):
            reference_links = []

        return {
            'title': str(parsed.get('title') or fallback_title),
            'summary': str(parsed.get('summary') or '').strip(),
            'category': category,
            'key_entities': [str(item) for item in parsed.get('key_entities', []) if str(item).strip()],
            'tags': [str(item) for item in parsed.get('tags', []) if str(item).strip()],
            'structured_notes': {
                'core_concept': str(structured_notes.get('core_concept') or '').strip(),
                'key_points': [str(item) for item in key_points if str(item).strip()],
                'code_or_example': str(structured_notes.get('code_or_example') or '').strip(),
                'reference_links': [str(item) for item in reference_links if str(item).strip()],
            },
        }
