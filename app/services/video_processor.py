import asyncio
import logging
import mimetypes
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import yt_dlp

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Summary, Video
from app.services.ai_processor import AIProcessor
from app.services.bilibili_service import BilibiliService

LOGGER = logging.getLogger(__name__)


class VideoProcessorError(RuntimeError):
    pass


class VideoProcessor:
    def __init__(
        self,
        bilibili_service: BilibiliService | None = None,
        ai_processor: AIProcessor | None = None,
    ) -> None:
        self.settings = get_settings()
        self.bilibili_service = bilibili_service or BilibiliService()
        self.ai_processor = ai_processor or AIProcessor()

    async def process_video(self, session: AsyncSession, bv_id: str) -> dict:
        video_data = await self.bilibili_service.get_video_with_subtitle(bv_id)
        content = (video_data.get("subtitle_content") or "").strip()
        transcript_source = "subtitle" if content else "whisper"

        if not content:
            LOGGER.info("视频 %s 未获取到 CC 字幕，尝试音频转写。", bv_id)
            content = await self._transcribe_video_audio(video_data["source_url"])

        if not content.strip():
            raise VideoProcessorError("未能从字幕或音频转写中提取到可用文本。")

        ai_result = await self.ai_processor.generate_summary(
            title=video_data["title"],
            content=content,
        )

        video = await self._upsert_video(session, video_data, content)
        summary = await self._upsert_summary(session, video.id, ai_result)
        await session.commit()
        await session.refresh(video)
        await session.refresh(summary)

        return {
            "video_id": video.id,
            "bv_id": video.bv_id,
            "title": summary.title,
            "category": summary.category,
            "summary": summary.summary_text,
            "key_entities": summary.key_entities or [],
            "tags": summary.tags or [],
            "structured_notes": summary.structured_notes or {},
            "transcript_source": transcript_source,
        }

    async def _transcribe_video_audio(self, video_url: str) -> str:
        if not self.settings.whisper_api_key:
            raise VideoProcessorError("视频无可用 CC 字幕，且未配置 WHISPER_API_KEY，无法进行音频转写。")

        with TemporaryDirectory(prefix="ai_news_") as temp_dir:
            audio_path = await self._download_audio(video_url, Path(temp_dir))
            return await self._transcribe_audio_with_whisper(audio_path)

    async def _download_audio(self, video_url: str, target_dir: Path) -> Path:
        output_template = str(target_dir / "audio.%(ext)s")

        def run_download() -> None:
            options = {
                "outtmpl": output_template,
                "noplaylist": True,
                "no_warnings": True,
                "format": "ba[ext=m4a]/ba/bestaudio",
                "quiet": True,
            }
            with yt_dlp.YoutubeDL(options) as downloader:
                downloader.download([video_url])

        try:
            await asyncio.to_thread(run_download)
        except Exception as exc:
            raise VideoProcessorError(f"音频下载失败: {exc}") from exc

        audio_files = sorted(
            [
                file_path
                for file_path in target_dir.glob("audio.*")
                if file_path.suffix.lower() in {".m4a", ".mp3", ".mp4", ".mpeg", ".mpga", ".wav", ".webm"}
            ]
        )
        if not audio_files:
            raise VideoProcessorError("音频下载完成，但未找到可用于转写的音频文件。")
        return audio_files[0]

    async def _transcribe_audio_with_whisper(self, audio_path: Path) -> str:
        mime_type, _ = mimetypes.guess_type(audio_path.name)
        headers = {
            "Authorization": f"Bearer {self.settings.whisper_api_key}",
        }

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                with audio_path.open("rb") as audio_file:
                    response = await client.post(
                        self.settings.whisper_transcriptions_url,
                        headers=headers,
                        data={
                            "model": self.settings.whisper_model,
                            "response_format": "json",
                        },
                        files={
                            "file": (
                                audio_path.name,
                                audio_file,
                                mime_type or "application/octet-stream",
                            )
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise VideoProcessorError(f"Whisper 转写失败: {detail}") from exc
        except httpx.HTTPError as exc:
            raise VideoProcessorError(f"Whisper 请求失败: {exc}") from exc

        text = str(payload.get("text") or "").strip()
        if not text:
            raise VideoProcessorError("Whisper 转写成功，但未返回文本内容。")
        return text

    async def _upsert_video(self, session: AsyncSession, video_data: dict, content: str) -> Video:
        result = await session.execute(select(Video).where(Video.bv_id == video_data["bv_id"]))
        video = result.scalar_one_or_none()

        if video is None:
            video = Video(bv_id=video_data["bv_id"], title=video_data["title"])
            session.add(video)

        video.aid = video_data.get("aid")
        video.cid = video_data.get("cid")
        video.title = video_data.get("title") or video.title
        video.description = video_data.get("description")
        video.owner_name = video_data.get("owner_name")
        video.owner_mid = video_data.get("owner_mid")
        video.publish_time = video_data.get("publish_time")
        video.has_subtitle = bool(video_data.get("has_subtitle"))
        video.subtitle_language = video_data.get("subtitle_language")
        video.subtitle_content = video_data.get("subtitle_content")
        video.transcript_content = content
        video.source_url = video_data.get("source_url")

        await session.flush()
        return video

    async def _upsert_summary(self, session: AsyncSession, video_id: int, ai_result: dict) -> Summary:
        result = await session.execute(select(Summary).where(Summary.video_id == video_id))
        summary = result.scalar_one_or_none()

        if summary is None:
            summary = Summary(video_id=video_id, title=ai_result["title"], summary_text=ai_result["summary"], category=ai_result["category"])
            session.add(summary)

        summary.title = ai_result["title"]
        summary.summary_text = ai_result["summary"]
        summary.category = ai_result["category"]
        summary.key_entities = ai_result.get("key_entities", [])
        summary.tags = ai_result.get("tags", [])
        summary.structured_notes = ai_result.get("structured_notes", {})
        summary.raw_response = ai_result

        await session.flush()
        return summary
