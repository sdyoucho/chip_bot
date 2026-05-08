"""
utils/viewer_tracker.py
플랫폼별 시청자 수 5분 polling → Notion 저장 → 급상승 감지.
"""

import asyncio
import logging
import os
import time
from typing import Callable

import aiohttp
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv("VIEWER_POLL_INTERVAL_SECONDS", "300"))
SPIKE_THRESHOLD = 0.3   # 30% 이상 급등 시 알림

# {streamer_name: [viewer_count, ...]} 최근 기록
_history: dict[str, list[int]] = {}


async def start_tracking(streamer_name: str, platform: str, channel_id: str, bot=None):
    """시청자 수 트래킹 시작."""
    _history[streamer_name] = []
    asyncio.create_task(_poll_loop(streamer_name, platform, channel_id, bot))
    log.info(f"시청자 트래킹 시작: {streamer_name} ({platform})")


async def _poll_loop(streamer_name: str, platform: str, channel_id: str, bot=None):
    """주기적으로 시청자 수 조회."""
    while True:
        try:
            count = await _fetch_viewer_count(platform, channel_id)
            if count is not None:
                hist = _history.setdefault(streamer_name, [])
                hist.append(count)
                if len(hist) > 100:
                    hist.pop(0)

                # 급상승 감지
                if bot and len(hist) >= 3:
                    prev = hist[-2]
                    if prev > 0 and (count - prev) / prev >= SPIKE_THRESHOLD:
                        await _send_spike_alert(bot, streamer_name, prev, count)

        except Exception as e:
            log.debug(f"시청자 트래킹 오류 ({streamer_name}): {e}")

        await asyncio.sleep(POLL_INTERVAL)


async def _fetch_viewer_count(platform: str, channel_id: str) -> int | None:
    """플랫폼별 시청자 수 조회."""
    try:
        if platform == "치지직":
            return await _chzzk_viewers(channel_id)
        elif platform == "유튜브":
            return await _youtube_viewers(channel_id)
        # SOOP은 Phase 6에서 추가
    except Exception as e:
        log.debug(f"시청자 조회 실패 ({platform}): {e}")
    return None


async def _chzzk_viewers(channel_id: str) -> int | None:
    """치지직 시청자 수 (비공식 API)."""
    url = f"https://api.chzzk.naver.com/service/v2/channels/{channel_id}/live-detail"
    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("content", {}).get("concurrentUserCount", 0)
    return None


async def _youtube_viewers(video_id: str) -> int | None:
    """YouTube 라이브 동시 시청자 수 (공개 API)."""
    api_key = os.getenv("YOUTUBE_API_KEY")
    url = (
        f"https://www.googleapis.com/youtube/v3/videos"
        f"?part=liveStreamingDetails&id={video_id}&key={api_key}"
    )
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                items = data.get("items", [])
                if items:
                    details = items[0].get("liveStreamingDetails", {})
                    count = details.get("concurrentViewers", "0")
                    return int(count)
    return None


async def _send_spike_alert(bot, streamer_name: str, prev: int, curr: int):
    """시청자 급상승 알림 발송."""
    cho_id = int(os.getenv("CHO_USER_ID", "0"))
    if not cho_id:
        return
    import discord
    change = ((curr - prev) / prev * 100)
    try:
        user = await bot.fetch_user(cho_id)
        embed = discord.Embed(
            title=f"📈 시청자 급상승 — {streamer_name}",
            description=f"{prev:,}명 → **{curr:,}명** (+{change:.1f}%)",
            color=0x059669,
        )
        await user.send(embed=embed)
    except Exception as e:
        log.error(f"급상승 알림 실패: {e}")


def get_current_viewers(streamer_name: str) -> int:
    hist = _history.get(streamer_name, [])
    return hist[-1] if hist else 0
