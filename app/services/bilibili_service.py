import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import get_settings

LOGGER = logging.getLogger(__name__)


class BilibiliAPIError(RuntimeError):
    pass


@dataclass
class SubtitleTrack:
    lan: str
    lan_doc: str
    subtitle_url: str


class BilibiliService:
    VIEW_API = "https://api.bilibili.com/x/web-interface/view"
    PLAYER_API = "https://api.bilibili.com/x/player/v2"

    def __init__(self) -> None:
        self.settings = get_settings()

    def _headers(self, referer: str | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        }
        if referer:
            headers["Referer"] = referer
        return headers

    def _cookies(self) -> dict[str, str] | None:
        if not self.settings.bilibili_sessdata:
            return None
        return {"SESSDATA": self.settings.bilibili_sessdata}

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        referer: str | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers=self._headers(referer),
                    cookies=self._cookies(),
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            LOGGER.exception("B站接口请求失败: %s", url)
            raise BilibiliAPIError(f"B站接口请求失败: {exc}") from exc

        if payload.get("code", 0) != 0:
            message = payload.get("message") or payload.get("msg") or "B站接口返回异常"
            raise BilibiliAPIError(message)
        return payload.get("data") or {}

    async def get_video_info(self, bv_id: str) -> dict[str, Any]:
        data = await self._get_json(
            self.VIEW_API,
            params={"bvid": bv_id},
            referer=f"https://www.bilibili.com/video/{bv_id}",
        )
        pages = data.get("pages") or []
        first_page = pages[0] if pages else {}
        pubdate = data.get("pubdate")
        publish_time = datetime.fromtimestamp(pubdate, tz=timezone.utc) if pubdate else None
        owner = data.get("owner") or {}

        return {
            "bv_id": data.get("bvid") or bv_id,
            "aid": data.get("aid"),
            "cid": first_page.get("cid") or data.get("cid"),
            "title": data.get("title") or "",
            "description": data.get("desc") or "",
            "owner_name": owner.get("name"),
            "owner_mid": owner.get("mid"),
            "publish_time": publish_time,
            "source_url": f"https://www.bilibili.com/video/{data.get('bvid') or bv_id}",
        }

    async def get_subtitle_tracks(self, bv_id: str, cid: int | None) -> list[SubtitleTrack]:
        if not cid:
            return []

        data = await self._get_json(
            self.PLAYER_API,
            params={"bvid": bv_id, "cid": cid},
            referer=f"https://www.bilibili.com/video/{bv_id}",
        )
        subtitle_data = data.get("subtitle") or {}
        tracks = subtitle_data.get("subtitles") or []
        return [
            SubtitleTrack(
                lan=item.get("lan") or "",
                lan_doc=item.get("lan_doc") or "",
                subtitle_url=item.get("subtitle_url") or "",
            )
            for item in tracks
            if item.get("subtitle_url")
        ]

    async def get_cc_subtitle_content(self, subtitle_url: str) -> str:
        if not subtitle_url:
            return ""
        if subtitle_url.startswith("//"):
            subtitle_url = "https:" + subtitle_url
        elif subtitle_url.startswith("/"):
            subtitle_url = "https://api.bilibili.com" + subtitle_url

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(subtitle_url, headers=self._headers())
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            LOGGER.exception("字幕下载失败: %s", subtitle_url)
            raise BilibiliAPIError(f"字幕下载失败: {exc}") from exc

        body = payload.get("body") or []
        lines = [item.get("content", "").strip() for item in body if item.get("content")]
        return "\n".join(line for line in lines if line)

    async def get_video_with_subtitle(self, bv_id: str) -> dict[str, Any]:
        video = await self.get_video_info(bv_id)
        tracks = await self.get_subtitle_tracks(video["bv_id"], video.get("cid"))
        subtitle_content = ""
        subtitle_language = None

        if tracks:
            subtitle_language = tracks[0].lan_doc or tracks[0].lan
            subtitle_content = await self.get_cc_subtitle_content(tracks[0].subtitle_url)

        video.update(
            {
                "has_subtitle": bool(subtitle_content),
                "subtitle_language": subtitle_language,
                "subtitle_content": subtitle_content,
            }
        )
        return video
