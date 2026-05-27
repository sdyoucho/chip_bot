"""
utils/restart_manager.py
봇 재부팅 관리 — Graceful Shutdown + 스케줄 기반 자동 재부팅.

핵심:
- sys.exit(1)로 Railway 비정상 종료 → 자동 재시작
- 진행 중 task 완료 대기 (graceful)
- 재부팅 사유 + 시간 저장 → 재시작 후 알림
- 재부팅 시간 변경 가능 (/restart_schedule)
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)

# 봇 시작 시각
_START_TIME = datetime.now()

# 재부팅 상태 저장 파일 (재시작 후 알림용)
_RESTART_STATE_FILE = Path("/tmp/cho_bot_restart_state.json")

# 자동 재부팅 스케줄 (환경변수에서 변경 가능)
DEFAULT_RESTART_HOUR = int(os.getenv("AUTO_RESTART_HOUR", "4"))
DEFAULT_RESTART_MINUTE = int(os.getenv("AUTO_RESTART_MINUTE", "0"))

# 전역 scheduler 참조 (재스케줄 시 사용)
_global_scheduler: Optional[AsyncIOScheduler] = None
_bot_ref = None


# ═══════════════════════════════════════════════════════════════════
# 시간 관리
# ═══════════════════════════════════════════════════════════════════

def get_start_time() -> datetime:
    """봇 시작 시각."""
    return _START_TIME


def get_uptime() -> str:
    """가동 시간 (사람이 읽기 좋게)."""
    delta = datetime.now() - _START_TIME
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


# ═══════════════════════════════════════════════════════════════════
# 재부팅 상태 저장/복원
# ═══════════════════════════════════════════════════════════════════

def save_restart_state(
    reason: str,
    requested_by: Optional[str] = None,
    is_auto: bool = False,
) -> None:
    """재부팅 직전 상태를 파일에 저장 (재시작 후 알림용)."""
    try:
        state = {
            "reason": reason,
            "requested_by": requested_by or "system",
            "is_auto": is_auto,
            "timestamp": datetime.now().isoformat(),
        }
        _RESTART_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))
        log.info(f"재부팅 상태 저장: {reason}")
    except Exception as e:
        log.warning(f"재부팅 상태 저장 실패: {e}")


def load_restart_state() -> Optional[dict]:
    """재시작 후 이전 재부팅 상태 로드. 로드 후 파일 삭제."""
    try:
        if not _RESTART_STATE_FILE.exists():
            return None
        state = json.loads(_RESTART_STATE_FILE.read_text())
        _RESTART_STATE_FILE.unlink()  # 일회용
        return state
    except Exception as e:
        log.warning(f"재부팅 상태 로드 실패: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════
# Graceful Shutdown
# ═══════════════════════════════════════════════════════════════════

async def graceful_shutdown(
    bot,
    *,
    timeout: float = 30.0,
) -> None:
    """
    진행 중인 task가 완료되도록 기다린 후 봇 종료.

    Args:
        bot: discord.py Bot 인스턴스
        timeout: 최대 대기 시간 (초)
    """
    log.info("Graceful shutdown 시작 — 진행 중 task 완료 대기")

    # 1) 진행 중 task 수집 (현재 task 제외)
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

    # 2) 봇 연결 종료
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
    """
    봇 재부팅 요청. delay_seconds 후 graceful shutdown → sys.exit(1).

    Railway는 exit code != 0 일 때 자동 재시작합니다.
    """
    log.warning(f"재부팅 요청: {reason} (지연: {delay_seconds}초)")

    # 1) 상태 저장
    save_restart_state(reason=reason, requested_by=requested_by, is_auto=is_auto)

    # 2) 지연
    await asyncio.sleep(delay_seconds)

    # 3) Graceful shutdown
    try:
        await graceful_shutdown(bot, timeout=30.0)
    except Exception as e:
        log.error(f"Graceful shutdown 실패: {e}")

    # 4) 강제 종료 (Railway 자동 재시작 트리거)
    log.warning("프로세스 종료 — Railway 재시작 대기")
    # os._exit(1)는 모든 finally 블록을 건너뛰고 즉시 종료
    # sys.exit(1)은 정상 종료 절차를 따르지만 일부 환경에서 안 멈출 수 있음
    os._exit(1)


# ═══════════════════════════════════════════════════════════════════
# 자동 재부팅 스케줄러
# ═══════════════════════════════════════════════════════════════════

def setup_auto_restart(bot, hour: int = None, minute: int = None) -> None:
    """
    매일 정해진 시각에 자동 재부팅 스케줄 등록.

    Args:
        bot: 봇 인스턴스
        hour: 재부팅 시각 (0~23). 미지정 시 환경변수 또는 기본값(4)
        minute: 분 (0~59)
    """
    global _global_scheduler, _bot_ref

    _bot_ref = bot
    hour = hour if hour is not None else DEFAULT_RESTART_HOUR
    minute = minute if minute is not None else DEFAULT_RESTART_MINUTE

    if _global_scheduler is None:
        _global_scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    async def _auto_reboot():
        await request_restart(
            bot,
            reason=f"자동 재부팅 ({hour:02d}:{minute:02d} 정기 점검)",
            delay_seconds=0,
            requested_by="auto_scheduler",
            is_auto=True,
        )

    # 기존 job 제거 후 새로 등록
    if _global_scheduler.get_job("auto_restart"):
        _global_scheduler.remove_job("auto_restart")

    _global_scheduler.add_job(
        _auto_reboot,
        CronTrigger(hour=hour, minute=minute),
        id="auto_restart",
        replace_existing=True,
    )

    if not _global_scheduler.running:
        _global_scheduler.start()

    log.info(f"자동 재부팅 스케줄: 매일 {hour:02d}:{minute:02d}")


def reschedule_auto_restart(hour: int, minute: int) -> dict:
    """
    자동 재부팅 시각 변경. 변경 결과 반환.

    Returns:
        {"success": bool, "message": str, "next_run": datetime | None}
    """
    if not (0 <= hour <= 23):
        return {"success": False, "message": "시간은 0~23 사이여야 합니다.", "next_run": None}
    if not (0 <= minute <= 59):
        return {"success": False, "message": "분은 0~59 사이여야 합니다.", "next_run": None}

    if _bot_ref is None:
        return {"success": False, "message": "봇이 초기화되지 않았습니다.", "next_run": None}

    setup_auto_restart(_bot_ref, hour=hour, minute=minute)

    # 다음 실행 시각 조회
    next_run = None
    try:
        job = _global_scheduler.get_job("auto_restart")
        if job:
            next_run = job.next_run_time
    except Exception:
        pass

    # config_manager에도 저장 (재부팅해도 유지)
    try:
        from utils.config_manager import set_key
        set_key("AUTO_RESTART_HOUR", str(hour))
        set_key("AUTO_RESTART_MINUTE", str(minute))
    except Exception:
        pass

    return {
        "success": True,
        "message": f"자동 재부팅 시각 변경: 매일 {hour:02d}:{minute:02d}",
        "next_run": next_run,
    }


def get_restart_schedule() -> dict:
    """현재 재부팅 스케줄 조회."""
    try:
        hour = int(os.getenv("AUTO_RESTART_HOUR", str(DEFAULT_RESTART_HOUR)))
        minute = int(os.getenv("AUTO_RESTART_MINUTE", str(DEFAULT_RESTART_MINUTE)))
    except (ValueError, TypeError):
        hour, minute = DEFAULT_RESTART_HOUR, DEFAULT_RESTART_MINUTE

    next_run = None
    if _global_scheduler is not None:
        try:
            job = _global_scheduler.get_job("auto_restart")
            if job:
                next_run = job.next_run_time
        except Exception:
            pass

    return {
        "hour": hour,
        "minute": minute,
        "next_run": next_run,
        "scheduler_running": _global_scheduler.running if _global_scheduler else False,
    }


# ═══════════════════════════════════════════════════════════════════
# 재시작 후 알림 (main.py의 on_ready에서 호출)
# ═══════════════════════════════════════════════════════════════════

async def send_restart_notification(bot) -> None:
    """
    재시작 후 R&D 채널 또는 Cho 본인에게 재부팅 완료 알림 전송.
    main.py의 on_ready에서 호출.
    """
    state = load_restart_state()
    if not state:
        return  # 첫 시작이거나 비정상 종료

    try:
        import discord
        embed = discord.Embed(
            title="🔄 봇 재부팅 완료",
            description=(
                f"**사유**: {state.get('reason', '미상')}\n"
                f"**요청자**: {state.get('requested_by', 'system')}\n"
                f"**유형**: {'자동' if state.get('is_auto') else '수동'}\n"
                f"**재부팅 시각**: {state.get('timestamp', '미상')}\n"
                f"**현재 시각**: {datetime.now():%Y-%m-%d %H:%M:%S}"
            ),
            color=0x06B6D4,
        )
        embed.set_footer(text=f"Uptime since: {get_start_time():%H:%M:%S}")

        # R&D 채널 우선
        from modules.rnd import post_to_rnd_channel
        ok = await post_to_rnd_channel(
            bot, category="maintenance",
            title="재부팅 완료",
            content=(
                f"사유: {state.get('reason', '미상')}\n"
                f"요청자: {state.get('requested_by', 'system')}"
            ),
        )

        # R&D 채널 실패 시 Cho에게 DM
        if not ok:
            cho_id_str = os.getenv("CHO_USER_ID", "").strip()
            if cho_id_str.isdigit():
                cho = await bot.fetch_user(int(cho_id_str))
                if cho:
                    await cho.send(embed=embed)
        log.info("재부팅 알림 전송 완료")
    except Exception as e:
        log.warning(f"재부팅 알림 전송 실패: {e}")