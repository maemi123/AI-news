from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProcessVideoResponse(ORMModel):
    video_id: int
    bv_id: str
    title: str
    category: str
    summary: str
    key_entities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    structured_notes: dict[str, Any] = Field(default_factory=dict)
    transcript_source: str


class MonitorSourceBase(ORMModel):
    name: str
    platform: str
    platform_id: str
    source_url: str | None = None
    rss_url: str | None = None
    category: str = 'kol'
    is_active: bool = True
    importance_weight: int = 1
    extra_config: dict[str, Any] = Field(default_factory=dict)


class MonitorSourceCreate(MonitorSourceBase):
    pass


class MonitorSourceUpdate(ORMModel):
    name: str | None = None
    platform: str | None = None
    platform_id: str | None = None
    source_url: str | None = None
    rss_url: str | None = None
    category: str | None = None
    is_active: bool | None = None
    importance_weight: int | None = None
    extra_config: dict[str, Any] | None = None


class MonitorSourceRead(MonitorSourceBase):
    id: int
    last_fetched_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MonitorSourceToggleResponse(MonitorSourceRead):
    pass


class RawContent(BaseModel):
    source_id: int | None = None
    source_name: str | None = None
    source_category: str | None = None
    importance_weight: int = 1
    platform: str
    original_id: str
    title: str
    content: str = ''
    url: str | None = None
    published_at: datetime | None = None
    author: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProcessedContentRead(ORMModel):
    id: int
    source_id: int | None = None
    source_name: str | None = None
    platform: str
    original_id: str
    title: str
    content: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    summary: str | None = None
    category: str | None = None
    importance_stars: int = 1
    importance_reason: str | None = None
    key_entities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    is_duplicate: bool = False
    duplicate_of: int | None = None
    collected_at: datetime
    processed_at: datetime | None = None


class ProcessedContentListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ProcessedContentRead]


class CategoryCount(BaseModel):
    category: str
    count: int


class StatsResponse(BaseModel):
    total_contents: int
    today_contents: int
    active_sources: int
    duplicate_contents: int
    by_platform: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, int] = Field(default_factory=dict)


class FetchRunResponse(BaseModel):
    sources_checked: int
    fetched_items: int
    new_items: int
    duplicate_items: int
    ai_processed_items: int
    stored_items: int
    pushed_messages: int = 0


class PushTestResponse(BaseModel):
    date: date
    items: int
    message_chunks: int
    sent: bool
    preview: list[str] = Field(default_factory=list)


class SimpleStatusResponse(BaseModel):
    ok: bool = True
    message: str = 'ok'


class SystemSettingsResponse(BaseModel):
    scheduler_enabled: bool
    daily_report_hour: int
    daily_report_minute: int
    scheduler_timezone: str
    fetch_lookback_hours: int
    push_provider: str
    pushplus_configured: bool
    pushplus_token_masked: str | None = None
    seed_default_monitor_sources: bool


class SystemSettingsUpdate(BaseModel):
    scheduler_enabled: bool
    daily_report_hour: int = Field(ge=0, le=23)
    daily_report_minute: int = Field(ge=0, le=59)
    scheduler_timezone: str = 'Asia/Shanghai'
    fetch_lookback_hours: int = Field(ge=1, le=168)
    push_provider: str = 'pushplus'
    pushplus_token: str | None = None
