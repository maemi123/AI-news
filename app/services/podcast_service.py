from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from app.models import PodcastEpisode, PodcastSetting, ProcessedContent
from app.services.audio_storage import AudioStorageError, AudioStorageService
from app.services.edge_tts_service import EdgeDialogueTTSService, EdgeTTSServiceError
from app.services.podcast_script_service import PodcastScriptError, PodcastScriptService
from app.services.tts_service import DialogueTTSService, TTSServiceError

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PodcastBuildResult:
    status: str
    audio_url: str | None = None
    duration_seconds: int | None = None
    title: str | None = None
    error_message: str | None = None


class PodcastService:
    def __init__(self) -> None:
        self.script_service = PodcastScriptService()
        self.tts_service = DialogueTTSService()
        self.edge_tts_service = EdgeDialogueTTSService()
        self.audio_storage = AudioStorageService()

    async def get_or_create_settings(self, session) -> PodcastSetting:
        settings = await session.get(PodcastSetting, 1)
        if settings is not None:
            return settings

        defaults = self.script_service.settings
        settings = PodcastSetting(
            id=1,
            podcast_audio_enabled=defaults.podcast_audio_enabled,
            podcast_include_audio_link=defaults.podcast_include_audio_link,
            podcast_channel=defaults.podcast_channel,
            tts_voice_male=defaults.tts_voice_male,
            tts_voice_female=defaults.tts_voice_female,
        )
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
        return settings

    async def get_latest_episode(self, session) -> PodcastEpisode | None:
        result = await session.execute(
            select(PodcastEpisode).order_by(PodcastEpisode.report_date.desc(), PodcastEpisode.updated_at.desc())
        )
        return result.scalars().first()

    async def get_or_create_episode(self, session, *, report_date: str) -> PodcastEpisode:
        result = await session.execute(
            select(PodcastEpisode).where(PodcastEpisode.report_date == report_date)
        )
        episode = result.scalar_one_or_none()
        if episode is not None:
            return episode

        episode = PodcastEpisode(report_date=report_date)
        session.add(episode)
        await session.commit()
        await session.refresh(episode)
        return episode

    async def build_episode(
        self,
        session,
        *,
        report_date: date,
        contents: list[ProcessedContent],
    ) -> PodcastBuildResult:
        settings = await self.get_or_create_settings(session)
        episode = await self.get_or_create_episode(session, report_date=report_date.isoformat())

        if not settings.podcast_audio_enabled:
            episode.status = 'disabled'
            episode.error_message = None
            await session.commit()
            return PodcastBuildResult(status='disabled')

        if not contents:
            episode.status = 'no_content'
            episode.error_message = None
            await session.commit()
            return PodcastBuildResult(status='no_content')

        episode.status = 'generating'
        episode.error_message = None
        await session.commit()

        try:
            script = await self.script_service.generate_dialogue_script(contents=contents, report_date=report_date)
            audio_bytes, duration_seconds, content_type, extension = await self._synthesize_dialogue(
                dialogue_lines=script.dialogue_lines,
                settings=settings,
            )
            storage_key = f'podcasts/{report_date.isoformat()}/ai-news-dialogue.{extension}'
            uploaded = await self.audio_storage.upload_audio(
                audio_bytes=audio_bytes,
                key=storage_key,
                content_type=content_type,
            )
        except (PodcastScriptError, TTSServiceError, EdgeTTSServiceError, AudioStorageError) as exc:
            episode.status = 'failed'
            episode.error_message = str(exc)
            await session.commit()
            LOGGER.exception('Podcast episode build failed for %s', report_date.isoformat())
            return PodcastBuildResult(status='failed', error_message=str(exc))

        episode.title = script.title
        episode.status = 'ready'
        episode.audio_url = uploaded.public_url
        episode.storage_key = uploaded.key
        episode.duration_seconds = duration_seconds
        episode.script_text = script.script_text
        episode.dialogue_lines = script.dialogue_lines
        episode.error_message = None
        await session.commit()

        return PodcastBuildResult(
            status='ready',
            audio_url=uploaded.public_url,
            duration_seconds=duration_seconds,
            title=script.title,
        )

    async def _synthesize_dialogue(
        self,
        *,
        dialogue_lines: list[dict[str, str]],
        settings: PodcastSetting,
    ) -> tuple[bytes, int, str, str]:
        channel = (settings.podcast_channel or 'built_in').strip().lower()
        if channel == 'edge_tts':
            return await self.edge_tts_service.synthesize_dialogue(
                dialogue_lines,
                male_voice=settings.tts_voice_male,
                female_voice=settings.tts_voice_female,
            )

        self.tts_service.settings.tts_voice_male = settings.tts_voice_male
        self.tts_service.settings.tts_voice_female = settings.tts_voice_female
        return await self.tts_service.synthesize_dialogue(dialogue_lines)
