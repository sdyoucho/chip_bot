"""
modules/weekly_report.py
주간 리포트 자동 생성 + 스케줄러.
매주 월요일 오전 9시 Cho에게 Discord DM 발송.

AI 오케스트레이션:
  Gemini Flash (채팅 감정 분석) + Perplexity (경쟁 트렌드)
  → Claude Sonnet (종합 개선 제안 생성)
"""

import logging
import os
from datetime import datetime, timedelta

import discord
import google.generativeai as genai
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
_model = genai.GenerativeModel("gemini-2.0-flash-exp")

_scheduler: AsyncIOScheduler | None = None


def start_scheduler(bot: discord.Client):
    """APScheduler로 주간 리포트 자동 실행 등록."""
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    day = int(os.getenv("WEEKLY_REPORT_DAY", "0"))   # 0 = 월요일
    hour = int(os.getenv("WEEKLY_REPORT_HOUR", "9"))

    _scheduler.add_job(
        _run_all_reports,
        "cron",
        day_of_week=day,
        hour=hour,
        minute=0,
        args=[bot],
        id="weekly_report",
    )
    _scheduler.start()
    log.info(f"주간 리포트 스케줄: 매주 {['월','화','수','목','금','토','일'][day]}요일 {hour:02d}:00")


async def _run_all_reports(bot: discord.Client):
    """모든 스트리머 주간 리포트 생성 후 Cho에게 발송."""
    from utils.notion_client import list_streamers

    streamers = await list_streamers()
    if not streamers:
        log.info("등록된 스트리머 없음 — 리포트 생략")
        return

    cho_id = int(os.getenv("CHO_USER_ID", "0"))
    if not cho_id:
        log.warning("CHO_USER_ID 미설정")
        return

    try:
        cho_user = await bot.fetch_user(cho_id)
    except Exception as e:
        log.error(f"Cho 사용자 조회 실패: {e}")
        return

    for streamer in streamers:
        try:
            embed = await generate_report(streamer["name"])
            await cho_user.send(embed=embed)
            log.info(f"주간 리포트 발송: {streamer['name']}")
        except Exception as e:
            log.error(f"리포트 발송 실패 ({streamer['name']}): {e}")


async def generate_report(streamer_name: str) -> discord.Embed:
    """
    스트리머 리포트 생성.
    streamer_name == "all" 이면 전체 스트리머 통합 요약 반환.
    """
    # "all" 처리 — 모든 스트리머 순회 후 통합 Embed
    if streamer_name == "all":
        from utils.notion_client import list_streamers
        streamers = await list_streamers()
        if not streamers:
            from bot.embeds import embed_info
            return embed_info("주간 리포트", "등록된 스트리머가 없습니다.")

        embeds_text = []
        for s in streamers:
            e = await generate_report(s["name"])
            # Embed 내용을 텍스트로 추출해서 통합
            lines = [f"**{s['name']}**"]
            for field in e.fields:
                lines.append(f"{field.name}: {field.value}")
            embeds_text.append("\n".join(lines))

        embed = discord.Embed(
            title="📊 전체 스트리머 주간 요약",
            description="\n\n".join(embeds_text)[:4000],
            color=0x4F46E5,
        )
        embed.set_footer(text="Cho's 매니지먼트 봇 | 자동 생성")
        return embed

    import asyncio

    period_end = datetime.now().date()
    period_start = period_end - timedelta(days=7)
    period_str = f"{period_start.strftime('%m/%d')} ~ {period_end.strftime('%m/%d')}"

    # Notion에서 방송 로그 수집
    from utils.notion_client import get_broadcast_logs
    logs = await get_broadcast_logs(streamer_name, days=7)

    broadcast_summary = _summarize_broadcast_logs(logs)

    # YouTube 통계 (인증된 경우)
    youtube_summary = ""
    from modules.youtube_auth import is_authenticated
    if is_authenticated(streamer_name):
        try:
            from modules.youtube_analytics import _fetch_stats
            yt_embed = await asyncio.get_running_loop().run_in_executor(None, lambda: _fetch_stats(streamer_name))
            youtube_summary = _extract_youtube_text(yt_embed)
        except Exception as e:
            log.warning(f"YouTube 통계 수집 실패 ({streamer_name}): {e}")
            youtube_summary = "YouTube 데이터 수집 실패"

    # Perplexity 경쟁 분석 수집 (Claude가 종합에 사용)
    from utils.pipeline_logger import step
    import time as _time
    competitor_summary = ""
    _t = _time.monotonic()
    try:
        from modules.competitor_analysis import _analyze_one
        competitor_summary = await _analyze_one(streamer_name)
        step("Perplexity 경쟁 분석 (주간)", "ok",
             f"{len(competitor_summary)}자",
             duration_ms=int((_time.monotonic() - _t) * 1000))
    except Exception as e:
        step("Perplexity 경쟁 분석 (주간)", "fail", str(e)[:80], "E006",
             int((_time.monotonic() - _t) * 1000))
        log.warning(f"경쟁 채널 분석 수집 실패 ({streamer_name}): {e}")

    # Claude Sonnet으로 Gemini 분석 + Perplexity 결과 종합 요약
    summary_text = await _generate_ai_summary(
        streamer_name, broadcast_summary, youtube_summary, competitor_summary
    )

    # Embed 생성
    from bot.embeds import embed_report
    return embed_report(
        streamer_name=streamer_name,
        period=period_str,
        broadcast_summary=broadcast_summary or "이번 주 방송 데이터 없음",
        youtube_summary=youtube_summary or "YouTube 데이터 없음",
        competitor_summary=competitor_summary or "경쟁 채널 데이터 없음",
        suggestion=summary_text,
    )


def _summarize_broadcast_logs(logs: list[dict]) -> str:
    if not logs:
        return ""
    total_days = len(logs)
    avg_peak = sum(l["peak"] for l in logs) // total_days if total_days else 0
    avg_chats = sum(l["chats"] for l in logs) // total_days if total_days else 0
    avg_sentiment = sum(l["sentiment"] for l in logs) / total_days if total_days else 0
    return (
        f"방송 {total_days}일 · 평균 최고 시청자 {avg_peak:,}명\n"
        f"평균 채팅 {avg_chats:,}개 · 긍정 감정 {avg_sentiment:.1f}%"
    )


def _extract_youtube_text(embed: discord.Embed) -> str:
    parts = []
    for field in embed.fields:
        parts.append(f"{field.name}: {field.value}")
    return "\n".join(parts)


async def _generate_ai_summary(
    streamer_name: str,
    broadcast_summary: str,
    youtube_summary: str,
    competitor_summary: str = "",
) -> str:
    """
    Claude Sonnet으로 종합 개선 제안 생성.
    AI-to-AI: Gemini(채팅 감정) + Perplexity(경쟁 트렌드) → Claude(최종 종합)
    """
    import anthropic

    prompt = f"""
스트리머 '{streamer_name}'의 이번 주 데이터를 분석해 핵심 개선 제안 3가지를 작성하세요.

[방송 성과 — Gemini Flash 감정 분석 결과]
{broadcast_summary or '데이터 없음'}

[YouTube 통계]
{youtube_summary or '데이터 없음'}

[경쟁 채널 트렌드 — Perplexity 실시간 분석]
{competitor_summary or '데이터 없음'}

세 데이터를 종합해 실행 가능한 개선 제안 3가지를 번호를 붙여 각 한 줄로 작성하세요.
경쟁사 대비 차별화 포인트를 최소 1개 포함하세요.
"""
    from utils.pipeline_logger import step, traced
    try:
        client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        message = await traced(
            "Claude Sonnet — 주간 리포트 종합",
            client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            ),
            error_code="E004",
        )
        result = message.content[0].text.strip()
        step("응답 파싱 (주간 리포트)", "ok", f"{len(result)}자")
        return result
    except Exception as e:
        log.error(f"Claude 종합 요약 실패, Gemini 폴백: {e}")
        # Gemini Flash로 폴백
        try:
            import asyncio
            _t2 = __import__("time").monotonic()
            response = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: _model.generate_content(
                    f"스트리머 '{streamer_name}' 이번 주 개선 제안 3가지:\n"
                    f"방송: {broadcast_summary}\nYouTube: {youtube_summary}\n"
                    "번호 붙여 3줄로만."
                ),
            )
            step("Gemini Flash — 폴백 요약", "ok",
                 "Claude 실패 후 폴백",
                 duration_ms=int((__import__("time").monotonic() - _t2) * 1000))
            return response.text.strip()
        except Exception as e2:
            step("Gemini Flash — 폴백 요약", "fail", str(e2)[:80], "E003")
            log.error(f"Gemini 폴백도 실패: {e2}")
            return "AI 요약 생성 실패"
