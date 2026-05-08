"""
modules/youtube_analytics.py
분쵸 — 유튜브 채널 통계 조회.
YouTube Data API v3 사용 (LLM 호출 없음).
"""

import logging
import os

import aiohttp
import discord

log = logging.getLogger(__name__)

YT_API_BASE = "https://www.googleapis.com/youtube/v3"


async def get_channel_stats(streamer_name: str = "all") -> discord.Embed:
    """등록된 스트리머의 YouTube 채널 통계."""
    from utils.notion_client import list_streamers

    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        return discord.Embed(
            title="📺 분쵸 — YouTube 통계",
            description="⚠️ `YOUTUBE_API_KEY` 미설정.\n`/config_ai`로 설정해주세요.",
            color=0xEAB308,
        )

    if streamer_name == "all":
        streamers = await list_streamers()
    else:
        streamers = [{"name": streamer_name}]

    if not streamers:
        return discord.Embed(
            title="📺 분쵸 — YouTube 통계",
            description="등록된 스트리머 없음",
            color=0x7C3AED,
        )

    embed = discord.Embed(
        title="📺 분쵸 — YouTube 채널 통계",
        color=0xFF0000,
    )

    for s in streamers[:5]:
        yt_url = s.get("youtube_url", "")
        if not yt_url:
            embed.add_field(
                name=f"❌ {s['name']}",
                value="YouTube URL 미등록",
                inline=False,
            )
            continue

        channel_id = _extract_channel_id(yt_url)
        if not channel_id:
            embed.add_field(
                name=f"⚠️ {s['name']}",
                value=f"URL 파싱 실패: {yt_url}",
                inline=False,
            )
            continue

        stats = await _fetch_channel_stats(api_key, channel_id)
        if stats:
            embed.add_field(
                name=f"📺 {s['name']}",
                value=(
                    f"구독자: {stats['subs']:,}\n"
                    f"조회수: {stats['views']:,}\n"
                    f"영상 수: {stats['videos']:,}"
                ),
                inline=True,
            )
        else:
            embed.add_field(
                name=f"❌ {s['name']}",
                value="통계 조회 실패",
                inline=False,
            )

    embed.set_footer(text="YouTube Data API v3 · 분쵸")
    return embed


def _extract_channel_id(url: str) -> str | None:
    """YouTube URL에서 채널 ID 추출 (단순 파서)."""
    if "/channel/" in url:
        return url.split("/channel/")[-1].split("/")[0].split("?")[0]
    # @handle, /c/, /user/ 등은 별도 API 호출 필요 — 생략
    return None


async def _fetch_channel_stats(api_key: str, channel_id: str) -> dict | None:
    url = f"{YT_API_BASE}/channels?part=statistics&id={channel_id}&key={api_key}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                items = data.get("items", [])
                if not items:
                    return None
                s = items[0]["statistics"]
                return {
                    "subs": int(s.get("subscriberCount", 0)),
                    "views": int(s.get("viewCount", 0)),
                    "videos": int(s.get("videoCount", 0)),
                }
    except Exception as e:
        log.error(f"YouTube API 오류: {e}")
        return None