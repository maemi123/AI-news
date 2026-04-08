from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Sequence

from app.schemas import RawContent


@dataclass
class DeduplicationDecision:
    content: RawContent
    is_duplicate: bool
    duplicate_of: int | None = None
    matched_title: str | None = None
    similarity: float = 0.0


class Deduplicator:
    def __init__(self, threshold: float = 0.8) -> None:
        self.threshold = threshold

    def is_duplicate(self, new_content: RawContent, recent_contents: Sequence[Any]) -> DeduplicationDecision:
        normalized_title = self._normalize_title(new_content.title)
        best_similarity = 0.0
        best_match: Any | None = None

        for candidate in recent_contents:
            candidate_title = self._normalize_title(self._read_attr(candidate, 'title'))
            if not candidate_title:
                continue
            similarity = SequenceMatcher(None, normalized_title, candidate_title).ratio()
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = candidate

        if best_match is None or best_similarity < self.threshold:
            return DeduplicationDecision(content=new_content, is_duplicate=False)

        return DeduplicationDecision(
            content=new_content,
            is_duplicate=True,
            duplicate_of=self._safe_int(self._read_attr(best_match, 'id')),
            matched_title=self._read_attr(best_match, 'title') or None,
            similarity=best_similarity,
        )

    def deduplicate_and_merge(
        self,
        content_list: Sequence[RawContent],
        recent_contents: Sequence[Any] | None = None,
    ) -> list[DeduplicationDecision]:
        recent_candidates: list[Any] = list(recent_contents or [])
        decisions: list[DeduplicationDecision] = []

        for content in content_list:
            decision = self.is_duplicate(content, recent_candidates)
            decisions.append(decision)
            if not decision.is_duplicate:
                recent_candidates.append(content)
        return decisions

    def _normalize_title(self, title: str | None) -> str:
        return re.sub(r'\s+', '', str(title or '').strip()).lower()

    def _read_attr(self, value: Any, attr: str) -> Any:
        if isinstance(value, dict):
            return value.get(attr)
        return getattr(value, attr, None)

    def _safe_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
