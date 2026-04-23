import asyncio
import logging
import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any

import httpx
from yt_dlp import YoutubeDL

from app.config import get_settings

LOGGER = logging.getLogger(__name__)


class BilibiliAPIError(RuntimeError):
    pass


class _SilentYTDLPLogger:
    def debug(self, msg: str) -> None:
        LOGGER.debug('yt-dlp: %s', msg)

    def warning(self, msg: str) -> None:
        LOGGER.warning('yt-dlp: %s', msg)

    def error(self, msg: str) -> None:
        LOGGER.error('yt-dlp: %s', msg)


@dataclass
class SubtitleTrack:
    lan: str
    lan_doc: str
    subtitle_url: str


@dataclass
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str


class BilibiliService:
    VIEW_API = 'https://api.bilibili.com/x/web-interface/view'
    PLAYER_API = 'https://api.bilibili.com/x/player/v2'
    USER_VIDEO_API = 'https://api.bilibili.com/x/space/arc/search'
    USER_AGENTS = [
        (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/123.0.0.0 Safari/537.36'
        ),
        (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) '
            'Gecko/20100101 Firefox/137.0'
        ),
    ]

    def __init__(self) -> None:
        self.settings = get_settings()

    def _headers(self, referer: str | None = None, user_agent: str | None = None) -> dict[str, str]:
        headers = {
            'User-Agent': user_agent or self.USER_AGENTS[0],
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        if referer:
            headers['Referer'] = referer
            if 'space.bilibili.com' in referer:
                headers['Origin'] = 'https://space.bilibili.com'
        return headers

    def _cookies(self) -> dict[str, str] | None:
        cookies: dict[str, str] = {}
        if self.settings.effective_bilibili_sessdata:
            cookies['SESSDATA'] = self.settings.effective_bilibili_sessdata
        if self.settings.effective_bilibili_bili_jct:
            cookies['bili_jct'] = self.settings.effective_bilibili_bili_jct
        if self.settings.effective_bilibili_buvid3:
            cookies['buvid3'] = self.settings.effective_bilibili_buvid3
        return cookies or None

    def _create_cookie_file(self) -> str | None:
        cookies = self._cookies() or {}
        if not cookies:
            return None

        with NamedTemporaryFile('w', encoding='utf-8', suffix='.txt', delete=False) as temp_file:
            temp_file.write('# Netscape HTTP Cookie File\n')
            for key, value in cookies.items():
                temp_file.write(f'.bilibili.com\tTRUE\t/\tTRUE\t0\t{key}\t{value}\n')
            return temp_file.name

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        referer: str | None = None,
    ) -> dict[str, Any]:
        attempts = len(self.USER_AGENTS) + 1 if url == self.USER_VIDEO_API else 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            user_agent = self.USER_AGENTS[min(attempt, len(self.USER_AGENTS) - 1)]
            try:
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    response = await client.get(
                        url,
                        params=params,
                        headers=self._headers(referer, user_agent=user_agent),
                        cookies=self._cookies(),
                    )
                    response.raise_for_status()
                    payload = response.json()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if url == self.USER_VIDEO_API and exc.response.status_code == 412 and attempt < attempts - 1:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                LOGGER.exception('Bilibili request failed: %s', url)
                raise BilibiliAPIError(f'Bilibili request failed: {exc}') from exc
            except httpx.HTTPError as exc:
                LOGGER.exception('Bilibili request failed: %s', url)
                raise BilibiliAPIError(f'Bilibili request failed: {exc}') from exc

            if payload.get('code', 0) == 0:
                return payload.get('data') or {}

            message = payload.get('message') or payload.get('msg') or 'Bilibili API returned an error'
            if url == self.USER_VIDEO_API and message in {'请求过于频繁，请稍后再试', 'request was banned'} and attempt < attempts - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise BilibiliAPIError(message)

        if last_error is not None:
            raise BilibiliAPIError(f'Bilibili request failed: {last_error}') from last_error
        raise BilibiliAPIError('Bilibili API returned an error')

    async def get_video_info(self, bv_id: str) -> dict[str, Any]:
        data = await self._get_json(
            self.VIEW_API,
            params={'bvid': bv_id},
            referer=f'https://www.bilibili.com/video/{bv_id}',
        )
        pages = data.get('pages') or []
        first_page = pages[0] if pages else {}
        pubdate = data.get('pubdate')
        publish_time = datetime.fromtimestamp(pubdate, tz=timezone.utc) if pubdate else None
        owner = data.get('owner') or {}

        return {
            'bv_id': data.get('bvid') or bv_id,
            'aid': data.get('aid'),
            'cid': first_page.get('cid') or data.get('cid'),
            'title': self._repair_mojibake(data.get('title') or ''),
            'description': self._repair_mojibake(data.get('desc') or ''),
            'owner_name': self._repair_mojibake(owner.get('name') or '') or None,
            'owner_mid': owner.get('mid'),
            'publish_time': publish_time,
            'source_url': f"https://www.bilibili.com/video/{data.get('bvid') or bv_id}",
        }

    async def get_user_videos(self, user_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        try:
            data = await self._get_json(
                self.USER_VIDEO_API,
                params={
                    'mid': user_id,
                    'pn': 1,
                    'ps': limit,
                    'order': 'pubdate',
                    'jsonp': 'jsonp',
                },
                referer=f'https://space.bilibili.com/{user_id}',
            )
            video_list = ((data.get('list') or {}).get('vlist')) or []
            items: list[dict[str, Any]] = []
            for item in video_list:
                created = item.get('created')
                items.append(
                    {
                        'original_id': item.get('bvid') or str(item.get('aid') or ''),
                        'title': self._repair_mojibake(item.get('title') or ''),
                        'content': self._repair_mojibake(item.get('description') or ''),
                        'url': f"https://www.bilibili.com/video/{item.get('bvid')}" if item.get('bvid') else None,
                        'published_at': datetime.fromtimestamp(created, tz=timezone.utc) if created else None,
                        'author': self._repair_mojibake(item.get('author') or '') or None,
                    }
                )
            return await self._hydrate_video_entries(items)
        except BilibiliAPIError as exc:
            LOGGER.warning('Falling back to yt-dlp for uploader %s: %s', user_id, exc)
            items = await self._get_user_videos_via_ytdlp(user_id, limit=limit)
            return await self._hydrate_video_entries(items)

    async def _hydrate_video_entries(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        hydrated: list[dict[str, Any]] = []
        for item in items:
            bv_id = self._extract_bv_id(item)
            if not bv_id:
                hydrated.append(item)
                continue

            try:
                info = await self.get_video_with_transcript(bv_id)
            except Exception as exc:
                LOGGER.warning('Skipping transcript hydration for %s: %s', bv_id, exc)
                hydrated.append(item)
                continue

            enriched = dict(item)
            enriched['original_id'] = item.get('original_id') or bv_id
            enriched['title'] = self._repair_mojibake(item.get('title') or info.get('title') or '')
            enriched['content'] = self._repair_mojibake(
                item.get('content') or info.get('transcript_content') or info.get('description') or ''
            )
            enriched['url'] = item.get('url') or info.get('source_url')
            enriched['published_at'] = item.get('published_at') or info.get('publish_time')
            enriched['author'] = self._repair_mojibake(item.get('author') or info.get('owner_name') or '') or None
            enriched['transcript_content'] = info.get('transcript_content') or ''
            enriched['transcript_segments'] = info.get('transcript_segments') or []
            enriched['transcript_source'] = info.get('transcript_source') or 'none'
            hydrated.append(enriched)
        return hydrated

    def _extract_bv_id(self, item: dict[str, Any]) -> str | None:
        original_id = str(item.get('original_id') or '').strip()
        if original_id.startswith('BV'):
            return original_id

        url = str(item.get('url') or '').strip()
        if not url:
            return None

        match = re.search(r'/video/(BV[0-9A-Za-z]+)', url)
        if match:
            return match.group(1)
        return None

    async def _get_user_videos_via_ytdlp(self, user_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        url = f'https://space.bilibili.com/{user_id}/video'
        last_error: Exception | None = None

        for attempt, user_agent in enumerate(self.USER_AGENTS, start=1):
            headers = {
                'User-Agent': user_agent,
                'Referer': url,
            }
            cookie_file = self._create_cookie_file()

            def extract() -> list[dict[str, Any]]:
                options = {
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': True,
                    'playlistend': limit,
                    'http_headers': headers,
                    'cookiefile': cookie_file,
                    'logger': _SilentYTDLPLogger(),
                }
                with YoutubeDL(options) as ydl:
                    info = ydl.extract_info(url, download=False)
                entries = info.get('entries') or []
                items: list[dict[str, Any]] = []
                for item in entries:
                    webpage_url = item.get('url') or item.get('webpage_url')
                    if not webpage_url:
                        continue
                    original_id = item.get('id') or webpage_url.rsplit('/', 1)[-1]
                    release_ts = item.get('timestamp') or item.get('release_timestamp')
                    items.append(
                        {
                            'original_id': original_id,
                            'title': self._repair_mojibake(item.get('title') or ''),
                            'content': self._repair_mojibake(item.get('description') or ''),
                            'url': webpage_url,
                            'published_at': datetime.fromtimestamp(release_ts, tz=timezone.utc) if release_ts else None,
                            'author': self._repair_mojibake(item.get('uploader') or item.get('channel') or '') or None,
                        }
                    )
                return items

            try:
                return await asyncio.to_thread(extract)
            except Exception as exc:
                last_error = exc
                if attempt < len(self.USER_AGENTS):
                    await asyncio.sleep(2 * attempt)
                    continue
            finally:
                if cookie_file:
                    Path(cookie_file).unlink(missing_ok=True)

        raise BilibiliAPIError(f'yt-dlp uploader fallback failed: {last_error}') from last_error

    async def get_subtitle_tracks(self, bv_id: str, cid: int | None) -> list[SubtitleTrack]:
        if not cid:
            return []

        data = await self._get_json(
            self.PLAYER_API,
            params={'bvid': bv_id, 'cid': cid},
            referer=f'https://www.bilibili.com/video/{bv_id}',
        )
        subtitle_data = data.get('subtitle') or {}
        tracks = subtitle_data.get('subtitles') or []
        return [
            SubtitleTrack(
                lan=item.get('lan') or '',
                lan_doc=item.get('lan_doc') or '',
                subtitle_url=item.get('subtitle_url') or '',
            )
            for item in tracks
            if item.get('subtitle_url')
        ]

    async def get_cc_subtitle_segments(self, subtitle_url: str) -> list[TranscriptSegment]:
        if not subtitle_url:
            return []
        if subtitle_url.startswith('//'):
            subtitle_url = 'https:' + subtitle_url
        elif subtitle_url.startswith('/'):
            subtitle_url = 'https://api.bilibili.com' + subtitle_url

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(subtitle_url, headers=self._headers())
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            LOGGER.exception('Subtitle download failed: %s', subtitle_url)
            raise BilibiliAPIError(f'Subtitle download failed: {exc}') from exc

        body = payload.get('body') or []
        segments: list[TranscriptSegment] = []
        for item in body:
            text = str(item.get('content') or '').strip()
            if not text:
                continue
            segments.append(
                TranscriptSegment(
                    start_seconds=float(item.get('from') or 0.0),
                    end_seconds=float(item.get('to') or item.get('from') or 0.0),
                    text=self._repair_mojibake(text),
                )
            )
        return segments

    async def get_cc_subtitle_content(self, subtitle_url: str) -> str:
        segments = await self.get_cc_subtitle_segments(subtitle_url)
        return '\n'.join(segment.text for segment in segments if segment.text.strip())

    async def get_video_with_subtitle(self, bv_id: str) -> dict[str, Any]:
        video = await self.get_video_info(bv_id)
        tracks = await self.get_subtitle_tracks(video['bv_id'], video.get('cid'))
        subtitle_language = None
        subtitle_segments: list[TranscriptSegment] = []

        if tracks:
            subtitle_language = tracks[0].lan_doc or tracks[0].lan
            subtitle_segments = await self.get_cc_subtitle_segments(tracks[0].subtitle_url)

        subtitle_content = '\n'.join(segment.text for segment in subtitle_segments if segment.text.strip())
        video.update(
            {
                'has_subtitle': bool(subtitle_content),
                'subtitle_language': subtitle_language,
                'subtitle_content': subtitle_content,
                'subtitle_segments': subtitle_segments,
            }
        )
        return video

    async def get_video_with_transcript(self, bv_id: str) -> dict[str, Any]:
        video = await self.get_video_with_subtitle(bv_id)
        subtitle_segments = list(video.get('subtitle_segments') or [])
        if subtitle_segments:
            video['transcript_source'] = 'subtitle'
            video['transcript_segments'] = subtitle_segments
            video['transcript_content'] = '\n'.join(segment.text for segment in subtitle_segments if segment.text.strip())
            return video

        if not self.settings.whisper_api_key:
            video['transcript_source'] = 'none'
            video['transcript_segments'] = []
            video['transcript_content'] = ''
            return video

        runtime_temp_root = Path.cwd() / '.runtime' / 'bilibili_audio'
        runtime_temp_root.mkdir(parents=True, exist_ok=True)
        try:
            with TemporaryDirectory(
                prefix='ai_news_bili_',
                dir=runtime_temp_root,
                ignore_cleanup_errors=True,
            ) as temp_dir:
                audio_path = await self._download_audio(video['source_url'], Path(temp_dir))
                transcript_text, transcript_segments = await self._transcribe_audio_with_whisper(audio_path)
        except Exception as exc:
            LOGGER.warning('Bilibili transcript fallback for %s: %s', bv_id, exc)
            video['transcript_source'] = 'none'
            video['transcript_segments'] = []
            video['transcript_content'] = ''
            return video

        video['transcript_source'] = 'whisper'
        video['transcript_segments'] = transcript_segments
        video['transcript_content'] = transcript_text
        return video

    async def _download_audio(self, video_url: str, target_dir: Path) -> Path:
        output_template = str(target_dir / 'audio.%(ext)s')

        def run_download() -> None:
            options = {
                'outtmpl': output_template,
                'noplaylist': True,
                'no_warnings': True,
                'format': 'ba[ext=m4a]/ba/bestaudio',
                'quiet': True,
                'overwrites': True,
            }
            with YoutubeDL(options) as downloader:
                downloader.download([video_url])

        try:
            await asyncio.to_thread(run_download)
        except Exception as exc:
            raise BilibiliAPIError(f'Audio download failed: {exc}') from exc

        audio_files = sorted(
            [
                file_path
                for file_path in target_dir.glob('audio.*')
                if file_path.suffix.lower() in {'.m4a', '.mp3', '.mp4', '.mpeg', '.mpga', '.wav', '.webm'}
            ]
        )
        if not audio_files:
            raise BilibiliAPIError('Audio download completed but no usable audio file was found.')
        return audio_files[0]

    async def _transcribe_audio_with_whisper(self, audio_path: Path) -> tuple[str, list[TranscriptSegment]]:
        mime_type, _ = mimetypes.guess_type(audio_path.name)
        headers = {
            'Authorization': f'Bearer {self.settings.whisper_api_key}',
        }

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                with audio_path.open('rb') as audio_file:
                    response = await client.post(
                        self.settings.whisper_transcriptions_url,
                        headers=headers,
                        data={
                            'model': self.settings.whisper_model,
                            'response_format': 'verbose_json',
                        },
                        files={
                            'file': (
                                audio_path.name,
                                audio_file,
                                mime_type or 'application/octet-stream',
                            )
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise BilibiliAPIError(f'Whisper transcription failed: {detail}') from exc
        except httpx.HTTPError as exc:
            raise BilibiliAPIError(f'Whisper request failed: {exc}') from exc

        text = str(payload.get('text') or '').strip()
        if not text:
            raise BilibiliAPIError('Whisper transcription succeeded but returned no text.')

        raw_segments = payload.get('segments') or []
        segments: list[TranscriptSegment] = []
        if isinstance(raw_segments, list):
            for item in raw_segments:
                if not isinstance(item, dict):
                    continue
                segment_text = str(item.get('text') or '').strip()
                if not segment_text:
                    continue
                segments.append(
                    TranscriptSegment(
                        start_seconds=float(item.get('start') or 0.0),
                        end_seconds=float(item.get('end') or item.get('start') or 0.0),
                        text=self._repair_mojibake(segment_text),
                    )
                )
        if not segments:
            segments = [TranscriptSegment(start_seconds=0.0, end_seconds=0.0, text=self._repair_mojibake(text))]
        return self._repair_mojibake(text), segments

    def _repair_mojibake(self, value: Any) -> str:
        text = str(value or '')
        if not text:
            return ''

        suspicious_markers = ('Ã', 'Â', 'å', 'æ', 'ç', 'è', 'é', 'ï¼', 'ã€')
        chinese_chars = sum(1 for char in text if '\u4e00' <= char <= '\u9fff')
        suspicious_hits = sum(text.count(marker) for marker in suspicious_markers)
        if suspicious_hits < 2 or chinese_chars > 0:
            return text

        try:
            repaired = text.encode('latin-1').decode('utf-8')
        except UnicodeError:
            return text

        repaired_chinese_chars = sum(1 for char in repaired if '\u4e00' <= char <= '\u9fff')
        return repaired if repaired_chinese_chars > chinese_chars else text
