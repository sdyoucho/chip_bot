"""
modules/weekly_report.py
분쵸 — 주간 리포트 생성 + APScheduler 주간 자동 발송.
OpenRouter standard 티어 (Claude Opus 4.7).
"""

import asyncio
import logging
from datetime import datetime, timedelta

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from utils.openrouter_client import chat

log = logging.getLogger(__name__)


async def generate_report(streamer_name: str = "all") -> discord.Embed:
    """주간 리포트 생성."""
    from utils.notion_client import list_streamers, get_broadcast_logs
    from utils.pipeline_logger import step

    step("분쵸(주간 리포트) 시작", "ok", f"대상={streamer_name}")

    # 대상 스트리머 선정
    if streamer_name == "all":
        streamers = await list_streamers()
        if not streamers:
            return discord.Embed(
                title="📊 분쵸 — 주간 리포트",
                description="등록된 스트리머 없음",
                color=0x7C3AED,
            )
    else:
        streamers = [{"name": streamer_name}]

    # 각 스트리머 데이터 수집
    sections = []
    for s in streamers[:5]:  # 한 번에 최대 5명
        name = s["name"]
        logs = await get_broadcast_logs(name, days=7)
        if not logs:
            sections.append(f"[{name}]\n최근 7일 데이터 없음")
            continue

        avg_viewers = sum(l["avg"] for l in logs) / len(logs)
        peak_viewers = max(l["peak"] for l in logs)
        total_chats = sum(l["chats"] for l in logs)
        avg_sentiment = sum(l["sentiment"] for l in logs) / len(logs)

        sections.append(
            f"[{name}]\n"
            f"- 방송 횟수: {len(logs)}회\n"
            f"- 평균 시청자: {avg_viewers:.0f}명\n"
            f"- 최고 시청자: {peak_viewers:,}명\n"
            f"- 총 채팅: {total_chats:,}건\n"
            f"- 긍정 감정: {avg_sentiment:.1f}%"
        )

    data_context = "\n\n".join(sections)
    today = datetime.now()
    start = today - timedelta(days=7)

    try:
        result = await chat(
            messages=[
                {"role": "system", "content":
                    "당신은 분쵸(분석 전문가)입니다. 주간 방송 데이터를 분석해 "
                    "실행 가능한 인사이트를 제공합니다.\n"
                    "구조: 1) 주간 하이라이트 2) 개선점 3) 다음 주 액션"},
                {"role": "user", "content":
                    f"{start:%Y-%m-%d} ~ {today:%Y-%m-%d} 주간 데이터:\n\n{data_context}\n\n"
                    "위 데이터를 토대로 주간 리포트를 작성해주세요."},
            ],
            agent="bunchyo",
            tier="standard",
            max_tokens=1200,
            temperature=0.5,
        )

        embed = discord.Embed(
            title=f"📊 분쵸 — 주간 리포트 ({start:%m/%d} ~ {today:%m/%d})",
            description=result["content"][:3500],
            color=0x7C3AED,
        )
        embed.set_footer(
            text=f"{result['model'].split('/')[-1]} · ${result['cost']:.5f} · 분쵸"
        )
        return embed

    except Exception as e:
        log.error(f"주간 리포트 오류: {e}")
        from bot.embeds import embed_error
        return embed_error("주간 리포트 실패", str(e))


# ── APScheduler — 매주 일요일 21시 자동 발송 ────────────────────────
_scheduler: AsyncIOScheduler | None = None


def start_scheduler(bot: discord.Client):
    """주간 리포트 자동 발송 스케줄러 시작."""
    global _scheduler
    if _scheduler and _scheduler.running:
        log.info("주간 리포트 스케줄러 이미 실행 중")
        return

    _scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    async def _auto_weekly():
        try:
            import os
            embed = await generate_report("all")

            # 포럼 채널 우선, 없으면 로그 채널
            forum_id = os.getenv("FORUM_CHANNEL_ID", "").strip()
            if forum_id.isdigit():
                forum = bot.get_channel(int(forum_id))
                if isinstance(forum, discord.ForumChannel):
                    await forum.create_thread(
                        name=f"[분쵸 주간 리포트] {datetime.now():%Y-%m-%d}",
                        embed=embed,
                    )
                    log.info("주간 리포트 포럼 발송 완료")
                    return

            log_ch_id = os.getenv("LOG_RAW_CHANNEL_ID", "").strip()
            if log_ch_id.isdigit():
                ch = bot.get_channel(int(log_ch_id))
                if ch:
                    await ch.send(embed=embed)
                    log.info("주간 리포트 로그 채널 발송 완료")
        except Exception as e:
            log.error(f"자동 주간 리포트 실패: {e}")

    # 매주 일요일 21:00
    _scheduler.add_job(
        _auto_weekly, CronTrigger(day_of_week="sun", hour=21, minute=0),
        id="weekly_report", replace_existing=True,
    )
    _scheduler.start()
    log.info("주간 리포트 스케줄러 등록 (매주 일요일 21:00)")