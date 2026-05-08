"""
modules/content_suggest.py
Claude Sonnet으로 영상 콘텐츠 개선 제안.
제목·썸네일·구성 개선안 생성.
비용: ₩8,646/인/월

AI 오케스트레이션:
  Perplexity (경쟁 채널 트렌드) → Claude Sonnet (종합 제안)
"""

import asyncio
import logging
import os

import anthropic
import discord
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)


def _get_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


async def generate_suggestions(query: str = "", streamer_name: str = "") -> discord.Embed:
    """콘텐츠 개선 제안 생성. Perplexity 경쟁 분석 → Claude 종합."""
    from utils.pipeline_logger import step, traced

    step("기쵸(콘텐츠 제안) 시작", "ok", f"스트리머={streamer_name or '미지정'}, 쿼리={query[:40] if query else '기본'}")

    # 두 데이터 소스를 병렬로 수집
    performance_ctx, competitor_ctx = await asyncio.gather(
        _get_performance_context(streamer_name),
        _get_competitor_context(streamer_name),
    )

    prompt = f"""
당신은 한국 유튜브/스트리밍 콘텐츠 전문가입니다.

스트리머: {streamer_name or '(미지정)'}
요청: {query or '최신 영상 개선 제안'}

[내부 성과 데이터 — Notion]
{performance_ctx}

[경쟁 채널 트렌드 — Perplexity 실시간 분석]
{competitor_ctx}

위 두 데이터를 종합해 다음 3가지 측면에서 구체적인 개선안을 제시하세요:
1. 제목 개선: 현재 제목의 문제점과 더 나은 제목 예시 (경쟁 채널 트렌드 반영)
2. 썸네일 개선: 클릭률을 높이기 위한 썸네일 구성 제안
3. 콘텐츠 구성: 시청 지속시간을 늘리기 위한 구조 개선 (경쟁사 대비 차별화 포함)

각 항목은 2~3줄로 간결하게 작성하세요.
"""

    try:
        client = _get_client()
        message = await traced(
            "Claude Sonnet — 콘텐츠 제안 생성",
            client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=700,
                messages=[{"role": "user", "content": prompt}],
            ),
            error_code="E004",
        )
        content = message.content[0].text
        step("응답 파싱", "ok", f"{len(content)}자 수신")

        embed = discord.Embed(
            title=f"✨ 콘텐츠 개선 제안 — {streamer_name or '전체'}",
            description=content,
            color=0x9333EA,
        )
        embed.set_footer(text="Claude Sonnet · Perplexity 경쟁 분석 + Notion 데이터 종합")
        return embed

    except Exception as e:
        log.error(f"콘텐츠 제안 오류: {e}")
        from bot.embeds import embed_error
        return embed_error("제안 생성 실패", str(e))


async def _get_performance_context(streamer_name: str) -> str:
    """Notion에서 최근 성과 데이터 요약."""
    import time
    from utils.pipeline_logger import step
    if not streamer_name:
        step("Notion 성과 조회", "skip", "스트리머 미지정")
        return "성과 데이터 없음"
    t = time.monotonic()
    try:
        from utils.notion_client import get_broadcast_logs
        logs = await get_broadcast_logs(streamer_name, days=14)
        ms = int((time.monotonic() - t) * 1000)
        if not logs:
            step("Notion 성과 조회", "ok", "최근 14일 데이터 없음", duration_ms=ms)
            return "최근 2주 방송 데이터 없음"
        avg_sentiment = sum(l["sentiment"] for l in logs) / len(logs)
        avg_viewers = sum(l["avg"] for l in logs) / len(logs)
        result = f"평균 시청자 {avg_viewers:.0f}명, 긍정 감정 {avg_sentiment:.1f}%, {len(logs)}회 방송"
        step("Notion 성과 조회", "ok", result, duration_ms=ms)
        return result
    except Exception as e:
        ms = int((time.monotonic() - t) * 1000)
        step("Notion 성과 조회", "fail", str(e)[:80], "E001", ms)
        return "데이터 조회 실패"


async def _get_competitor_context(streamer_name: str) -> str:
    """Perplexity로 경쟁 채널 트렌드 수집 (AI-to-AI: Perplexity → Claude)."""
    import time
    from utils.pipeline_logger import step
    if not streamer_name:
        step("Perplexity 경쟁 분석", "skip", "스트리머 미지정")
        return "경쟁 채널 데이터 없음"
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
        log.warning(f"경쟁 채널 컨텍스트 수집 실패: {e}")
        return "경쟁 채널 데이터 수집 실패"
