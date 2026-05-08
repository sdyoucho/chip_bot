"""
modules/content_suggest.py
기쵸 — 콘텐츠 개선 제안.
OpenRouter standard 티어 (Claude Opus 4.7).
"""

import asyncio
import logging

import discord

from utils.openrouter_client import chat

log = logging.getLogger(__name__)


async def generate_suggestions(query: str = "", streamer_name: str = "") -> discord.Embed:
    from utils.pipeline_logger import step

    step("기쵸(콘텐츠 제안) 시작", "ok",
         f"스트리머={streamer_name or '미지정'}")

    performance_ctx, competitor_ctx = await asyncio.gather(
        _get_performance_context(streamer_name),
        _get_competitor_context(streamer_name),
    )

    prompt = f"""당신은 한국 유튜브/스트리밍 콘텐츠 전문가입니다.

스트리머: {streamer_name or '(미지정)'}
요청: {query or '최신 영상 개선 제안'}

[내부 성과 데이터]
{performance_ctx}

[경쟁 채널 트렌드]
{competitor_ctx}

다음 3가지 측면에서 구체적인 개선안을 제시하세요:
1. 제목 개선: 현재 문제점과 대안 제시
2. 썸네일 개선: 클릭률을 높이는 구성
3. 콘텐츠 구성: 시청 지속시간 개선 (경쟁사 대비 차별화)

각 항목 2~3줄로 간결하게."""

    try:
        result = await chat(
            messages=[{"role": "user", "content": prompt}],
            agent="gihyo",
            max_tokens=900,
            temperature=0.7,
        )
        embed = discord.Embed(
            title=f"✨ 콘텐츠 개선 제안 — {streamer_name or '전체'}",
            description=result["content"][:3500],
            color=0x9333EA,
        )
        embed.set_footer(
            text=f"{result['model'].split('/')[-1]} · ${result['cost']:.5f} · 기쵸"
        )
        return embed
    except Exception as e:
        log.error(f"콘텐츠 제안 오류: {e}")
        from bot.embeds import embed_error
        return embed_error("제안 생성 실패", str(e))


async def _get_performance_context(streamer_name: str) -> str:
    if not streamer_name:
        return "스트리머 미지정"
    try:
        from utils.notion_client import get_broadcast_logs
        logs = await get_broadcast_logs(streamer_name, days=14)
        if not logs:
            return "최근 2주 데이터 없음"
        avg_sentiment = sum(l["sentiment"] for l in logs) / len(logs)
        avg_viewers = sum(l["avg"] for l in logs) / len(logs)
        return (
            f"평균 시청자 {avg_viewers:.0f}명, "
            f"긍정 감정 {avg_sentiment:.1f}%, {len(logs)}회 방송"
        )
    except Exception as e:
        log.warning(f"성과 컨텍스트 수집 실패: {e}")
        return "데이터 조회 실패"


async def _get_competitor_context(streamer_name: str) -> str:
    if not streamer_name:
        return "경쟁 채널 데이터 없음"
    try:
        from modules.competitor_analysis import _analyze_one
        return await _analyze_one(streamer_name)
    except Exception as e:
        log.warning(f"경쟁 컨텍스트 수집 실패: {e}")
        return "경쟁 데이터 수집 실패"