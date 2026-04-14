from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.utils.helpers import utcnow


class Base(DeclarativeBase):
    pass


class SystemSetting(Base):
    __tablename__ = 'system_settings'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    scheduler_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_report_hour: Mapped[int] = mapped_column(Integer, default=8)
    daily_report_minute: Mapped[int] = mapped_column(Integer, default=0)
    fetch_lookback_hours: Mapped[int] = mapped_column(Integer, default=24)
    scheduler_timezone: Mapped[str] = mapped_column(String(64), default='Asia/Shanghai')
    push_provider: Mapped[str] = mapped_column(String(32), default='pushplus')
    pushplus_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    seed_default_monitor_sources: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MonitorSource(Base):
    __tablename__ = 'monitor_sources'
    __table_args__ = (
        UniqueConstraint('platform', 'platform_id', name='uq_monitor_source_platform_platform_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    platform: Mapped[str] = mapped_column(String(50), index=True)
    platform_id: Mapped[str] = mapped_column(String(100))
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rss_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    category: Mapped[str] = mapped_column(String(50), default='kol', index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    importance_weight: Mapped[int] = mapped_column(Integer, default=1)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    extra_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    processed_contents: Mapped[list[ProcessedContent]] = relationship(back_populates='source')


class Video(Base):
    __tablename__ = 'videos'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    bv_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    aid: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    cid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_mid: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    publish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    has_subtitle: Mapped[bool] = mapped_column(default=False)
    subtitle_language: Mapped[str | None] = mapped_column(String(50), nullable=True)
    subtitle_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    summary: Mapped[Summary | None] = relationship(back_populates='video', cascade='all, delete-orphan', uselist=False)


class Summary(Base):
    __tablename__ = 'summaries'
    __table_args__ = (UniqueConstraint('video_id', name='uq_summary_video_id'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    video_id: Mapped[int] = mapped_column(ForeignKey('videos.id', ondelete='CASCADE'), index=True)
    title: Mapped[str] = mapped_column(String(500))
    summary_text: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(100), index=True)
    key_entities: Mapped[list[str]] = mapped_column(JSON, default=list)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    structured_notes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    raw_response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    video: Mapped[Video] = relationship(back_populates='summary')


class ProcessedContent(Base):
    __tablename__ = 'processed_contents'
    __table_args__ = (
        UniqueConstraint('platform', 'original_id', name='uq_processed_content_platform_original_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey('monitor_sources.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
    )
    source_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    platform: Mapped[str] = mapped_column(String(50), index=True)

    original_id: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str | None] = mapped_column(String(5000), nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    importance_stars: Mapped[int] = mapped_column(Integer, default=1, index=True)
    importance_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    key_entities: Mapped[list[str]] = mapped_column(JSON, default=list)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)

    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    duplicate_of: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source: Mapped[MonitorSource | None] = relationship(back_populates='processed_contents')


class ScheduledPushState(Base):
    __tablename__ = 'scheduled_push_states'
    __table_args__ = (
        UniqueConstraint('report_date', name='uq_scheduled_push_state_report_date'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_date: Mapped[str] = mapped_column(String(16), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PodcastSetting(Base):
    __tablename__ = 'podcast_settings'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    podcast_audio_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    podcast_include_audio_link: Mapped[bool] = mapped_column(Boolean, default=True)
    podcast_channel: Mapped[str] = mapped_column(String(32), default='built_in')
    tts_voice_male: Mapped[str] = mapped_column(String(100), default='alloy')
    tts_voice_female: Mapped[str] = mapped_column(String(100), default='nova')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PodcastEpisode(Base):
    __tablename__ = 'podcast_episodes'
    __table_args__ = (
        UniqueConstraint('report_date', name='uq_podcast_episode_report_date'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_date: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default='pending', index=True)
    audio_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    script_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    dialogue_lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
