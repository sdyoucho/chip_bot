import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 한국 시간대 통일
# ═══════════════════════════════════════════════════════════════════
KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """현재 한국 시간 반환."""
    return datetime.now(KST)


def utc_to_kst(utc_dt: datetime) -> datetime:
    """UTC datetime → KST 변환."""
    if utc_dt.tzinfo is None:
        # naive datetime은 UTC로 간주
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(KST)


# ═══════════════════════════════════════════════════════════════════
# 시작 시각 (KST)
# ═══════════════════════════════════════════════════════════════════
_START_TIME_KST = now_kst()

# 재부팅 상태 저장 파일
_RESTART_STATE_FILE = Path("/tmp/cho_bot_restart_state.json")

# 자동 재부팅 스케줄 (KST 기준)
DEFAULT_RESTART_HOUR = int(os.getenv("AUTO_RESTART_HOUR", "4"))
DEFAULT_RESTART_MINUTE = int(os.getenv("AUTO_RESTART_MINUTE", "0"))

# 전역 scheduler
_global_scheduler: Optional[AsyncIOScheduler] = None
_bot_ref = None


# ═══════════════════════════════════════════════════════════════════
# 시간 관리
# ═══════════════════════════════════════════════════════════════════

def get_start_time() -> datetime:
    """봇 시작 시각 (KST)."""
    return _START_TIME_KST


def get_uptime() -> str:
    """가동 시간 (사람이 읽기 좋게)."""
    delta = now_kst() - _START_TIME_KST
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(rem, 60)

    parts = []
    if days > 0:
        parts.append(f"{days}일")
    if hours > 0:
        parts.append(f"{hours}시간")
    if minutes > 0:
        parts.append(f"{minutes}분")
    parts.append(f"{seconds}초")
    return " ".join(parts)


def format_kst(dt: datetime, with_seconds: bool = True) -> str:
    """datetime을 KST 형식 문자열로."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc).astimezone(KST)
    elif dt.tzinfo != KST:
        dt = dt.astimezone(KST)

    fmt = "%Y-%m-%d %H:%M:%S KST" if with_seconds else "%Y-%m-%d %H:%M KST"
    return dt.strftime(fmt)


# ═══════════════════════════════════════════════════════════════════
# 재부팅 상태 저장/복원 (KST 사용)
# ═══════════════════════════════════════════════════════════════════

def save_restart_state(
    reason: str,
    requested_by: Optional[str] = None,
    is_auto: bool = False,
) -> None:
    """재부팅 직전 상태를 파일에 저장."""
    try:
        state = {
            "reason": reason,
            "requested_by": requested_by or "system",
            "is_auto": is_auto,
            "timestamp_kst": now_kst().isoformat(),
        }
        _RESTART_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))
        log.info(f"재부팅 상태 저장: {reason} ({format_kst(now_kst())})")
    except Exception as e:
        log.warning(f"재부팅 상태 저장 실패: {e}")


def load_restart_state() -> Optional[dict]:
    """재시작 후 이전 재부팅 상태 로드."""
    try:
        if not _RESTART_STATE_FILE.exists():
            return None
        state = json.loads(_RESTART_STATE_FILE.read_text())
        _RESTART_STATE_FILE.unlink()
        return state
    except Exception as e:
        log.warning(f"재부팅 상태 로드 실패: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════
# Graceful Shutdown
# ═══════════════════════════════════════════════════════════════════

async def graceful_shutdown(bot, *, timeout: float = 30.0) -> None:
    """진행 중인 task가 완료되도록 기다린 후 봇 종료."""
    log.info(f"Graceful shutdown 시작 ({format_kst(now_kst())})")

    current = asyncio.current_task()
    pending = [
        t for t in asyncio.all_tasks()
        if t is not current and not t.done()
    ]

    if pending:
        log.info(f"대기 중인 task: {len(pending)}개 (최대 {timeout}초)")
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=timeout,
            )
            log.info("모든 task 완료")
        except asyncio.TimeoutError:
            log.warning(f"Timeout — 남은 {len(pending)}개 task 강제 취소")
            for t in pending:
                t.cancel()

    try:
        await bot.close()
        log.info("Discord 연결 종료")
    except Exception as e:
        log.warning(f"봇 종료 중 오류: {e}")


# ═══════════════════════════════════════════════════════════════════
# 재부팅 요청 (수동)
# ═══════════════════════════════════════════════════════════════════

async def request_restart(
    bot,
    *,
    reason: str = "수동 재부팅",
    delay_seconds: int = 5,
    requested_by: Optional[str] = None,
    is_auto: bool = False,
) -> None:
    """봇 재부팅 요청."""
    log.warning(
        f"재부팅 요청: {reason} (지연: {delay_seconds}초) "
        f"@ {format_kst(now_kst())}"
    )

    save_restart_state(reason=reason, requested_by=requested_by, is_auto=is_auto)

    await asyncio.sleep(delay_seconds)

    try:
        await graceful_shutdown(bot, timeout=30.0)
    except Exception as e:
        log.error(f"Graceful shutdown 실패: {e}")

    log.warning(f"프로세스 종료 ({format_kst(now_kst())}) — Railway 재시작 대기")
    os._exit(1)


# ═══════════════════════════════════════════════════════════════════
# 자동 재부팅 스케줄러 (KST 기준)
# ═══════════════════════════════════════════════════════════════════

def setup_auto_restart(bot, hour: int = None, minute: int = None) -> None:
    """
    매일 정해진 KST 시각에 자동 재부팅 스케줄 등록.

    Args:
        bot: 봇 인스턴스
        hour: 재부팅 시각 (0~23 KST). 미지정 시 환경변수 또는 기본값(4)
        minute: 분 (0~59)
    """
    global _global_scheduler, _bot_ref

    _bot_ref = bot
    hour = hour if hour is not None else DEFAULT_RESTART_HOUR
    minute = minute if minute is not None else DEFAULT_RESTART_MINUTE

    # 🌏 timezone을 KST 객체로 명시
    if _global_scheduler is None:
        _global_scheduler = AsyncIOScheduler(timezone=KST)

    async def _auto_reboot():
        await request_restart(
            bot,
            reason=f"자동 재부팅 ({hour:02d}:{minute:02d} KST 정기 점검)",
            delay_seconds=0,
            requested_by="auto_scheduler",
            is_auto=True,
        )

    if _global_scheduler.get_job("auto_restart"):
        _global_scheduler.remove_job("auto_restart")

    # 🌏 CronTrigger도 KST 명시
    _global_scheduler.add_job(
        _auto_reboot,
        CronTrigger(hour=hour, minute=minute, timezone=KST),
        id="auto_restart",
        replace_existing=True,
    )

    if not _global_scheduler.running:
        _global_scheduler.start()

    next_run = _global_scheduler.get_job("auto_restart").next_run_time
    log.info(
        f"자동 재부팅 스케줄: 매일 {hour:02d}:{minute:02d} KST "
        f"(다음 실행: {format_kst(next_run)})"
    )


def reschedule_auto_restart(hour: int, minute: int) -> dict:
    """자동 재부팅 시각 변경."""
    if not (0 <= hour <= 23):
        return {"success": False, "message": "시간은 0~23 사이여야 합니다.", "next_run": None}
    if not (0 <= minute <= 59):
        return {"success": False, "message": "분은 0~59 사이여야 합니다.", "next_run": None}

    if _bot_ref is None:
        return {"success": False, "message": "봇이 초기화되지 않았습니다.", "next_run": None}

    setup_auto_restart(_bot_ref, hour=hour, minute=minute)

    next_run = None
    try:
        job = _global_scheduler.get_job("auto_restart")
        if job:
            next_run = job.next_run_time
    except Exception:
        pass

    try:
        from utils.config_manager import set_key
        set_key("AUTO_RESTART_HOUR", str(hour))
        set_key("AUTO_RESTART_MINUTE", str(minute))
    except Exception:
        pass

    return {
        "success": True,
        "message": f"자동 재부팅 시각 변경: 매일 {hour:02d}:{minute:02d} KST",
        "next_run": next_run,   # KST 객체
    }


def get_restart_schedule() -> dict:
    """현재 재부팅 스케줄 조회 (KST 기준)."""
    try:
        hour = int(os.getenv("AUTO_RESTART_HOUR", str(DEFAULT_RESTART_HOUR)))
        minute = int(os.getenv("AUTO_RESTART_MINUTE", str(DEFAULT_RESTART_MINUTE)))
    except (ValueError, TypeError):
        hour, minute = DEFAULT_RESTART_HOUR, DEFAULT_RESTART_MINUTE

    next_run = None
    if _global_scheduler is not None:
        try:
            job = _global_scheduler.get_job("auto_restart")
            if job and job.next_run_time:
                # next_run_time이 KST timezone이면 그대로, 아니면 변환
                if job.next_run_time.tzinfo != KST:
                    next_run = job.next_run_time.astimezone(KST)
                else:
                    next_run = job.next_run_time
        except Exception:
            pass

    return {
        "hour": hour,
        "minute": minute,
        "next_run": next_run,
        "scheduler_running": _global_scheduler.running if _global_scheduler else False,
        "timezone": "Asia/Seoul (KST, UTC+9)",
        "current_kst": now_kst(),
    }


# ═══════════════════════════════════════════════════════════════════
# 재시작 후 알림
# ═══════════════════════════════════════════════════════════════════

async def send_restart_notification(bot) -> None:
    """재시작 후 R&D 채널 또는 Cho에게 재부팅 완료 알림 전송."""
    state = load_restart_state()
    if not state:
        return

    try:
        import discord

        timestamp_text = state.get("timestamp_kst") or state.get("timestamp", "미상")
        embed = discord.Embed(
            title="🔄 봇 재부팅 완료",
            description=(
                f"**사유**: {state.get('reason', '미상')}\n"
                f"**요청자**: {state.get('requested_by', 'system')}\n"
                f"**유형**: {'자동' if state.get('is_auto') else '수동'}\n"
                f"**재부팅 시각**: {timestamp_text}\n"
                f"**현재 시각**: {format_kst(now_kst())}"
            ),
            color=0x06B6D4,
        )
        embed.set_footer(text=f"가동 시작: {format_kst(_START_TIME_KST)}")

        from modules.rnd import post_to_rnd_channel
        ok = await post_to_rnd_channel(
            bot, category="maintenance",
            title="재부팅 완료",
            content=(
                f"사유: {state.get('reason', '미상')}\n"
                f"요청자: {state.get('requested_by', 'system')}"
            ),
        )

        if not ok:
            cho_id_str = os.getenv("CHO_USER_ID", "").strip()
            if cho_id_str.isdigit():
                cho = await bot.fetch_user(int(cho_id_str))
                if cho:
                    await cho.send(embed=embed)
        log.info(f"재부팅 알림 전송 완료 ({format_kst(now_kst())})")
    except Exception as e:
        log.warning(f"재부팅 알림 전송 실패: {e}")
```

</details>

## ✅ `/uptime`, `/restart_schedule` 커맨드 KST 표시 보강

<details open>
<summary><b>📋 bot/commands.py — 관련 커맨드 교체</b></summary>

기존 `cmd_uptime`과 `cmd_restart_schedule`을 다음으로 교체:

```python
    @bot.tree.command(name="uptime", description="봇 가동 시간 (KST 기준)")
    @is_cho()
    async def cmd_uptime(interaction: discord.Interaction):
        from utils.restart_manager import (
            get_uptime, get_start_time, now_kst, format_kst,
        )
        embed = discord.Embed(title="⏱️ 봇 가동 현황", color=0x4F46E5)
        embed.add_field(name="가동 시간", value=get_uptime(), inline=False)
        embed.add_field(
            name="시작 시각 (KST)",
            value=format_kst(get_start_time()),
            inline=False,
        )
        embed.add_field(
            name="현재 시각 (KST)",
            value=format_kst(now_kst()),
            inline=False,
        )
        embed.add_field(name="서버 수", value=f"{len(bot.guilds)}개", inline=True)
        embed.set_footer(text="모든 시각은 한국 표준시(KST, UTC+9) 기준")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="restart_schedule", description="자동 재부팅 시각 변경 (KST)")
    @is_cho()
    @app_commands.describe(
        hour="시 (0~23 KST, 비우면 현재 설정 조회)",
        minute="분 (0~59, 기본 0)",
    )
    async def cmd_restart_schedule(
        interaction: discord.Interaction,
        hour: int | None = None,
        minute: int = 0,
    ):
        from utils.restart_manager import (
            reschedule_auto_restart, get_restart_schedule, format_kst,
        )

        if hour is None:
            schedule = get_restart_schedule()
            embed = discord.Embed(
                title="⏰ 자동 재부팅 스케줄 (KST)",
                color=0x4F46E5,
            )
            embed.add_field(
                name="📅 현재 설정",
                value=f"매일 **{schedule['hour']:02d}:{schedule['minute']:02d} KST**",
                inline=False,
            )
            if schedule["next_run"]:
                embed.add_field(
                    name="⏭️ 다음 실행",
                    value=format_kst(schedule["next_run"]),
                    inline=False,
                )
            embed.add_field(
                name="🌏 시간대",
                value=schedule.get("timezone", "Asia/Seoul"),
                inline=True,
            )
            embed.add_field(
                name="🔧 스케줄러",
                value="✅ 동작 중" if schedule["scheduler_running"] else "❌ 정지",
                inline=True,
            )
            embed.add_field(
                name="🕐 현재 KST",
                value=format_kst(schedule["current_kst"]),
                inline=False,
            )
            embed.set_footer(text="변경: /restart_schedule hour:N minute:N")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        result = reschedule_auto_restart(hour, minute)

        if result["success"]:
            embed = discord.Embed(
                title="✅ 재부팅 스케줄 변경",
                description=result["message"],
                color=0x059669,
            )
            if result["next_run"]:
                embed.add_field(
                    name="⏭️ 다음 실행 (KST)",
                    value=format_kst(result["next_run"]),
                    inline=False,
                )
        else:
            embed = discord.Embed(
                title="❌ 변경 실패",
                description=result["message"],
                color=0xE11D48,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)