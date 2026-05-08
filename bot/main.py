"""
bot/main.py
Discord 봇 진입점.
Guild 자동 감지 — 봇이 참여한 모든 서버에 슬래시 커맨드 즉시 등록.
"""

import asyncio
import logging
import os
from datetime import datetime

import discord
from discord.ext import commands
from dotenv import load_dotenv

from bot.commands import setup_commands
from utils.logger import setup_logger

load_dotenv()
setup_logger()
log = logging.getLogger(__name__)

# ── 인텐트 ──────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True          # 멤버 정보 접근 (서버 Members Intent 필요)

bot = commands.Bot(command_prefix="!", intents=intents)

# 중복 sync 방지
_synced_guilds: set[int] = set()


@bot.event
async def on_ready():
    log.info(f"봇 온라인: {bot.user} (ID: {bot.user.id})")
    log.info(f"참여 중인 서버: {len(bot.guilds)}개")

    # ── 모든 참여 서버에 슬래시 커맨드 동기화 ───────────────────
    await _sync_all_guilds()

    # ── Raw Data 로그 채널 복원 ─────────────────────────────────
    try:
        raw_ch = os.getenv("LOG_RAW_CHANNEL_ID", "").strip()
        if raw_ch.isdigit():
            from utils.pipeline_logger import set_log_channel
            set_log_channel(int(raw_ch))
            log.info(f"Raw Data 로그 채널 복원: {raw_ch}")
    except Exception as e:
        log.warning(f"Raw Data 채널 복원 실패: {e}")

    # ── 주간 리포트 + 인쵸 스케줄러 ─────────────────────────────
    try:
        from modules.weekly_report import start_scheduler
        start_scheduler(bot)
        log.info("주간 리포트 스케줄러 시작")
    except Exception as e:
        log.warning(f"주간 리포트 스케줄러 시작 실패: {e}")

    try:
        _start_money_scheduler(bot)
        log.info("인쵸 스케줄러 시작 (15분 임계치 + 월말정산)")
    except Exception as e:
        log.warning(f"인쵸 스케줄러 시작 실패: {e}")

    # ── 고정비 납부 알림 (매일 9시) ─────────────────────────────
    try:
        _start_fixed_costs_scheduler(bot)
        log.info("고정비 납부 알림 스케줄러 시작 (매일 09:00)")
    except Exception as e:
        log.warning(f"고정비 스케줄러 시작 실패: {e}")

    # ── 자동 재부팅 (매일 04:00) ────────────────────────────────
    try:
        from utils.restart_manager import setup_auto_restart
        setup_auto_restart(bot, hour=4, minute=0)
    except Exception as e:
        log.warning(f"자동 재부팅 스케줄 실패: {e}")

        # ── 개쵸: R&D 자동화 스케줄러 ─────────────────────────────────
    try:
        _start_rnd_scheduler(bot)
        log.info("개쵸 R&D 스케줄러 시작")
    except Exception as e:
        log.warning(f"개쵸 스케줄러 시작 실패: {e}")

    # ── 배포 알림 (R&D 채널에) ────────────────────────────────────
    try:
        from modules.rnd import notify_update
        asyncio.create_task(notify_update(
            bot,
            version=datetime.now().strftime("build-%Y%m%d-%H%M"),
            changes=[
                "개쵸 R&D 확장 (헬스체크/진단/설계서/공지)",
                "고정비 납부 관리 (매일 09:00 알림)",
                "스케줄 CRUD 커맨드",
                "자동 재부팅 (매일 04:00)",
                "/ask 응답 누락 버그 수정",
            ],
        ))
    except Exception as e:
        log.warning(f"배포 알림 실패: {e}")       


async def _sync_all_guilds():
    """참여 중인 모든 서버에 슬래시 커맨드 동기화 (즉시 반영)."""
    total = 0
    for guild in bot.guilds:
        if guild.id in _synced_guilds:
            continue
        try:
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            _synced_guilds.add(guild.id)
            total += len(synced)
            log.info(f"✅ [{guild.name}] 슬래시 커맨드 {len(synced)}개 동기화")
        except discord.Forbidden:
            log.error(
                f"❌ [{guild.name}] 동기화 실패 (403 Missing Access)\n"
                f"   → 이 서버에서 봇이 'applications.commands' 스코프 없이 초대됨.\n"
                f"   → 재초대 URL: https://discord.com/api/oauth2/authorize?"
                f"client_id={bot.user.id}&permissions=8&integration_type=0"
                f"&scope=bot+applications.commands"
            )
        except Exception as e:
            log.exception(f"❌ [{guild.name}] 동기화 중 예기치 않은 오류: {e}")

    log.info(f"🎯 총 {len(_synced_guilds)}개 서버에 {total}개 커맨드 동기화 완료")


# ── 새 서버 참여 시 자동 동기화 ─────────────────────────────────────
@bot.event
async def on_guild_join(guild: discord.Guild):
    """봇이 새 서버에 초대되면 즉시 슬래시 커맨드 등록."""
    log.info(f"🎉 새 서버 참여: {guild.name} (ID: {guild.id})")
    try:
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        _synced_guilds.add(guild.id)
        log.info(f"✅ [{guild.name}] 초대 즉시 {len(synced)}개 커맨드 동기화")
    except discord.Forbidden:
        log.error(f"❌ [{guild.name}] 동기화 실패 — applications.commands 스코프 누락")
    except Exception as e:
        log.exception(f"❌ [{guild.name}] 초대 동기화 오류: {e}")


@bot.event
async def on_guild_remove(guild: discord.Guild):
    """봇이 서버에서 제거될 때 sync 캐시에서 삭제."""
    _synced_guilds.discard(guild.id)
    log.info(f"👋 서버 탈퇴: {guild.name} (ID: {guild.id})")


@bot.event
async def on_error(event, *args, **kwargs):
    import traceback
    from utils.self_monitor import record_error

    tb_str = traceback.format_exc()
    log.exception(f"이벤트 오류: {event}")
    record_error(
        category=f"event:{event}",
        message=str(args[0]) if args else event,
        traceback_str=tb_str,
    )


# ── 스케줄러 ─────────────────────────────────────────────────────────
def _start_money_scheduler(bot_instance):
    """APScheduler로 인쵸 자동화."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from modules.money import check_thresholds, monthly_settlement

    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    # 15분마다 임계치 체크
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

    # 매주 월요일 9시 분쵸 경쟁분석
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


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise ValueError(".env에 DISCORD_TOKEN이 없습니다")

    await setup_commands(bot)

    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())

def _start_fixed_costs_scheduler(bot_instance):
    """매일 09:00 고정비 납부 알림."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from modules.fixed_costs import check_upcoming_payments

    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        check_upcoming_payments, CronTrigger(hour=9, minute=0),
        args=[bot_instance],
        id="fixed_costs_alert", replace_existing=True,
    )
    scheduler.start()

def _start_rnd_scheduler(bot_instance):
    """개쵸 R&D 자동화 스케줄러."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    from modules.rnd import daily_health_report
    from utils.self_monitor import check_error_thresholds, reset_counters

    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    # 매일 08:00 건강 체크 공지
    scheduler.add_job(
        daily_health_report, CronTrigger(hour=8, minute=0),
        args=[bot_instance],
        id="daily_health", replace_existing=True,
    )

    # 10분마다 에러 임계치 체크
    scheduler.add_job(
        check_error_thresholds, IntervalTrigger(minutes=10),
        args=[bot_instance],
        id="error_threshold_check", replace_existing=True,
    )

    # 자정마다 에러 카운터 리셋
    scheduler.add_job(
        reset_counters, CronTrigger(hour=0, minute=0),
        id="error_counter_reset", replace_existing=True,
    )

    scheduler.start()