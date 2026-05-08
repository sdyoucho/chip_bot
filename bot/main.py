"""
bot/main.py
Discord 봇 진입점. 이벤트 루프, 슬래시 커맨드 로드, 스케줄러 시작.
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

@bot.event
async def on_ready():
    log.info(f"봇 온라인: {bot.user} (ID: {bot.user.id})")

    # 슬래시 커맨드 동기화
    guild_id = os.getenv("DISCORD_GUILD_ID")
    if guild_id:
        guild = discord.Object(id=int(guild_id))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        log.info(f"슬래시 커맨드 동기화 완료 (guild: {guild_id})")
    else:
        await bot.tree.sync()
        log.info("슬래시 커맨드 글로벌 동기화 완료")

    # Raw Data 로그 채널 복원 (env에 저장된 채널 ID 로드)
    raw_ch = os.getenv("LOG_RAW_CHANNEL_ID", "").strip()
    if raw_ch.isdigit():
        from utils.pipeline_logger import set_log_channel
        set_log_channel(int(raw_ch))
        log.info(f"Raw Data 로그 채널 복원: {raw_ch}")

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    # 인쵸: 15분마다 임계치 체크
    from modules.money import check_thresholds, monthly_settlement
    scheduler.add_job(
        check_thresholds, "interval", minutes=15, args=[bot],
        id="money_threshold", replace_existing=True,
    )

    # 인쵸: 매월 말일 23시 월말정산
    async def _auto_settlement():
        from utils.forum_publisher import publish_session
        embed = await monthly_settlement()
        # 포럼이 있으면 포럼에, 없으면 LOG_RAW_CHANNEL에
        forum_id = os.getenv("FORUM_CHANNEL_ID", "").strip()
        if forum_id.isdigit():
            forum = bot.get_channel(int(forum_id))
            if isinstance(forum, discord.ForumChannel):
                await forum.create_thread(name=f"[월말정산] {datetime.now():%Y-%m}", embed=embed)

    scheduler.add_job(
        _auto_settlement, CronTrigger(day="last", hour=23, minute=0),
        id="monthly_settlement", replace_existing=True,
    )

    # 분쵸: 매주 월요일 9시 경쟁 분석
    from modules.competitor_analysis import run_analysis
    scheduler.add_job(
        run_analysis, CronTrigger(day_of_week="mon", hour=9, minute=0),
        id="weekly_competitor", replace_existing=True,
    )

    scheduler.start()
    log.info("스케줄러 가동: 임계치(15분) + 월말정산 + 분쵸 주1회")

    # 스케줄러 시작
    from modules.weekly_report import start_scheduler
    start_scheduler(bot)
    log.info("주간 리포트 스케줄러 시작")


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
