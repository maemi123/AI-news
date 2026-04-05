﻿from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.utils.helpers import utcnow


class Base(DeclarativeBase):
    pass


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
    key_entities: Mapped[list] = mapped_column(JSON, default=list)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    structured_notes: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_response: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    video: Mapped[Video] = relationship(back_populates='summary')
