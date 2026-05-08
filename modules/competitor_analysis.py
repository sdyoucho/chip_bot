"""
modules/competitor_analysis.py
Perplexity Sonar Pro로 경쟁 채널 주1회 분석.
비용: ₩5,460/인/월
"""

import asyncio
import logging
import os

import aiohttp
import discord
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"


async def run_analysis(streamer_name: str = "all") -> discord.Embed:
    """경쟁 채널 분석 실행 후 Embed 반환."""
    from utils.notion_client import list_streamers

    if streamer_name == "all":
        streamers = await list_streamers()
    else:
        streamers = [{"name": streamer_name}]

    if not streamers:
        from bot.embeds import embed_info
        return embed_info("경쟁 채널 분석", "등록된 스트리머 없음")

    results = []
    for s in streamers[:3]:  # 한 번에 최대 3명 (비용 제어)
        result = await _analyze_one(s["name"])
        results.append(f"**{s['name']}**\n{result}")

    embed = discord.Embed(
        title="🔍 경쟁 채널 분석",
        description="\n\n".join(results),
        color=0x7C3AED,
    )
    embed.set_footer(text="Perplexity Sonar Pro · 주1회 자동 실행")
    return embed


async def _analyze_one(streamer_name: str) -> str:
    """스트리머 1인의 경쟁 채널 트렌드 분석."""
    api_key = os.getenv("PERPLEXITY_API_KEY")
    if not api_key:
        return "PERPLEXITY_API_KEY 미설정"

    prompt = f"""
한국 스트리머 '{streamer_name}'와 비슷한 카테고리의 경쟁 채널 상위 3개를 조사하고,
이번 주 주목할 만한 트렌드나 콘텐츠 변화를 요약해주세요.
각 채널별로 최근 성과나 화제가 된 콘텐츠를 포함해주세요.
한국어로 간결하게 200자 이내로 작성하세요.
"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "sonar-pro",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 400,
    }

    import time
    from utils.pipeline_logger import step
    t = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                PERPLEXITY_API_URL, headers=headers, json=payload
            ) as resp:
                ms = int((time.monotonic() - t) * 1000)
                if resp.status != 200:
                    step("Perplexity HTTP 요청", "fail",
                         f"HTTP {resp.status}", "E006", ms)
                    log.error(f"Perplexity API 오류: {resp.status}")
                    return "API 호출 실패"
                data = await resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                step("Perplexity HTTP 요청", "ok",
                     f"HTTP 200 | {len(content)}자 수신", duration_ms=ms)
                return content
    except Exception as e:
        ms = int((time.monotonic() - t) * 1000)
        step("Perplexity HTTP 요청", "fail", str(e)[:80], "E006", ms)
        log.error(f"경쟁 채널 분석 오류: {e}")
        return f"오류: {e}"
