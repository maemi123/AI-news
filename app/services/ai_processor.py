import json
import logging
import re
from typing import Any

import httpx

from app.config import get_settings

LOGGER = logging.getLogger(__name__)
CATEGORIES = [
    'new_model_release',
    'funding_merger',
    'policy_regulation',
    'product_update',
    'research_paper',
    'industry_viewpoint',
    'tutorial_share',
    'other',
]


class AIProcessorError(RuntimeError):
    pass


class AIProcessor:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def generate_summary(self, *, title: str, content: str, source_weight: int = 1) -> dict[str, Any]:
        if not self.settings.deepseek_api_key:
            raise AIProcessorError('DEEPSEEK_API_KEY is not configured.')

        prompt = self._build_prompt(title=title, content=content, source_weight=source_weight)
        payload = {
            'model': self.settings.deepseek_model,
            'temperature': 0.2,
            'response_format': {'type': 'json_object'},
            'messages': [
                {
                    'role': 'system',
                    'content': (
                        'You are an AI news analyst. Reply with a single JSON object and no extra prose.'
                    ),
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
            LOGGER.exception('DeepSeek returned an error: %s', detail)
            raise AIProcessorError(f'DeepSeek returned an error: {detail}') from exc
        except httpx.HTTPError as exc:
            LOGGER.exception('DeepSeek request failed')
            raise AIProcessorError(f'DeepSeek request failed: {exc}') from exc

        try:
            content_text = data['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProcessorError('DeepSeek response structure is invalid.') from exc

        parsed = self._parse_json(content_text)
        return self._normalize_result(parsed, fallback_title=title)

    def _build_prompt(self, *, title: str, content: str, source_weight: int) -> str:
        excerpt = content.strip()[:12000]
        categories_json = json.dumps(CATEGORIES, ensure_ascii=False)
        source_weight = max(1, min(source_weight, 5))
        return f'''Read the following content and decide whether it is truly relevant to AI industry news. Return a valid JSON object only.

Allowed categories: {categories_json}

Use the source weight as one input for importance scoring.
Source importance weight: {source_weight} (1-5)

Importance scoring rules:
- 5 stars: major model releases, large funding, policy shifts, major product launches.
- 4 stars: strong industry impact, widely useful product updates, notable research.
- 3 stars: useful but moderate impact updates.
- 2 stars: niche updates or limited impact.
- 1 star: low signal or routine information.

Relevance rules:
- Set "is_ai_relevant" to true only when the content is directly about AI models, AI products, AI companies, AI policy, AI research, AI chips, AI agents, AI tooling, robotics, autonomous driving, or core enabling infrastructure for AI.
- Set "is_ai_relevant" to false for general politics, sports, entertainment, macro news, social commentary, personal life updates, or reposts that do not have clear AI relevance.
- Being posted by a famous AI person does NOT automatically make the content AI relevant.

Language rules:
- All natural-language fields in the JSON must be written in Simplified Chinese.
- If the original content is in English, translate and summarize it in Chinese.
- "title" must be a concise Chinese title, not the original English sentence.

Required JSON shape:
{{
  "title": "中文标题",
  "summary": "中文摘要，2-4句，尽量控制在120字以内",
  "category": "one allowed category",
  "importance_stars": 4,
  "importance_reason": "中文短理由",
  "is_ai_relevant": true,
  "relevance_reason": "中文说明它为什么属于或不属于AI时讯",
  "key_entities": ["entity1", "entity2"],
  "tags": ["tag1", "tag2"],
  "structured_notes": {{
    "core_concept": "中文一句话核心观点",
    "key_points": ["中文要点1", "中文要点2", "中文要点3"],
    "code_or_example": "中文示例或空字符串",
    "reference_links": ["https://example.com"]
  }}
}}

Title: {title}

Content:
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
            LOGGER.error('LLM output is not valid JSON: %s', content_text)
            raise AIProcessorError('LLM output is not valid JSON.') from exc
        if not isinstance(parsed, dict):
            raise AIProcessorError('LLM output is not a JSON object.')
        return parsed

    def _normalize_result(self, parsed: dict[str, Any], *, fallback_title: str) -> dict[str, Any]:
        category = str(parsed.get('category') or 'other')
        if category not in CATEGORIES:
            category = 'other'

        structured_notes = parsed.get('structured_notes')
        if not isinstance(structured_notes, dict):
            structured_notes = {}

        key_points = structured_notes.get('key_points')
        if not isinstance(key_points, list):
            key_points = []

        reference_links = structured_notes.get('reference_links')
        if not isinstance(reference_links, list):
            reference_links = []

        importance_stars = parsed.get('importance_stars', 1)
        try:
            importance_stars = int(importance_stars)
        except (TypeError, ValueError):
            importance_stars = 1
        importance_stars = max(1, min(importance_stars, 5))
        is_ai_relevant = parsed.get('is_ai_relevant', True)
        if not isinstance(is_ai_relevant, bool):
            is_ai_relevant = str(is_ai_relevant).strip().lower() in {'true', '1', 'yes'}

        return {
            'title': str(parsed.get('title') or fallback_title),
            'summary': str(parsed.get('summary') or '').strip(),
            'category': category,
            'importance_stars': importance_stars,
            'importance_reason': str(parsed.get('importance_reason') or '').strip(),
            'is_ai_relevant': is_ai_relevant,
            'relevance_reason': str(parsed.get('relevance_reason') or '').strip(),
            'key_entities': [str(item) for item in parsed.get('key_entities', []) if str(item).strip()],
            'tags': [str(item) for item in parsed.get('tags', []) if str(item).strip()],
            'structured_notes': {
                'core_concept': str(structured_notes.get('core_concept') or '').strip(),
                'key_points': [str(item) for item in key_points if str(item).strip()],
                'code_or_example': str(structured_notes.get('code_or_example') or '').strip(),
                'reference_links': [str(item) for item in reference_links if str(item).strip()],
            },
        }
