"""
modules/planning.py
기획서·제안서 문서 생성 — Claude Sonnet.
스트리머 컨텍스트를 Notion에서 읽어서 반영.

AI 오케스트레이션:
  Notion (방송 이력) + Perplexity (경쟁 트렌드) → Claude Sonnet (기획 문서)
"""

import asyncio
import logging
import os

import anthropic
import discord
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)


async def create_document(query: str, streamer_name: str = "") -> discord.Embed:
    """기획 문서 생성. Notion + Perplexity 데이터를 Claude가 종합."""
    from utils.pipeline_logger import step, traced
    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    step("기쵸(기획 문서) 시작", "ok", f"스트리머={streamer_name or '미지정'}, 쿼리={query[:40]}")

    # Notion 방송 이력과 Perplexity 경쟁 분석을 병렬 수집
    streamer_ctx, competitor_ctx = await asyncio.gather(
        _get_streamer_context(streamer_name) if streamer_name else asyncio.sleep(0, result=""),
        _get_competitor_context(streamer_name) if streamer_name else asyncio.sleep(0, result=""),
    )

    system_prompt = (
        "당신은 한국 스트리머 매니지먼트 전문가입니다. "
        "실용적이고 구체적인 기획 문서를 작성합니다."
    )
    if streamer_ctx:
        system_prompt += f"\n\n[스트리머 방송 이력 — Notion]\n{streamer_ctx}"
    if competitor_ctx:
        system_prompt += f"\n\n[경쟁 채널 트렌드 — Perplexity 실시간]\n{competitor_ctx}"

    try:
        message = await traced(
            "Claude Sonnet — 기획 문서 생성",
            client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=900,
                system=system_prompt,
                messages=[{"role": "user", "content": query}],
            ),
            error_code="E004",
        )
        content = message.content[0].text
        step("응답 파싱", "ok", f"{len(content)}자 수신")
        embed = discord.Embed(
            title=f"📋 기획 문서{f' — {streamer_name}' if streamer_name else ''}",
            description=content[:2000],
            color=0x4F46E5,
        )
        embed.set_footer(text="Claude Sonnet · Notion + Perplexity 경쟁 분석 종합")
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
            step("Notion 방송 이력 조회", "ok", "14일 데이터 없음", duration_ms=ms)
            return f"{streamer_name}: 최근 방송 데이터 없음"
        avg_viewers = sum(l["avg"] for l in logs) / len(logs)
        avg_sentiment = sum(l["sentiment"] for l in logs) / len(logs)
        result = (
            f"{streamer_name}: 최근 {len(logs)}회 방송, "
            f"평균 시청자 {avg_viewers:.0f}명, "
            f"긍정 감정 {avg_sentiment:.1f}%"
        )
        step("Notion 방송 이력 조회", "ok", result, duration_ms=ms)
        return result
    except Exception as e:
        ms = int((time.monotonic() - t) * 1000)
        step("Notion 방송 이력 조회", "fail", str(e)[:80], "E001", ms)
        return ""


async def _get_competitor_context(streamer_name: str) -> str:
    """Perplexity로 경쟁 채널 트렌드 수집 (AI-to-AI: Perplexity → Claude)."""
    import time
    from utils.pipeline_logger import step
    t = time.monotonic()
    try:
        from modules.competitor_analysis import _analyze_one
        result = await _analyze_one(streamer_name)
        ms = int((time.monotonic() - t) * 1000)
        step("Perplexity 경쟁 분석", "ok", f"{len(result)}자 수신", duration_ms=ms)
        return result
    except Exception as e:
        ms = int((time.monotonic() - t) * 1000)
        step("Perplexity 경쟁 분석", "fail", str(e)[:80], "E006", ms)
        log.warning(f"기획 모듈 경쟁 컨텍스트 수집 실패: {e}")
        return ""
