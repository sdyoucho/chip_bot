"""
utils/restart_manager.py
봇 재부팅 관리.

Railway에서는 프로세스를 sys.exit(1)로 종료하면
restartPolicyType = ON_FAILURE 설정에 의해 자동 재시작됨.

기능:
1. 수동 재부팅: /reboot 커맨드
2. 자동 재부팅: 매일 04:00 (메모리 정리용)
3. 상태 체크: /uptime 커맨드
"""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime

import discord

log = logging.getLogger(__name__)

_start_time = time.time()
_restart_flag = False


def get_uptime() -> str:
    """봇 가동 시간 (사람이 읽을 수 있는 형식)."""
    seconds = int(time.time() - _start_time)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days: parts.append(f"{days}일")
    if hours: parts.append(f"{hours}시간")
    if minutes: parts.append(f"{minutes}분")
    parts.append(f"{secs}초")
    return " ".join(parts)


def get_start_time() -> datetime:
    return datetime.fromtimestamp(_start_time)


async def request_restart(
    bot: discord.Client,
    reason: str = "수동 재부팅 요청",
    delay_seconds: int = 3,
) -> None:
    """
    재부팅 요청.
    1) Cho에게 DM 알림
    2) delay_seconds 후 sys.exit(1) → Railway가 자동 재시작
    """
    global _restart_flag
    if _restart_flag:
        log.warning("이미 재부팅 요청됨 — 중복 무시")
        return
    _restart_flag = True

    log.warning(f"🔄 재부팅 요청: {reason}")

    # Cho에게 알림
    cho_id = int(os.getenv("CHO_USER_ID", "0"))
    if cho_id:
        try:
            user = await bot.fetch_user(cho_id)
            embed = discord.Embed(
                title="🔄 봇 재부팅 중...",
                description=(
                    f"**사유**: {reason}\n"
                    f"**가동 시간**: {get_uptime()}\n"
                    f"**예상 복귀**: 약 30초 후\n\n"
                    "Railway가 자동으로 재시작합니다."
                ),
                color=0xF97316,
            )
            await user.send(embed=embed)
        except Exception as e:
            log.error(f"재부팅 알림 DM 실패: {e}")

    # 우아한 종료
    await asyncio.sleep(delay_seconds)
    log.info("프로세스 종료 → Railway가 재시작할 예정")
    await bot.close()
    sys.exit(1)  # ON_FAILURE 정책에 의해 Railway가 재시작


# ── 자동 재부팅 스케줄러 ───────────────────────────────────────────
def setup_auto_restart(bot: discord.Client, hour: int = 4, minute: int = 0):
    """매일 특정 시각에 자동 재부팅 (메모리 누수 방지)."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    async def _auto_reboot():
        await request_restart(bot, reason=f"정기 재부팅 (매일 {hour:02d}:{minute:02d})")

    scheduler.add_job(
        _auto_reboot, CronTrigger(hour=hour, minute=minute),
        id="auto_restart", replace_existing=True,
    )
    scheduler.start()
    log.info(f"자동 재부팅 스케줄 등록: 매일 {hour:02d}:{minute:02d}")