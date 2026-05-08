"""
modules/planning.py
기쵸 — 기획서·협업 제안서 생성.
OpenRouter standard 티어 (Claude Opus 4.7).
"""

import asyncio
import logging

import discord

from utils.openrouter_client import chat

log = logging.getLogger(__name__)


async def create_document(query: str, streamer_name: str = "") -> discord.Embed:
    from utils.pipeline_logger import step

    step("기쵸(기획 문서) 시작", "ok",
         f"스트리머={streamer_name or '미지정'}, 쿼리={query[:40]}")

    # Notion + 경쟁 분석 병렬 수집
    streamer_ctx, competitor_ctx = await asyncio.gather(
        _get_streamer_context(streamer_name) if streamer_name else asyncio.sleep(0, result=""),
        _get_competitor_context(streamer_name) if streamer_name else asyncio.sleep(0, result=""),
    )

    system_prompt = (
        "당신은 한국 스트리머 매니지먼트 전문가 '기쵸'입니다. "
        "타사에 전달 가능한 수준의 실용적이고 구체적인 협업 기획서를 작성합니다. "
        "구조: [목적] [대상] [타임라인] [예산 개요] [기대효과] 순."
    )
    if streamer_ctx:
        system_prompt += f"\n\n[스트리머 방송 이력]\n{streamer_ctx}"
    if competitor_ctx:
        system_prompt += f"\n\n[경쟁 채널 트렌드]\n{competitor_ctx}"

    try:
        result = await chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
            agent="gihyo",
            max_tokens=1200,
            temperature=0.6,
        )
        embed = discord.Embed(
            title=f"📋 기획 문서{f' — {streamer_name}' if streamer_name else ''}",
            description=result["content"][:3500],
            color=0x4F46E5,
        )
        embed.set_footer(
            text=f"{result['model'].split('/')[-1]} · ${result['cost']:.5f} · 기쵸"
        )
        return embed
    except Exception as e:
        log.error(f"기획 모듈 오류: {e}")
        from bot.embeds import embed_error
        return embed_error("기획 오류", str(e))


async def _get_streamer_context(streamer_name: str) -> str:
    import time
    from utils.pipeline_logger import step
    t = time.monotonic()
    try:
        from utils.notion_client import get_broadcast_logs
        logs = await get_broadcast_logs(streamer_name, days=14)
        ms = int((time.monotonic() - t) * 1000)
        if not logs:
            step("Notion 방송 이력", "ok", "14일 데이터 없음", duration_ms=ms)
            return f"{streamer_name}: 최근 방송 데이터 없음"
        avg_viewers = sum(l["avg"] for l in logs) / len(logs)
        avg_sentiment = sum(l["sentiment"] for l in logs) / len(logs)
        result = (
            f"{streamer_name}: 최근 {len(logs)}회 방송, "
            f"평균 시청자 {avg_viewers:.0f}명, 긍정 감정 {avg_sentiment:.1f}%"
        )
        step("Notion 방송 이력", "ok", result, duration_ms=ms)
        return result
    except Exception as e:
        ms = int((time.monotonic() - t) * 1000)
        step("Notion 방송 이력", "fail", str(e)[:80], "E001", ms)
        return ""


async def _get_competitor_context(streamer_name: str) -> str:
    try:
        from modules.competitor_analysis import _analyze_one
        return await _analyze_one(streamer_name)
    except Exception as e:
        log.warning(f"경쟁 컨텍스트 수집 실패: {e}")
        return ""