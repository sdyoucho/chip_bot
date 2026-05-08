"""
bot/main.py
Discord 봇 진입점.
"""

import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from bot.commands import setup_commands
from utils.logger import setup_logger

load_dotenv()
setup_logger()
log = logging.getLogger(__name__)

# ── 인텐트 설정 ──────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 중복 sync 방지 플래그
_synced = False


@bot.event
async def on_ready():
    global _synced
    log.info(f"봇 온라인: {bot.user} (ID: {bot.user.id})")

    # ── 슬래시 커맨드 동기화 (실패해도 봇 동작 유지) ─────────────
    if not _synced:
        try:
            guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()
            if guild_id.isdigit():
                guild = discord.Object(id=int(guild_id))
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                log.info(f"✅ 슬래시 커맨드 {len(synced)}개 동기화 완료 (guild: {guild_id})")
            else:
                synced = await bot.tree.sync()
                log.info(f"✅ 슬래시 커맨드 {len(synced)}개 글로벌 동기화 완료 (최대 1시간 반영)")
            _synced = True
        except discord.Forbidden as e:
            log.error(
                "❌ 슬래시 커맨드 동기화 실패 (403 Forbidden / Missing Access)\n"
                "   → 봇 초대 시 'applications.commands' 스코프가 빠졌을 가능성이 높습니다.\n"
                "   → https://discord.com/api/oauth2/authorize?"
                f"client_id={bot.user.id}&permissions=8&scope=bot+applications.commands\n"
                "   위 URL로 봇을 재초대해주세요."
            )
        except Exception as e:
            log.exception(f"❌ 슬래시 커맨드 동기화 중 예기치 않은 오류: {e}")

    # ── Raw Data 로그 채널 복원 ─────────────────────────────────
    try:
        raw_ch = os.getenv("LOG_RAW_CHANNEL_ID", "").strip()
        if raw_ch.isdigit():
            from utils.pipeline_logger import set_log_channel
            set_log_channel(int(raw_ch))
            log.info(f"Raw Data 로그 채널 복원: {raw_ch}")
    except Exception as e:
        log.warning(f"Raw Data 채널 복원 실패: {e}")

    # ── 주간 리포트 스케줄러 ─────────────────────────────────────
    try:
        from modules.weekly_report import start_scheduler
        start_scheduler(bot)
        log.info("주간 리포트 스케줄러 시작")
    except Exception as e:
        log.warning(f"주간 리포트 스케줄러 시작 실패: {e}")

    # ── 인쵸 임계치 모니터링 + 월말정산 스케줄러 ────────────────
    try:
        _start_money_scheduler(bot)
        log.info("인쵸 스케줄러 시작 (15분 임계치 + 월말정산)")
    except Exception as e:
        log.warning(f"인쵸 스케줄러 시작 실패: {e}")


def _start_money_scheduler(bot_instance):
    """APScheduler로 인쵸 자동화."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from modules.money import check_thresholds, monthly_settlement
    from datetime import datetime

    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    # 15분마다 크레딧 임계치 체크
    scheduler.add_job(
        check_thresholds, "interval", minutes=15, args=[bot_instance],
        id="money_threshold", replace_existing=True,
    )

    # 매월 말일 23시 월말정산
    async def _auto_settlement():
        try:
            embed = await monthly_settlement()
            forum_id = os.getenv("FORUM_CHANNEL_ID", "").strip()
            if forum_id.isdigit():
                forum = bot_instance.get_channel(int(forum_id))
                if isinstance(forum, discord.ForumChannel):
                    await forum.create_thread(
                        name=f"[월말정산] {datetime.now():%Y-%m}",
                        embed=embed,
                    )
                    return
            # 포럼 없으면 log 채널에 발송
            log_ch_id = os.getenv("LOG_RAW_CHANNEL_ID", "").strip()
            if log_ch_id.isdigit():
                ch = bot_instance.get_channel(int(log_ch_id))
                if ch:
                    await ch.send(embed=embed)
        except Exception as e:
            log.error(f"자동 월말정산 실패: {e}")

    scheduler.add_job(
        _auto_settlement, CronTrigger(day="last", hour=23, minute=0),
        id="monthly_settlement", replace_existing=True,
    )

    # 매주 월요일 9시 분쵸 경쟁 분석
    async def _weekly_competitor():
        try:
            from modules.competitor_analysis import run_analysis
            embed = await run_analysis()
            forum_id = os.getenv("FORUM_CHANNEL_ID", "").strip()
            if forum_id.isdigit():
                forum = bot_instance.get_channel(int(forum_id))
                if isinstance(forum, discord.ForumChannel):
                    await forum.create_thread(
                        name=f"[분쵸 주간 경쟁분석] {datetime.now():%Y-%m-%d}",
                        embed=embed,
                    )
        except Exception as e:
            log.error(f"주간 경쟁분석 실패: {e}")

    scheduler.add_job(
        _weekly_competitor, CronTrigger(day_of_week="mon", hour=9, minute=0),
        id="weekly_competitor", replace_existing=True,
    )

    scheduler.start()


@bot.event
async def on_error(event, *args, **kwargs):
    log.exception(f"이벤트 오류: {event}")


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise ValueError(".env에 DISCORD_TOKEN이 없습니다")

    await setup_commands(bot)

    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())