import logging
import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
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
        if not cookies:
            return None
        return cookies

    def _cookie_header(self) -> str:
        cookies = self._cookies() or {}
        return '; '.join(f'{key}={value}' for key, value in cookies.items())

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
            'title': data.get('title') or '',
            'description': data.get('desc') or '',
            'owner_name': owner.get('name'),
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
                        'title': item.get('title') or '',
                        'content': item.get('description') or '',
                        'url': f"https://www.bilibili.com/video/{item.get('bvid')}" if item.get('bvid') else None,
                        'published_at': datetime.fromtimestamp(created, tz=timezone.utc) if created else None,
                        'author': item.get('author') or None,
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
            if item.get('title') and item.get('published_at'):
                hydrated.append(item)
                continue

            bv_id = self._extract_bv_id(item)
            if not bv_id:
                hydrated.append(item)
                continue

            try:
                info = await self.get_video_info(bv_id)
            except BilibiliAPIError:
                hydrated.append(item)
                continue

            enriched = dict(item)
            enriched['original_id'] = item.get('original_id') or bv_id
            enriched['title'] = item.get('title') or info.get('title') or ''
            enriched['content'] = item.get('content') or info.get('description') or ''
            enriched['url'] = item.get('url') or info.get('source_url')
            enriched['published_at'] = item.get('published_at') or info.get('publish_time')
            enriched['author'] = item.get('author') or info.get('owner_name')
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
                            'title': item.get('title') or '',
                            'content': item.get('description') or '',
                            'url': webpage_url,
                            'published_at': datetime.fromtimestamp(release_ts, tz=timezone.utc) if release_ts else None,
                            'author': item.get('uploader') or item.get('channel') or None,
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

    async def get_cc_subtitle_content(self, subtitle_url: str) -> str:
        if not subtitle_url:
            return ''
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
        lines = [item.get('content', '').strip() for item in body if item.get('content')]
        return '\n'.join(line for line in lines if line)

    async def get_video_with_subtitle(self, bv_id: str) -> dict[str, Any]:
        video = await self.get_video_info(bv_id)
        tracks = await self.get_subtitle_tracks(video['bv_id'], video.get('cid'))
        subtitle_content = ''
        subtitle_language = None

        if tracks:
            subtitle_language = tracks[0].lan_doc or tracks[0].lan
            subtitle_content = await self.get_cc_subtitle_content(tracks[0].subtitle_url)

        video.update(
            {
                'has_subtitle': bool(subtitle_content),
                'subtitle_language': subtitle_language,
                'subtitle_content': subtitle_content,
            }
        )
        return video
